import json
import os
from flask import Flask, request, jsonify, send_from_directory

from engine.models import Character, Setting, Sequence, Scene, Beat, AttireOption, CharacterCasting, new_id
from engine.storage import JsonStore
from engine.template_engine import generate_sequence_workflow, TemplateEngineError
from engine.prompt_compiler import DELIVERY_PRESETS, STYLE_PRESETS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TEMPLATE_PATH = os.path.join(DATA_DIR, "templates", "Grant_Template_Workflow.json")
OUTPUT_DIR = os.path.join(DATA_DIR, "generated_workflows")
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="")

characters = JsonStore(os.path.join(DATA_DIR, "characters"), Character)
settings = JsonStore(os.path.join(DATA_DIR, "settings"), Setting)
scenes = JsonStore(os.path.join(DATA_DIR, "scenes"), Scene)


def load_template():
    with open(TEMPLATE_PATH) as f:
        return json.load(f)


# ---------- static frontend ----------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def _parse_attire_options(raw_list):
    """
    Build AttireOption objects from JSON body entries. Entries that already
    carry an 'id' (i.e. an existing option being edited) keep that id, so
    scenes that reference it by attire_id don't get silently orphaned by an
    edit. New entries (no 'id') get a fresh one. Exactly one ends up marked
    default -- the first, if none was explicitly marked.
    """
    options = []
    for item in raw_list or []:
        kwargs = dict(
            label=item.get("label", ""),
            image_path=item.get("image_path", ""),
            description=item.get("description", ""),
            is_default=bool(item.get("is_default", False)),
        )
        if item.get("id"):
            options.append(AttireOption(id=item["id"], **kwargs))
        else:
            options.append(AttireOption.create(**kwargs))
    if options and not any(o.is_default for o in options):
        options[0].is_default = True
    return options


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
        attire_options=_parse_attire_options(data.get("attire_options", [])),
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
        attire_options=(_parse_attire_options(data["attire_options"])
                         if "attire_options" in data else existing.attire_options),
        appearance_description=data.get("appearance_description", existing.appearance_description),
        properties=data.get("properties", existing.properties),
    )
    characters.save(updated)
    return jsonify(updated.to_dict())


@app.route("/api/characters/<cid>", methods=["DELETE"])
def delete_character(cid):
    characters.delete(cid)
    return "", 204


# ---------- settings ----------
@app.route("/api/settings", methods=["GET"])
def list_settings():
    return jsonify([s.to_dict() for s in settings.list_all()])


@app.route("/api/settings", methods=["POST"])
def create_setting():
    data = request.json
    s = Setting.create(
        name=data["name"],
        reference_image=data.get("reference_image", ""),
        ambient_audio=data.get("ambient_audio", ""),
        visual_description=data.get("visual_description", ""),
        soundscape_description=data.get("soundscape_description", ""),
        properties=data.get("properties", {}),
    )
    settings.save(s)
    return jsonify(s.to_dict()), 201


@app.route("/api/settings/<sid>", methods=["PUT"])
def update_setting(sid):
    existing = settings.load(sid)
    if not existing:
        return jsonify({"error": "not found"}), 404
    data = request.json
    updated = Setting(id=sid, name=data.get("name", existing.name),
                       reference_image=data.get("reference_image", existing.reference_image),
                       ambient_audio=data.get("ambient_audio", existing.ambient_audio),
                       visual_description=data.get("visual_description", existing.visual_description),
                       soundscape_description=data.get("soundscape_description", existing.soundscape_description),
                       properties=data.get("properties", existing.properties))
    settings.save(updated)
    return jsonify(updated.to_dict())


@app.route("/api/settings/<sid>", methods=["DELETE"])
def delete_setting(sid):
    settings.delete(sid)
    return "", 204


def _parse_castings(raw_list):
    return [
        CharacterCasting(character_id=item["character_id"], attire_id=item.get("attire_id", ""))
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
        setting_id=data.get("setting_id", ""),
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
    existing.setting_id = data.get("setting_id", existing.setting_id)
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
            beat = Beat.create(
                kind,
                text=data.get("text", ""),
                character_id=data.get("character_id", ""),
                line=data.get("line", ""),
                language=data.get("language", "English"),
                delivery_preset=delivery_preset,
                delivery=delivery,
                timestamp=data.get("timestamp", ""),
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
        for beat in seq.beats:
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

            beat.kind = kind
            beat.text = data.get("text", beat.text)
            beat.character_id = character_id
            beat.line = data.get("line", beat.line)
            beat.language = data.get("language", beat.language)
            beat.delivery_preset = delivery_preset
            beat.delivery = delivery
            beat.timestamp = data.get("timestamp", beat.timestamp)
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


# ---------- generation ----------
@app.route("/api/scenes/<scid>/sequences/<seqid>/generate", methods=["POST"])
def generate_sequence(scid, seqid):
    scene = scenes.load(scid)
    if not scene:
        return jsonify({"error": "scene not found"}), 404
    setting = settings.load(scene.setting_id)
    if not setting:
        return jsonify({"error": "scene has no valid setting"}), 400

    scene_characters = []
    attire_by_char_id = {}
    for casting in scene.character_castings:
        character = characters.load(casting.character_id)
        if character is None:
            return jsonify({"error": f"scene references a missing character '{casting.character_id}'"}), 400
        scene_characters.append(character)
        if casting.attire_id:
            chosen = next((a for a in character.attire_options if a.id == casting.attire_id), None)
            if chosen is None:
                return jsonify({
                    "error": f"attire '{casting.attire_id}' not found for character '{character.name}'"
                }), 400
        else:
            chosen = character.default_attire()
        attire_by_char_id[character.id] = chosen

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
            setting=setting,
            characters=scene_characters,
            sequence=sequence,
            scene=scene,
            attire_by_char_id=attire_by_char_id,
            previous_output_path=previous_output_path,
        )
    except TemplateEngineError as e:
        return jsonify({"error": str(e)}), 400

    scene_out_dir = os.path.join(OUTPUT_DIR, scid)
    os.makedirs(scene_out_dir, exist_ok=True)
    out_path = os.path.join(scene_out_dir, f"{sequence.index:02d}_{sequence.id}.json")
    with open(out_path, "w") as f:
        json.dump(wf, f, indent=2)

    sequence.status = "generated"
    scenes.save(scene)

    return jsonify({
        "workflow_path": out_path,
        "filename_prefix": prefix,
        "sequence": sequence.to_dict(),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5151)
