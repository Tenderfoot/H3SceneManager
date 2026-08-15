"""
Turns the Grant_Template_Workflow.json template + a Scene/Sequence's data into
a concrete, ready-to-queue ComfyUI workflow JSON for a single sequence.

Core ideas, matched to the conventions already present in the template file:

- A group titled "{{ Character N }}" is the *template* to clone once per
  character in the scene. Each character gets its own real group box in the
  output, titled "Character (<name>)", laid out side-by-side with a buffer
  gap between them (not just floating unlabeled nodes). The nodes inside the
  template group are identified by their own {{ }} titles:
      "{{ Face N }}"   (LoadImage)  -> wired into the next free ref_images.* slot
      "{{ Attire N }}" (LoadImage)  -> wired into the next free ref_images.* slot
      "{{ Voice N }}"  (LoadAudio)  -> wired into the next free ref_audios.* slot,
                                       only for characters who speak this sequence

- A group titled "{{ Establishing References }}" holds two singleton nodes:
      "{{ Setting Image }}"            (LoadImage)   -> wired once, into ref_images.*
      "{{ Previous Video Final Frame }}" (FirstFrameLastFrameExtractor)
          -> only included for sequences after the first in a scene; its
             video_path widget contains the literal substring "{{oldfilename}}"
             which gets replaced with the previous sequence's resolved output path.

- The "sink" node (found by class_type, currently "MiniMaxH3ReferenceToVideo")
  exposes numbered socket families named "<family>.<family_singular>_<N>"
  (e.g. "ref_images.ref_image_0"). We reuse existing *unlinked* slots first,
  and only append brand-new slot entries once those run out.

- Any other string widget value containing "{{ Some_Key }}" is replaced via a
  generic substitution pass, keyed by a dict you pass in (Pickup_Description,
  Setting_Description, etc.)

ASSUMPTION THAT NEEDS LIVE VERIFICATION: appending new socket entries beyond
what the template already shows (e.g. a 5th ref_image or 2nd ref_audio) is
based on pattern-matching the existing entries, not on the custom node's
Python source. Confirm in ComfyUI itself once you generate a 2+ character
scene that the new sockets are actually recognized by the node.

HARD CAPS: MiniMax H3's ref2va mode supports at most 9 reference images and
3 reference audio clips per generation (per its own documentation). Exceeding
either produces an unclear failure mode at best, so this module enforces
both caps at generation time and raises TemplateEngineError rather than
silently emitting a workflow the model can't actually use. Voice nodes are
only wired for characters who actually have a dialogue beat in that specific
sequence -- a character present in the scene but silent this sequence
doesn't consume an audio slot at all, which is what keeps a larger
non-speaking cast from needlessly hitting the 3-audio cap.
"""
import copy
import re

from .prompt_compiler import compile_prompt, compile_lean_prompt

SINK_NODE_TYPE = "MiniMaxH3ReferenceToVideo"
CHARACTER_GROUP_TITLE = "{{ Character N }}"
ESTABLISHING_GROUP_TITLE = "{{ Establishing References }}"
FACE_TITLE = "{{ Face N }}"
ATTIRE_TITLE = "{{ Attire N }}"
VOICE_TITLE = "{{ Voice N }}"
SETTING_IMAGE_TITLE = "{{ Setting Image }}"
PREV_FRAME_TITLE = "{{ Previous Video Final Frame }}"

IMAGE_FAMILY = ("ref_images", "ref_image")
AUDIO_FAMILY = ("ref_audios", "ref_audio")

MAX_REF_IMAGES = 9  # MiniMax H3 ref2va hard cap
MAX_REF_AUDIOS = 3  # MiniMax H3 ref2va hard cap
FAMILY_CAPS = {IMAGE_FAMILY: MAX_REF_IMAGES, AUDIO_FAMILY: MAX_REF_AUDIOS}

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_ ]+?)\s*\}\}")


class TemplateEngineError(Exception):
    pass


def _find_group(template, title):
    for g in template.get("groups", []):
        if g.get("title") == title:
            return g
    raise TemplateEngineError(f"No group titled {title!r} found in template")


def _nodes_in_group(template, group):
    bx, by, bw, bh = group["bounding"]
    result = []
    for node in template["nodes"]:
        nx, ny = node["pos"]
        if bx <= nx <= bx + bw and by <= ny <= by + bh:
            result.append(node)
    return result


def _find_node_by_type(template, class_type):
    for node in template["nodes"]:
        if node.get("type") == class_type:
            return node
    raise TemplateEngineError(f"No node of type {class_type!r} found in template")


def _next_ids(template):
    """Returns callables that hand out fresh, unique node/link/group ids."""
    state = {
        "node": template.get("last_node_id", 0),
        "link": template.get("last_link_id", 0),
        "group": max((g.get("id", 0) for g in template.get("groups", [])), default=0),
    }

    def next_node_id():
        state["node"] += 1
        template["last_node_id"] = state["node"]
        return state["node"]

    def next_link_id():
        state["link"] += 1
        template["last_link_id"] = state["link"]
        return state["link"]

    def next_group_id():
        state["group"] += 1
        return state["group"]

    return next_node_id, next_link_id, next_group_id


def _reserve_slot(sink_node, family):
    """
    Find the next usable socket on `family` (e.g. IMAGE_FAMILY) on sink_node.
    Prefers an existing, unlinked slot; otherwise fabricates a new one by
    incrementing past the highest existing index, and appends it to the
    node's `inputs` list (returning its index within that list).
    Returns (input_index_in_list, slot_name, is_new).
    """
    family_key, singular = family
    prefix = f"{family_key}.{singular}_"
    existing = []
    for i, inp in enumerate(sink_node["inputs"]):
        if inp.get("name", "").startswith(prefix):
            idx = int(inp["name"].rsplit("_", 1)[1])
            existing.append((idx, i, inp))
    existing.sort(key=lambda t: t[0])

    for idx, list_index, inp in existing:
        if inp.get("link") is None:
            return list_index, inp["name"], False

    new_idx = (existing[-1][0] + 1) if existing else 0
    cap = FAMILY_CAPS.get(family)
    if cap is not None and new_idx >= cap:
        raise TemplateEngineError(
            f"This sequence needs more than {cap} {family_key} references, which exceeds "
            f"MiniMax H3 ref2va's hard cap of {cap}. Reduce the cast for this scene, or free "
            f"up references (e.g. only characters who actually speak in a sequence need a "
            f"voice reference wired for it)."
        )
    new_name = f"{prefix}{new_idx}"
    new_input = {
        "label": f"{singular}_{new_idx}",
        "name": new_name,
        "shape": 7,
        "type": "IMAGE" if family_key == "ref_images" else "AUDIO",
        "link": None,
    }
    sink_node["inputs"].append(new_input)
    return len(sink_node["inputs"]) - 1, new_name, True


def _wire(template, sink_node, slot_list_index, source_node_id, source_slot_index, link_type,
          next_link_id):
    link_id = next_link_id()
    template["links"].append([link_id, source_node_id, source_slot_index,
                               sink_node["id"], slot_list_index, link_type])
    sink_node["inputs"][slot_list_index]["link"] = link_id


def _unwire_and_remove(wf, node_ids_to_remove):
    """
    Cleanly detach and delete a set of nodes: drops any link touching them,
    resets the `link` field on any input that pointed at one of those links
    back to None (so that slot is free for reuse), and removes the nodes
    themselves from wf["nodes"].
    """
    if not node_ids_to_remove:
        return
    removed_link_ids = {
        link[0] for link in wf["links"]
        if link[1] in node_ids_to_remove or link[3] in node_ids_to_remove
    }
    wf["links"] = [l for l in wf["links"] if l[0] not in removed_link_ids]
    for node in wf["nodes"]:
        for inp in node.get("inputs", []):
            if inp.get("link") in removed_link_ids:
                inp["link"] = None
    wf["nodes"] = [n for n in wf["nodes"] if n["id"] not in node_ids_to_remove]


def _substitute_strings(node, substitutions):
    wv = node.get("widgets_values")
    if not isinstance(wv, list):
        return
    for i, val in enumerate(wv):
        if isinstance(val, str):
            def repl(m):
                key = m.group(1).strip()
                return str(substitutions.get(key, m.group(0)))
            wv[i] = PLACEHOLDER_RE.sub(repl, val)


def generate_sequence_workflow(template, *, setting, characters, sequence, scene,
                                attire_by_char_id=None,
                                previous_output_path=None, output_prefix=None):
    """
    template: parsed dict of the template workflow JSON (will be deep-copied)
    setting: Setting instance
    characters: ordered list of Character instances for this scene
    sequence: Sequence instance being generated
    scene: Scene instance (for non_diegetic_dialog / soundscape)
    attire_by_char_id: dict of character_id -> AttireOption (or None) --
        the ONE attire chosen for that character in this scene. Only that
        attire's image gets wired in; a character with no entry (or a None
        value) gets no Attire node at all for this generation.
    previous_output_path: resolved absolute path to previous sequence's video,
        or None if this is the first sequence in the scene
    output_prefix: filename prefix to give the SaveVideo node; defaults to a
        prefix derived from scene id + sequence index

    Returns: a new workflow dict, ready to write to disk / submit to ComfyUI.
    """
    attire_by_char_id = attire_by_char_id or {}
    wf = copy.deepcopy(template)
    next_node_id, next_link_id, next_group_id = _next_ids(wf)
    sink = _find_node_by_type(wf, SINK_NODE_TYPE)

    # --- Establishing References group ---
    establishing_group = _find_group(wf, ESTABLISHING_GROUP_TITLE)
    establishing_nodes = _nodes_in_group(wf, establishing_group)

    setting_node = next((n for n in establishing_nodes if n.get("title") == SETTING_IMAGE_TITLE), None)
    prev_frame_node = next((n for n in establishing_nodes if n.get("title") == PREV_FRAME_TITLE), None)

    # --- Character N group: identify template nodes before wiping anything ---
    character_group = _find_group(wf, CHARACTER_GROUP_TITLE)
    template_char_nodes = _nodes_in_group(wf, character_group)
    if not template_char_nodes:
        raise TemplateEngineError("Character N group is empty in template")
    template_char_node_ids = {n["id"] for n in template_char_nodes}

    # --- Clean slate: detach every node whose wiring we're about to rebuild.
    # This resets their target slots on the sink node back to link=None so
    # _reserve_slot can correctly reuse or extend them below.
    nodes_to_reset = set(template_char_node_ids)
    if setting_node is not None:
        nodes_to_reset.add(setting_node["id"])
    if prev_frame_node is not None:
        nodes_to_reset.add(prev_frame_node["id"])
    # Keep setting_node/prev_frame_node themselves (we still want to reuse and
    # rewire them if applicable) -- only fully *remove* the character template
    # nodes and, conditionally, prev_frame_node if there's no previous output.
    keep_ids = set()
    if setting_node is not None:
        keep_ids.add(setting_node["id"])
    if prev_frame_node is not None and previous_output_path:
        keep_ids.add(prev_frame_node["id"])
    remove_ids = nodes_to_reset - keep_ids
    _unwire_and_remove(wf, remove_ids)
    if prev_frame_node is not None and prev_frame_node["id"] in remove_ids:
        prev_frame_node = None
    # For nodes we're keeping, still clear their existing outbound link so
    # _reserve_slot treats the target slot as free (the node itself remains).
    keep_node_ids_only = keep_ids - remove_ids
    if keep_node_ids_only:
        kept_link_ids = {
            link[0] for link in wf["links"] if link[1] in keep_node_ids_only
        }
        wf["links"] = [l for l in wf["links"] if l[0] not in kept_link_ids]
        for node in wf["nodes"]:
            for inp in node.get("inputs", []):
                if inp.get("link") in kept_link_ids:
                    inp["link"] = None

    if setting_node is not None:
        setting_node["widgets_values"][0] = setting.reference_image
        setting_node["title"] = f"Setting: {setting.name}"
        list_idx, setting_picture_slot, _ = _reserve_slot(sink, IMAGE_FAMILY)
        _wire(wf, sink, list_idx, setting_node["id"], 0, "IMAGE", next_link_id)
    else:
        setting_picture_slot = None

    prev_frame_picture_slot = None
    if prev_frame_node is not None:
        if previous_output_path:
            # The template's stored path is just an example (it hardcodes one
            # machine's directory structure around the {{oldfilename}} marker).
            # Since we already have the *fully resolved* absolute path to the
            # previous sequence's output (from resolve_output), use it outright
            # rather than splicing into that example string -- the two won't
            # generally agree on directory structure (e.g. per-scene subfolders).
            if "{{oldfilename}}" in prev_frame_node["widgets_values"][0]:
                prev_frame_node["widgets_values"][0] = previous_output_path
            else:
                # No placeholder present at all -- something changed in the
                # template; fail loudly rather than silently doing nothing.
                raise TemplateEngineError(
                    "Previous Video Final Frame node's path has no {{oldfilename}} "
                    "placeholder to replace"
                )
            list_idx, prev_frame_picture_slot, _ = _reserve_slot(sink, IMAGE_FAMILY)
            # output slot 1 on FirstFrameLastFrameExtractor is `last_frame`
            _wire(wf, sink, list_idx, prev_frame_node["id"], 1, "IMAGE", next_link_id)
        # else: no previous_output_path -> prev_frame_node was already fully
        # removed above (first sequence in the scene has no prior frame).

    # --- Character N group: clone once per character, each into its OWN
    # visible group box, laid out left-to-right with a buffer between them.
    group_bx, group_by, group_bw, group_bh = character_group["bounding"]
    GROUP_BUFFER = 80
    step = group_bw + GROUP_BUFFER

    # The original "{{ Character N }}" group box is a template, not a real
    # character -- drop it, we're replacing it with one real group per character.
    wf["groups"] = [g for g in wf["groups"] if g is not character_group]

    # Only characters who actually have a dialogue beat in THIS sequence get a
    # Voice node wired -- a character present in the scene but silent this
    # sequence doesn't need (or consume) an audio reference slot. This is what
    # keeps a larger non-speaking cast from needlessly hitting the 3-audio cap.
    speaking_char_ids = {b.character_id for b in sequence.beats if b.kind == "dialogue"}

    character_slots = {}  # character.id -> {"face": slot_name, "attire": slot_name, "voice": slot_name}

    for i, character in enumerate(characters):
        dx = i * step
        wf["groups"].append({
            "id": next_group_id(),
            "title": f"Character ({character.name})",
            "bounding": [group_bx + dx, group_by, group_bw, group_bh],
            "color": character_group.get("color", "#3f789e"),
            "flags": character_group.get("flags", {}),
        })
        character_slots[character.id] = {"face": None, "attire": None, "voice": None}

        for src_node in template_char_nodes:
            title = src_node.get("title")

            if title == ATTIRE_TITLE:
                chosen_attire = attire_by_char_id.get(character.id)
                if chosen_attire is None or not chosen_attire.image_path:
                    # No attire selected/available for this character in this
                    # scene -- skip creating this node entirely rather than
                    # wiring an empty path.
                    continue
            elif title == VOICE_TITLE:
                if not character.voice_audio:
                    continue  # no voice reference for this character at all
                if character.id not in speaking_char_ids:
                    continue  # has a voice reference, but doesn't speak this sequence

            new_node = copy.deepcopy(src_node)
            new_node["id"] = next_node_id()
            new_node["pos"] = [src_node["pos"][0] + dx, src_node["pos"][1]]

            if title == FACE_TITLE:
                new_node["widgets_values"][0] = character.face_image
                new_node["title"] = f"Face: {character.name}"
                family = IMAGE_FAMILY
                slot_key = "face"
            elif title == ATTIRE_TITLE:
                new_node["widgets_values"][0] = chosen_attire.image_path
                label = f" ({chosen_attire.label})" if chosen_attire.label else ""
                new_node["title"] = f"Attire: {character.name}{label}"
                family = IMAGE_FAMILY
                slot_key = "attire"
            elif title == VOICE_TITLE:
                new_node["widgets_values"][0] = character.voice_audio
                new_node["title"] = f"Voice: {character.name}"
                family = AUDIO_FAMILY
                slot_key = "voice"
            else:
                # Unrecognized node in the group -- keep it as-is, unwired.
                wf["nodes"].append(new_node)
                continue

            new_node["outputs"][0]["links"] = []
            wf["nodes"].append(new_node)

            list_idx, slot_name, _ = _reserve_slot(sink, family)
            _wire(wf, sink, list_idx, new_node["id"], 0,
                  "IMAGE" if family is IMAGE_FAMILY else "AUDIO", next_link_id)
            character_slots[character.id][slot_key] = slot_name

    # --- Duration -> the PrimitiveFloat feeding the length calculation ---
    for node in wf["nodes"]:
        if node.get("title") == "Float (Duration)":
            node["widgets_values"][0] = sequence.duration

    # --- Output filename prefix, for chaining ---
    prefix = output_prefix or f"video/{scene.id}/{sequence.index:02d}_{sequence.id}"
    for node in wf["nodes"]:
        if node.get("type") == "SaveVideo":
            node["widgets_values"][0] = prefix

    # --- Compile the prompt (full six-section rewrite-guide format, or the
    # leaner base-guide-style format -- scene.prompt_format decides) and drop
    # it wholesale into the prompt widget (fully replaces the template's
    # example prompt text) ---
    compiler_fn = compile_lean_prompt if scene.prompt_format == "lean" else compile_prompt
    compiled_prompt = compiler_fn(
        setting=setting, characters=characters, sequence=sequence, scene=scene,
        setting_picture_slot=setting_picture_slot,
        prev_frame_picture_slot=prev_frame_picture_slot,
        character_slots=character_slots,
        attire_by_char_id=attire_by_char_id,
    )
    for node in wf["nodes"]:
        if node.get("title") == "Input Text (Prompt)":
            node["widgets_values"][0] = compiled_prompt

    # --- Generic {{ placeholder }} substitution across any OTHER string widgets
    # (nothing currently uses this since the prompt is now fully compiled above,
    # but it's kept as a hook for future template placeholders) ---
    substitutions = {}
    for node in wf["nodes"]:
        _substitute_strings(node, substitutions)

    return wf, prefix
