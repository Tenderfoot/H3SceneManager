import json
import os
import re
import threading
import time
from flask import Flask, request, jsonify, send_from_directory, send_file

from engine.models import Character, Location, Sequence, Scene, Beat, CharacterCasting, new_id
from engine.storage import JsonStore
from engine.template_engine import generate_sequence_workflow, TemplateEngineError
from engine.prompt_compiler import DELIVERY_PRESETS, STYLE_PRESETS
from engine.comfy_client import ComfyClient, ComfyClientError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TEMPLATE_PATH = os.path.join(DATA_DIR, "templates", "Grant_Template_Workflow.json")
OUTPUT_DIR = os.path.join(DATA_DIR, "generated_workflows")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------- ComfyUI connection config: persisted to data/config.json,
# editable at runtime from the Config tab (no restart needed). Env vars
# only seed the file's initial defaults the very first time it's created --
# after that, the file on disk is the source of truth. ----------
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DEFAULT_CONFIG = {
    "comfyui_url": os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"),
    # Must point at the SAME directory ComfyUI itself writes rendered output
    # to -- Scene Forge needs to read it directly to confirm a render landed
    # and record its real path for chaining into the next sequence.
    "comfyui_output_dir": os.environ.get("COMFYUI_OUTPUT_DIR", os.path.expanduser("~/ComfyUI/output")),
    # ComfyUI's input/ directory -- where reference images/audio (character
    # faces, voice clips, location photos) typically live so LoadImage/
    # LoadAudio nodes can find them. Scene Forge doesn't read/write this
    # directory itself; it's just where you'd browse from when picking a
    # reference file path for a character or location.
    "comfyui_input_dir": os.environ.get("COMFYUI_INPUT_DIR", os.path.expanduser("~/ComfyUI/input")),
    "poll_interval": float(os.environ.get("COMFYUI_POLL_INTERVAL", "3")),
    "timeout_seconds": float(os.environ.get("COMFYUI_TIMEOUT_SECONDS", "1800")),
}


def _load_config_from_disk():
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                on_disk = json.load(f)
            return {**DEFAULT_CONFIG, **{k: v for k, v in on_disk.items() if k in DEFAULT_CONFIG}}
        except (json.JSONDecodeError, OSError):
            pass  # corrupt/unreadable file -- fall back to defaults rather than crash on startup
    return dict(DEFAULT_CONFIG)


# In-memory cache, single source of truth while the process is running --
# read by the config endpoints AND the background scene-run job, so it's
# guarded by a lock the same way SCENE_RUNS is below.
CONFIG = _load_config_from_disk()
CONFIG_LOCK = threading.Lock()


def get_config():
    with CONFIG_LOCK:
        return dict(CONFIG)


def save_config(updates):
    with CONFIG_LOCK:
        CONFIG.update(updates)
        with open(CONFIG_PATH, "w") as f:
            json.dump(CONFIG, f, indent=2)
        return dict(CONFIG)


# In-memory job tracking for scene run-throughs (see "scene run" section
# near the bottom). Single-process/single-user local app, so a plain dict
# behind a lock is enough -- no need for a job queue or persistence.
SCENE_RUNS = {}
SCENE_RUNS_LOCK = threading.Lock()

app = Flask(__name__, static_folder="static", static_url_path="")

# One-time migration: earlier versions stored this data under data/settings/
# (back when "Location" was called "Setting"). If that folder still exists
# and the new one doesn't, just rename it in place so nothing on disk from
# before this rename gets silently orphaned.
LOCATIONS_DIR = os.path.join(DATA_DIR, "locations")
_legacy_settings_dir = os.path.join(DATA_DIR, "settings")
if os.path.isdir(_legacy_settings_dir) and not os.path.isdir(LOCATIONS_DIR):
    os.rename(_legacy_settings_dir, LOCATIONS_DIR)

characters = JsonStore(os.path.join(DATA_DIR, "characters"), Character)
locations = JsonStore(LOCATIONS_DIR, Location)
scenes = JsonStore(os.path.join(DATA_DIR, "scenes"), Scene)


def load_template():
    with open(TEMPLATE_PATH) as f:
        return json.load(f)


# ---------- filesystem-safe naming for generated workflow output ----------
_ORDINAL_WORDS = [
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty",
]


def _sequence_word(index):
    """0-based sequence index -> spelled-out ordinal ('one', 'two', ...),
    falling back to a plain number past the word list."""
    if 0 <= index < len(_ORDINAL_WORDS):
        return _ORDINAL_WORDS[index]
    return str(index + 1)


def slugify(name):
    """Lowercase, filesystem-safe slug for a scene/character/location name.
    Falls back to 'untitled' if nothing usable is left after stripping."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return slug or "untitled"


def scene_output_dir(scene):
    """Directory (under OUTPUT_DIR) for this scene's generated workflows,
    named after the scene rather than its id. If another scene already
    claimed the same slug, disambiguate with a short id suffix so the two
    scenes' outputs never collide or overwrite one another."""
    base_slug = slugify(scene.name)
    marker_name = ".scene_id"

    def _claim(slug):
        path = os.path.join(OUTPUT_DIR, slug)
        marker_path = os.path.join(path, marker_name)
        if os.path.isdir(path):
            if os.path.isfile(marker_path):
                with open(marker_path) as f:
                    owner_id = f.read().strip()
                if owner_id and owner_id != scene.id:
                    return None  # slug is taken by a different scene
            # existing folder with no marker (e.g. pre-existing/manual) or
            # already owned by this scene -- safe to reuse
        os.makedirs(path, exist_ok=True)
        if not os.path.isfile(marker_path):
            with open(marker_path, "w") as f:
                f.write(scene.id)
        return path

    claimed = _claim(base_slug)
    if claimed is not None:
        return claimed
    # collision with a different scene's slug -- disambiguate with the scene's
    # own id, which by construction can't collide with anything else
    return _claim(f"{base_slug}_{scene.id[:8]}")


def _sequence_workflow_path(scene, sequence):
    """Where a sequence's generated workflow JSON gets written on disk."""
    scene_out_dir = scene_output_dir(scene)
    scene_slug = os.path.basename(scene_out_dir)
    return os.path.join(scene_out_dir, f"{scene_slug}_sequence_{_sequence_word(sequence.index)}.json")


class SceneGenerationError(Exception):
    """User-facing error for problems found while assembling generation
    context for a scene (missing location/character, etc.) -- shared by the
    manual /generate endpoint and the background scene-run job."""


def _gather_scene_generation_context(scene):
    location = locations.load(scene.location_id)
    if not location:
        raise SceneGenerationError("scene has no valid location")

    scene_characters = []
    for casting in scene.character_castings:
        character = characters.load(casting.character_id)
        if character is None:
            raise SceneGenerationError(f"scene references a missing character '{casting.character_id}'")
        scene_characters.append(character)

    return location, scene_characters


# ---------- static frontend ----------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------- config (ComfyUI connection settings) ----------
@app.route("/api/config", methods=["GET"])
def get_config_route():
    return jsonify(get_config())


@app.route("/api/config", methods=["PUT"])
def update_config_route():
    data = request.json or {}
    updates = {}

    if "comfyui_url" in data:
        url = str(data["comfyui_url"]).strip()
        if not url:
            return jsonify({"error": "ComfyUI URL cannot be empty"}), 400
        updates["comfyui_url"] = url

    if "comfyui_output_dir" in data:
        path = str(data["comfyui_output_dir"]).strip()
        if not path:
            return jsonify({"error": "ComfyUI output directory cannot be empty"}), 400
        updates["comfyui_output_dir"] = path

    if "comfyui_input_dir" in data:
        path = str(data["comfyui_input_dir"]).strip()
        if not path:
            return jsonify({"error": "ComfyUI input directory cannot be empty"}), 400
        updates["comfyui_input_dir"] = path

    if "poll_interval" in data:
        try:
            val = float(data["poll_interval"])
        except (TypeError, ValueError):
            return jsonify({"error": "poll interval must be a number"}), 400
        if val <= 0:
            return jsonify({"error": "poll interval must be greater than 0"}), 400
        updates["poll_interval"] = val

    if "timeout_seconds" in data:
        try:
            val = float(data["timeout_seconds"])
        except (TypeError, ValueError):
            return jsonify({"error": "timeout must be a number"}), 400
        if val <= 0:
            return jsonify({"error": "timeout must be greater than 0"}), 400
        updates["timeout_seconds"] = val

    if not updates:
        return jsonify({"error": "no valid fields provided"}), 400

    return jsonify(save_config(updates))


# ---------- media: serve local reference images/audio by path ----------
# Character/Location reference fields (face_image, reference_image,
# voice_audio) are typically just a bare filename -- the same convention
# ComfyUI's own LoadImage/LoadAudio nodes use, where a plain filename means
# "relative to ComfyUI's input/ directory", not relative to Scene Forge.
# A full absolute path still works too, for anyone who typed one. Browsers
# can't load either directly in <img>/<audio> tags, so this reads the file
# off disk and serves it over HTTP for the frontend to display.
@app.route("/api/media")
def serve_media():
    path = request.args.get("path", "")
    if not path:
        return jsonify({"error": "path is required"}), 400
    if not os.path.isabs(path):
        path = os.path.join(get_config()["comfyui_input_dir"], path)
    if not os.path.isfile(path):
        return jsonify({"error": "file not found"}), 404
    return send_file(path)


# ---------- characters ----------
@app.route("/api/characters", methods=["GET"])
def list_characters():
    return jsonify([c.to_dict() for c in characters.list_all()])


@app.route("/api/characters", methods=["POST"])
def create_character():
    data = request.json
    c = Character.create(
        name=data["name"],
        face_image=data.get("face_image", ""),
        voice_audio=data.get("voice_audio", ""),
        category=data.get("category", ""),
        appearance_description=data.get("appearance_description", ""),
        properties=data.get("properties", {}),
    )
    characters.save(c)
    return jsonify(c.to_dict()), 201


@app.route("/api/characters/<cid>", methods=["PUT"])
def update_character(cid):
    existing = characters.load(cid)
    if not existing:
        return jsonify({"error": "not found"}), 404
    data = request.json
    updated = Character(
        id=cid, name=data.get("name", existing.name),
        face_image=data.get("face_image", existing.face_image),
        voice_audio=data.get("voice_audio", existing.voice_audio),
        category=data.get("category", existing.category),
        appearance_description=data.get("appearance_description", existing.appearance_description),
        properties=data.get("properties", existing.properties),
    )
    characters.save(updated)
    return jsonify(updated.to_dict())


@app.route("/api/characters/<cid>", methods=["DELETE"])
def delete_character(cid):
    characters.delete(cid)
    return "", 204


# ---------- locations ----------
@app.route("/api/locations", methods=["GET"])
def list_locations():
    return jsonify([loc.to_dict() for loc in locations.list_all()])


@app.route("/api/locations", methods=["POST"])
def create_location():
    data = request.json
    loc = Location.create(
        name=data["name"],
        reference_image=data.get("reference_image", ""),
        category=data.get("category", ""),
        visual_description=data.get("visual_description", ""),
        soundscape_description=data.get("soundscape_description", ""),
        properties=data.get("properties", {}),
    )
    locations.save(loc)
    return jsonify(loc.to_dict()), 201


@app.route("/api/locations/<lid>", methods=["PUT"])
def update_location(lid):
    existing = locations.load(lid)
    if not existing:
        return jsonify({"error": "not found"}), 404
    data = request.json
    updated = Location(id=lid, name=data.get("name", existing.name),
                        reference_image=data.get("reference_image", existing.reference_image),
                        category=data.get("category", existing.category),
                        visual_description=data.get("visual_description", existing.visual_description),
                        soundscape_description=data.get("soundscape_description", existing.soundscape_description),
                        properties=data.get("properties", existing.properties))
    locations.save(updated)
    return jsonify(updated.to_dict())


@app.route("/api/locations/<lid>", methods=["DELETE"])
def delete_location(lid):
    locations.delete(lid)
    return "", 204


def _parse_castings(raw_list):
    return [
        CharacterCasting(character_id=item["character_id"],
                          include_voice=bool(item.get("include_voice", True)))
        for item in raw_list or [] if item.get("character_id")
    ]


# ---------- scenes ----------
@app.route("/api/scenes", methods=["GET"])
def list_scenes():
    return jsonify([s.to_dict() for s in scenes.list_all()])


@app.route("/api/scenes", methods=["POST"])
def create_scene():
    data = request.json
    style_preset, style_opening, err = _resolve_style(data)
    if err:
        return jsonify({"error": err}), 400
    scene = Scene.create(
        name=data["name"],
        location_id=data.get("location_id", ""),
        character_castings=_parse_castings(data.get("character_castings", [])),
        non_diegetic_music=data.get("non_diegetic_music", ""),
        summary_premise=data.get("summary_premise", ""),
        style_preset=style_preset,
        style_opening=style_opening,
    )
    scenes.save(scene)
    return jsonify(scene.to_dict()), 201


@app.route("/api/scenes/<scid>", methods=["GET"])
def get_scene(scid):
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "not found"}), 404
    return jsonify(scene.to_dict())


@app.route("/api/scenes/<scid>", methods=["PUT"])
def update_scene(scid):
    existing = scenes.load(scid)
    if not existing:
        return jsonify({"error": "not found"}), 404
    data = request.json
    style_preset, style_opening, err = _resolve_style(
        data, fallback_preset=existing.style_preset, fallback_opening=existing.style_opening)
    if err:
        return jsonify({"error": err}), 400
    existing.name = data.get("name", existing.name)
    existing.location_id = data.get("location_id", existing.location_id)
    if "character_castings" in data:
        existing.character_castings = _parse_castings(data["character_castings"])
    existing.non_diegetic_music = data.get("non_diegetic_music", existing.non_diegetic_music)
    existing.summary_premise = data.get("summary_premise", existing.summary_premise)
    existing.style_preset = style_preset
    existing.style_opening = style_opening
    scenes.save(existing)
    return jsonify(existing.to_dict())


@app.route("/api/scenes/<scid>", methods=["DELETE"])
def delete_scene(scid):
    scenes.delete(scid)
    return "", 204


# ---------- sequences (nested under a scene) ----------
@app.route("/api/scenes/<scid>/sequences", methods=["POST"])
def add_sequence(scid):
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "not found"}), 404
    data = request.json
    seq = Sequence(
        id=new_id("seq"),
        index=len(scene.sequences),
        duration=float(data.get("duration", 8.0)),
    )
    scene.sequences.append(seq)
    scenes.save(scene)
    return jsonify(seq.to_dict()), 201


@app.route("/api/scenes/<scid>/sequences/<seqid>", methods=["PUT"])
def update_sequence(scid, seqid):
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    data = request.json
    for seq in scene.sequences:
        if seq.id == seqid:
            seq.duration = float(data.get("duration", seq.duration))
            scenes.save(scene)
            return jsonify(seq.to_dict())
    return jsonify({"error": "sequence not found"}), 404


@app.route("/api/scenes/<scid>/sequences/<seqid>", methods=["DELETE"])
def delete_sequence(scid, seqid):
    """
    Deletes a sequence and re-indexes the remaining ones to stay contiguous
    (0..N-1). This matters: the "previous sequence" lookup during generation
    is a literal index-1 lookup, so a gap left by deleting a middle sequence
    would break chaining for everything after it. Re-indexing does NOT reset
    the status/output_video_path of sequences that shift position -- if a
    sequence after the deleted one was already generated/rendered assuming a
    different previous sequence, it may need regenerating.
    """
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    before = len(scene.sequences)
    scene.sequences = [s for s in scene.sequences if s.id != seqid]
    if len(scene.sequences) == before:
        return jsonify({"error": "sequence not found"}), 404
    scene.sequences.sort(key=lambda s: s.index)
    for i, seq in enumerate(scene.sequences):
        seq.index = i
    scenes.save(scene)
    return "", 204


# ---------- shared preset-resolution helper (delivery presets, style presets) ----------
@app.route("/api/delivery-options", methods=["GET"])
def list_delivery_options():
    return jsonify([{"key": k, "label": label} for k, (label, _phrase) in DELIVERY_PRESETS.items()])


@app.route("/api/style-options", methods=["GET"])
def list_style_options():
    return jsonify([{"key": k, "label": label} for k, (label, _phrase) in STYLE_PRESETS.items()])


def _resolve_preset(data, presets, preset_field, text_field, fallback_preset="", fallback_value=""):
    """
    Generic resolver for the "dropdown preset, or custom free text" pattern
    used by both beat delivery and scene style opening. Resolves the
    preset/text pair in `data` into the (preset_key, resolved_phrase) pair
    actually stored on the model. If `preset_field` is entirely absent from
    `data`, keeps the fallback values (used by PUT/update, where omitted
    fields should stay unchanged).
    Returns (preset_key, resolved_value, error_message_or_None).
    """
    if preset_field not in data:
        return fallback_preset, fallback_value, None
    preset = data.get(preset_field, "")
    if preset == "custom":
        return preset, data.get(text_field, "").strip(), None
    if preset:
        if preset not in presets:
            return None, None, f"unknown {preset_field} '{preset}'"
        return preset, presets[preset][1], None
    return "", "", None


def _resolve_delivery(data, fallback_preset="", fallback_delivery=""):
    return _resolve_preset(data, DELIVERY_PRESETS, "delivery_preset", "delivery_text",
                            fallback_preset, fallback_delivery)


def _resolve_style(data, fallback_preset="", fallback_opening=""):
    return _resolve_preset(data, STYLE_PRESETS, "style_preset", "style_text",
                            fallback_preset, fallback_opening)


def _normalize_shot_fields(is_new_shot, timestamp):
    """A beat may only carry a timestamp if it starts a new shot -- a beat
    that folds into the previous shot has no [Shot N] header of its own to
    attach one to, so its timestamp is always cleared."""
    return timestamp if is_new_shot else ""



# ---------- beats (nested under a sequence) ----------
@app.route("/api/scenes/<scid>/sequences/<seqid>/beats", methods=["POST"])
def add_beat(scid, seqid):
    """
    Body for an action beat:   {"kind": "action", "text": "..."}
    Body for a dialogue beat:  {"kind": "dialogue", "character_id": "...",
                                 "line": "...", "language": "English",
                                 "delivery_preset": "nervous" | "custom" | "",
                                 "delivery_text": "..."}   # only used when delivery_preset == "custom"
    """
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    data = request.json
    kind = data.get("kind")
    if kind not in ("action", "dialogue"):
        return jsonify({"error": "kind must be 'action' or 'dialogue'"}), 400
    scene_char_ids = {c.character_id for c in scene.character_castings}
    if kind == "dialogue" and data.get("character_id") not in scene_char_ids:
        return jsonify({"error": "character_id must be one of this scene's characters"}), 400

    delivery_preset, delivery, err = _resolve_delivery(data)
    if err:
        return jsonify({"error": err}), 400

    for seq in scene.sequences:
        if seq.id == seqid:
            is_first = len(seq.beats) == 0
            is_new_shot = True if is_first else bool(data.get("is_new_shot", True))
            beat = Beat.create(
                kind,
                text=data.get("text", ""),
                character_id=data.get("character_id", ""),
                line=data.get("line", ""),
                language=data.get("language", "English"),
                delivery_preset=delivery_preset,
                delivery=delivery,
                is_new_shot=is_new_shot,
                timestamp=_normalize_shot_fields(is_new_shot, data.get("timestamp", "")),
            )
            seq.beats.append(beat)
            scenes.save(scene)
            return jsonify(beat.to_dict()), 201
    return jsonify({"error": "sequence not found"}), 404


@app.route("/api/scenes/<scid>/sequences/<seqid>/beats/<beatid>", methods=["PUT"])
def update_beat(scid, seqid, beatid):
    """Same body shape as POST .../beats. Any field omitted keeps its current value."""
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    data = request.json
    scene_char_ids = {c.character_id for c in scene.character_castings}

    for seq in scene.sequences:
        if seq.id != seqid:
            continue
        for idx, beat in enumerate(seq.beats):
            if beat.id != beatid:
                continue
            kind = data.get("kind", beat.kind)
            if kind not in ("action", "dialogue"):
                return jsonify({"error": "kind must be 'action' or 'dialogue'"}), 400
            character_id = data.get("character_id", beat.character_id)
            if kind == "dialogue" and character_id not in scene_char_ids:
                return jsonify({"error": "character_id must be one of this scene's characters"}), 400

            delivery_preset, delivery, err = _resolve_delivery(data, fallback_preset=beat.delivery_preset,
                                                                 fallback_delivery=beat.delivery)
            if err:
                return jsonify({"error": err}), 400

            is_first = idx == 0
            is_new_shot = True if is_first else bool(data.get("is_new_shot", beat.is_new_shot))

            beat.kind = kind
            beat.text = data.get("text", beat.text)
            beat.character_id = character_id
            beat.line = data.get("line", beat.line)
            beat.language = data.get("language", beat.language)
            beat.delivery_preset = delivery_preset
            beat.delivery = delivery
            beat.is_new_shot = is_new_shot
            beat.timestamp = _normalize_shot_fields(is_new_shot, data.get("timestamp", beat.timestamp))
            scenes.save(scene)
            return jsonify(beat.to_dict())
        return jsonify({"error": "beat not found"}), 404
    return jsonify({"error": "sequence not found"}), 404


@app.route("/api/scenes/<scid>/sequences/<seqid>/beats/reorder", methods=["POST"])
def reorder_beats(scid, seqid):
    """Body: {"beat_ids": [...]} -- the full list of this sequence's beat ids
    in the desired new order. Must be exactly the same set of ids as before."""
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    data = request.json
    new_order = data.get("beat_ids", [])

    for seq in scene.sequences:
        if seq.id == seqid:
            current_ids = {b.id for b in seq.beats}
            if set(new_order) != current_ids or len(new_order) != len(seq.beats):
                return jsonify({"error": "beat_ids must contain exactly this sequence's current beats"}), 400
            by_id = {b.id: b for b in seq.beats}
            seq.beats = [by_id[bid] for bid in new_order]
            scenes.save(scene)
            return jsonify([b.to_dict() for b in seq.beats])
    return jsonify({"error": "sequence not found"}), 404


@app.route("/api/scenes/<scid>/sequences/<seqid>/beats/<beatid>", methods=["DELETE"])
def delete_beat(scid, seqid, beatid):
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    for seq in scene.sequences:
        if seq.id == seqid:
            before = len(seq.beats)
            seq.beats = [b for b in seq.beats if b.id != beatid]
            if len(seq.beats) == before:
                return jsonify({"error": "beat not found"}), 404
            scenes.save(scene)
            return "", 204
    return jsonify({"error": "sequence not found"}), 404


@app.route("/api/scenes/<scid>/sequences_bulk", methods=["PUT"])
def bulk_save_sequences(scid):
    """
    Replaces this scene's entire sequences (and their beats) list in one
    shot. This is the ONLY point where the sequence-editor tab's edits are
    actually persisted -- that tab stages all add/edit/delete/reorder
    actions in a local in-browser working copy and never calls the
    per-sequence/per-beat endpoints above individually; clicking "Save"
    sends the whole working copy here at once.

    Delivery presets are resolved server-side here (same as add_beat/
    update_beat), since the client only ever holds the preset key + custom
    text locally, not the resolved phrase, until save time.
    """
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    data = request.json
    raw_sequences = data.get("sequences", [])

    new_sequences = []
    for i, raw_seq in enumerate(raw_sequences):
        beats = []
        for bi, raw_beat in enumerate(raw_seq.get("beats", [])):
            delivery_preset, delivery, err = _resolve_delivery(raw_beat)
            if err:
                return jsonify({"error": err}), 400
            is_first = bi == 0
            is_new_shot = True if is_first else bool(raw_beat.get("is_new_shot", True))
            beats.append(Beat(
                id=raw_beat.get("id") or new_id("beat"),
                kind=raw_beat.get("kind", "action"),
                text=raw_beat.get("text", ""),
                character_id=raw_beat.get("character_id", ""),
                line=raw_beat.get("line", ""),
                language=raw_beat.get("language", "English"),
                delivery_preset=delivery_preset,
                delivery=delivery,
                is_new_shot=is_new_shot,
                timestamp=_normalize_shot_fields(is_new_shot, raw_beat.get("timestamp", "")),
            ))
        new_sequences.append(Sequence(
            id=raw_seq.get("id") or new_id("seq"),
            index=i,
            duration=float(raw_seq.get("duration", 8.0)),
            beats=beats,
            status=raw_seq.get("status", "pending"),
            output_video_path=raw_seq.get("output_video_path", ""),
        ))

    scene.sequences = new_sequences
    scenes.save(scene)
    return jsonify(scene.to_dict())


@app.route("/api/scenes/<scid>/sequences/<seqid>/resolve_output", methods=["POST"])
def resolve_output(scid, seqid):
    """
    Call this after you've actually run a sequence's workflow in ComfyUI and
    know the real output file path. This marks it rendered and makes that
    path available as {{oldfilename}} when generating the *next* sequence.
    """
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    data = request.json
    output_path = data.get("output_video_path", "")
    for seq in scene.sequences:
        if seq.id == seqid:
            seq.output_video_path = output_path
            seq.status = "rendered"
            scenes.save(scene)
            return jsonify(seq.to_dict())
    return jsonify({"error": "sequence not found"}), 404


def _resolve_prompt_format(data):
    """Reads prompt_format straight off the request body -- it's a per-call
    choice (from the scene editor's dropdown), not anything stored on the
    Scene or Sequence model. Defaults to "lean" if omitted."""
    value = data.get("prompt_format", "lean")
    if value not in ("lean", "full"):
        return None, f"prompt_format must be 'lean' or 'full', got {value!r}"
    return value, None


def _resolve_randomize_seed(data):
    """Reads randomize_seed straight off the request body -- same per-call,
    not-stored-anywhere pattern as prompt_format (see the scene editor's
    "Randomize seed" checkbox). Defaults to True: without a fresh seed each
    call, regenerating an unchanged sequence submits a byte-identical graph
    that ComfyUI's node cache can just skip re-executing."""
    return bool(data.get("randomize_seed", True))


# ---------- generation ----------
@app.route("/api/scenes/<scid>/sequences/<seqid>/generate", methods=["POST"])
def generate_sequence(scid, seqid):
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404

    data = request.json or {}
    prompt_format, err = _resolve_prompt_format(data)
    if err:
        return jsonify({"error": err}), 400
    randomize_seed = _resolve_randomize_seed(data)

    try:
        location, scene_characters = _gather_scene_generation_context(scene)
    except SceneGenerationError as e:
        return jsonify({"error": str(e)}), 400

    sequence = next((s for s in scene.sequences if s.id == seqid), None)
    if sequence is None:
        return jsonify({"error": "sequence not found"}), 404

    # Determine previous sequence's resolved output, if any
    previous_output_path = None
    if sequence.index > 0:
        prev_seq = next((s for s in scene.sequences if s.index == sequence.index - 1), None)
        if prev_seq is None:
            return jsonify({"error": f"no sequence found at index {sequence.index - 1}"}), 400
        if not prev_seq.output_video_path:
            return jsonify({
                "error": f"previous sequence (index {prev_seq.index}) has not been resolved yet. "
                         f"Call /resolve_output on it first."
            }), 400
        previous_output_path = prev_seq.output_video_path

    try:
        template = load_template()
        wf, prefix = generate_sequence_workflow(
            template,
            location=location,
            characters=scene_characters,
            sequence=sequence,
            scene=scene,
            previous_output_path=previous_output_path,
            prompt_format=prompt_format,
            randomize_seed=randomize_seed,
        )
    except TemplateEngineError as e:
        return jsonify({"error": str(e)}), 400

    out_path = _sequence_workflow_path(scene, sequence)
    with open(out_path, "w") as f:
        json.dump(wf, f, indent=2)

    sequence.status = "generated"
    scenes.save(scene)

    return jsonify({
        "workflow_path": out_path,
        "filename_prefix": prefix,
        "sequence": sequence.to_dict(),
    })


# ---------- run: submit a whole scene's sequences to ComfyUI in order ----------
def _new_run_job(scene):
    return {
        "state": "running",       # running | done | error | cancelled | none
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "cancel_requested": False,
        "output_folder": None,    # ComfyUI output subfolder this run is writing into,
                                    # resolved once _run_scene_job actually starts (see
                                    # _pick_run_output_folder) -- None until then
        "sequences": [
            {
                "id": s.id, "index": s.index,
                "state": "rendered" if (s.status == "rendered" and s.output_video_path) else "pending",
                "prompt_id": None,
                "output_video_path": s.output_video_path or None,
                "error": None,
                "progress": None,  # {"value": N, "max": M} sampler-step progress while
                                    # "rendering", from ComfyUI's WebSocket -- None if not
                                    # yet available (websocket-client not installed, the
                                    # connection failed, or no progress message has arrived
                                    # for this sequence yet)
            }
            for s in sorted(scene.sequences, key=lambda s: s.index)
        ],
    }


def _pick_run_output_folder(scene, ordered_sequences, comfyui_output_dir):
    """Decides which ComfyUI output subfolder (under COMFYUI_OUTPUT_DIR) an
    entire scene run should write into.

    - If any sequence already has a recorded output_video_path (this run is
      CONTINUING a partial previous run, not starting fresh -- e.g. you added
      a sequence and only it needs rendering), reuse that same folder for
      consistency. It's read directly off the first already-rendered
      sequence's actual path, so a run resuming mid-way through an earlier
      versioned folder (e.g. "..._2") keeps landing there rather than
      reverting to the unsuffixed base name.
    - Otherwise (a genuinely fresh run -- nothing rendered yet, e.g. right
      after a manual status reset), pick a folder name that doesn't already
      exist on disk: the scene's base slug if free, else "<slug>_2",
      "<slug>_3", etc. -- so re-running a scene from scratch never mixes a
      new attempt's files into an older attempt's folder.
    """
    for seq in ordered_sequences:
        if seq.status == "rendered" and seq.output_video_path:
            return os.path.basename(os.path.dirname(seq.output_video_path))

    base_slug = slugify(scene.name)
    candidate = base_slug
    n = 2
    while os.path.isdir(os.path.join(comfyui_output_dir, candidate)):
        candidate = f"{base_slug}_{n}"
        n += 1
    return candidate


def _run_scene_job(scid, prompt_format, randomize_seed):
    def _touch_job(mutate):
        with SCENE_RUNS_LOCK:
            job = SCENE_RUNS.get(scid)
            if job:
                mutate(job)

    def _touch_seq(seq_id, **fields):
        def _mut(job):
            for entry in job["sequences"]:
                if entry["id"] == seq_id:
                    entry.update(fields)
        _touch_job(_mut)

    def _should_cancel():
        with SCENE_RUNS_LOCK:
            job = SCENE_RUNS.get(scid)
            return bool(job and job.get("cancel_requested"))

    cfg = get_config()
    client = ComfyClient(cfg["comfyui_url"], cfg["comfyui_output_dir"],
                          poll_interval=cfg["poll_interval"], timeout_seconds=cfg["timeout_seconds"])
    cancelled = False

    try:
        scene = scenes.load(scid)
        if not scene:
            raise ComfyClientError("scene disappeared before the run could start")

        location, scene_characters = _gather_scene_generation_context(scene)
        ordered = sorted(scene.sequences, key=lambda s: s.index)
        run_output_folder = _pick_run_output_folder(scene, ordered, cfg["comfyui_output_dir"])
        _touch_job(lambda job: job.update(output_folder=run_output_folder))

        previous_output_path = None
        for sequence in ordered:
            if sequence.status == "rendered" and sequence.output_video_path:
                previous_output_path = sequence.output_video_path
                continue

            if _should_cancel():
                cancelled = True
                _touch_seq(sequence.id, state="cancelled")
                break

            _touch_seq(sequence.id, state="generating")
            template = load_template()
            wf, _prefix = generate_sequence_workflow(
                template, location=location, characters=scene_characters,
                sequence=sequence, scene=scene,
                previous_output_path=previous_output_path,
                prompt_format=prompt_format,
                randomize_seed=randomize_seed,
                output_prefix=f"{run_output_folder}/{run_output_folder}_{sequence.index + 1}",
            )
            out_path = _sequence_workflow_path(scene, sequence)
            with open(out_path, "w") as f:
                json.dump(wf, f, indent=2)
            sequence.status = "generated"
            scenes.save(scene)

            _touch_seq(sequence.id, state="converting")
            api_wf = client.convert_to_api_format(wf)

            _touch_seq(sequence.id, state="queued")
            prompt_id = client.queue_prompt(api_wf)
            _touch_seq(sequence.id, state="rendering", prompt_id=prompt_id, progress=None)

            history_entry = client.wait_for_completion(
                prompt_id, should_cancel=_should_cancel,
                on_progress=lambda value, max_, sid=sequence.id: _touch_seq(sid, progress={"value": value, "max": max_}),
            )
            output_path = client.find_output_file(history_entry)

            sequence.status = "rendered"
            sequence.output_video_path = output_path
            scenes.save(scene)
            _touch_seq(sequence.id, state="rendered", output_video_path=output_path, progress=None)

            previous_output_path = output_path

        if cancelled:
            _touch_job(lambda job: job.update(state="cancelled", finished_at=time.time()))
        else:
            _touch_job(lambda job: job.update(state="done", finished_at=time.time()))

    except SceneGenerationError as e:
        _touch_job(lambda job: job.update(state="error", error=str(e), finished_at=time.time()))
    except TemplateEngineError as e:
        _touch_job(lambda job: job.update(state="error", error=str(e), finished_at=time.time()))
    except ComfyClientError as e:
        if str(e) == "cancelled":
            _touch_job(lambda job: job.update(state="cancelled", finished_at=time.time()))
        else:
            client.interrupt()
            _touch_job(lambda job: job.update(state="error", error=str(e), finished_at=time.time()))
    except Exception as e:  # last-resort guard so a bug never leaves the job stuck "running"
        _touch_job(lambda job: job.update(state="error", error=f"unexpected error: {e}", finished_at=time.time()))
    finally:
        client.close()


@app.route("/api/scenes/<scid>/run", methods=["POST"])
def start_scene_run(scid):
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    if not scene.sequences:
        return jsonify({"error": "scene has no sequences to run"}), 400

    data = request.json or {}
    prompt_format, err = _resolve_prompt_format(data)
    if err:
        return jsonify({"error": err}), 400
    randomize_seed = _resolve_randomize_seed(data)

    with SCENE_RUNS_LOCK:
        existing = SCENE_RUNS.get(scid)
        if existing and existing["state"] == "running":
            return jsonify({"error": "a render run is already in progress for this scene"}), 409
        SCENE_RUNS[scid] = _new_run_job(scene)

    threading.Thread(target=_run_scene_job, args=(scid, prompt_format, randomize_seed), daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/scenes/<scid>/run/status", methods=["GET"])
def get_scene_run_status(scid):
    with SCENE_RUNS_LOCK:
        job = SCENE_RUNS.get(scid)
    return jsonify(job or {"state": "none"})


@app.route("/api/scenes/<scid>/run/cancel", methods=["POST"])
def cancel_scene_run(scid):
    with SCENE_RUNS_LOCK:
        job = SCENE_RUNS.get(scid)
        if not job or job["state"] != "running":
            return jsonify({"error": "no render run is currently in progress for this scene"}), 400
        job["cancel_requested"] = True
    return jsonify({"cancel_requested": True})


if __name__ == "__main__":
    app.run(debug=True, port=5151)
