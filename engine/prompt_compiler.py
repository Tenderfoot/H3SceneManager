"""
Compiles Location + Characters + a Sequence's beats into the six-section
full-reference prompt text described in MiniMax-H3's
VIDEO_PROMPT_WRITING_GUIDE_ref_en.md:

    subject_definitions
    summary
    retention_analysis
    detailed_description
    overall_soundscape
    non_diegetic_music

Design choices, spelled out because they're not fully dictated by the guide:

- <Subject N> numbering is our own bookkeeping (Location is always Subject 1,
  characters follow in scene order) -- it does NOT need to match physical
  socket order. <Picture N> and <Audio N> numbering DOES have to match the
  actual ref_image_N / ref_audio_N socket each asset is wired into, since
  that's positional data the model reads directly. Callers pass in the
  resolved slot names from the wiring step for exactly this reason.

- Every beat starts its own shot ([Shot 1], [Shot 2], ...) UNLESS its
  is_new_shot flag is explicitly False, in which case it folds into the
  shot started by the beat before it -- letting e.g. an action beat and the
  dialogue beat right after it share one [Shot N]. The sequence's first
  beat always starts [Shot 1] regardless of its own flag. [Shot 1] never
  carries a timestamp, per the guide -- later shots render as
  "[Shot N] At MM:SS.mmm, ..." using the timestamp of whichever beat
  actually started that shot. Timestamps are NOT validated (ordering,
  range, or format) -- garbage in, garbage out, by design.

- Speaker IDs (S1, S2...) are assigned fresh per sequence, in order of first
  appearance in that sequence's beats -- each sequence is its own independent
  MiniMax generation call, so there's no persistent speaker memory across
  sequences to preserve.

- A character only gets an <Audio N> definition in the prompt if they
  actually speak in this sequence's beats. Their voice reference socket may
  still be wired for every sequence (for consistency), but if they don't
  speak here, describing an "Audio reference" nothing in the text actually
  uses would be confusing and isn't clearly specified by the guide either way.

- Every occurrence of a character's name inside action-beat text is REPLACED
  with the bare <Subject N> tag (e.g. "Grant" -> "<Subject 2>"), matching the
  guide's own examples, which use the tag directly as the sentence's subject
  rather than a name-plus-parenthetical. The full name still appears once,
  in that character's own subject_definitions line ("<Subject 2> is Grant,
  appearing in...") -- it's only the later, in-scene mentions that get
  replaced outright.

- retention_analysis lines NEVER include (Sx), for Subject or Audio lines --
  the guide states this explicitly ("Do not write (Sx) in retention_analysis")
  and its own worked example confirms it even for subjects that speak
  throughout. (Sx) only appears in subject_definitions and detailed_description.

- `summary` and the detailed_description "style opening" sentence are both
  Scene-level fields (scene.summary_premise, scene.style_opening), not
  derived per-sequence -- summary describes the scene's overall premise and
  reference relationships, which stays the same across every sequence in it,
  and visual style is likewise a scene-wide constant. The summary's
  [task-type] bracket is still auto-computed from actual wiring (which asset
  roles are genuinely present this sequence), since that's mechanical and a
  person can't easily get it right by hand; only the plain-English sentence
  after the bracket comes from the scene field. Per the guide's rule that
  summary must reuse existing labels rather than introduce new ones, that
  premise sentence gets the same character/location name -> bare <Subject N>
  substitution applied to it as detailed_description's beat text does.

- Both the location and each character get a one-time introduction sentence
  prepended at their first appearance, reusing the same description text
  already given in subject_definitions -- per the guide's "describe an
  important Subject at its first clear appearance, then reuse the label
  without redefining it" rule. The location's sentence always attaches to
  [Shot 1] (it's present from the start regardless of that beat's own text);
  each character's attaches to whichever shot they first speak in or are
  named in. Later shots featuring the same subject get no redescription --
  just the bare tag, via _tag_names_in_text or the dialogue line itself.
  This is done as a PREPENDED sentence rather than editing the beat's own
  sentence in place, since splicing a description into an arbitrary
  position (e.g. mid-possessive -- "Grant's hand") risks broken grammar that
  a prepended sentence avoids entirely.
"""
import re

TASK_KEYFRAME = "keyframe completion"
TASK_REFERENCE = "reference generation"
TASK_AUDIO_REF = "audio reference"

# key -> (dropdown label, phrase woven into the compiled prompt as
# "... says <phrase>, <d>...")
DELIVERY_PRESETS = {
    "calm":         ("Calm and measured",        "in a calm, measured tone"),
    "nervous":      ("Nervous / hesitant",        "in a nervous, hesitant tone"),
    "urgent":       ("Urgent / rushed",           "in an urgent, rushed tone"),
    "angry":        ("Angry / sharp",             "in a sharp, angry tone"),
    "sarcastic":    ("Sarcastic / dry",           "in a dry, sarcastic tone"),
    "warm":         ("Warm / affectionate",       "in a warm, affectionate tone"),
    "cold":         ("Cold / clipped",            "in a cold, clipped tone"),
    "pleading":     ("Pleading",                  "in a pleading tone"),
    "confident":    ("Confident / assertive",     "in a confident, assertive tone"),
    "whispered":    ("Whispered",                 "in a hushed whisper"),
    "shouted":      ("Shouted",                   "in a raised, shouting voice"),
    "amused":       ("Amused / teasing",          "in an amused, teasing tone"),
    "sad":          ("Sad / subdued",             "in a sad, subdued tone"),
    "threatening":  ("Threatening / low",         "in a low, threatening tone"),
    "surprised":    ("Surprised / breathless",    "in a surprised, breathless tone"),
    "flat":         ("Flat / deadpan",            "in a flat, deadpan tone"),
    "excited":      ("Excited / eager",           "in an excited, eager tone"),
    "weary":        ("Weary / resigned",          "in a weary, resigned tone"),
    "suspicious":   ("Suspicious / guarded",      "in a suspicious, guarded tone"),
    "commanding":   ("Commanding / authoritative", "in a commanding, authoritative tone"),
}

# key -> (dropdown label, 1-2 sentence style opening prepended before [Shot 1]
# in detailed_description, per the guide's "Style opening" requirement)
STYLE_PRESETS = {
    "cinematic_soft":     ("Cinematic, soft & desaturated",
                            "The target video is in a cinematic, literary style with soft "
                            "lighting and a slightly desaturated color palette."),
    "sitcom_multicam":    ("Multi-camera sitcom, warm",
                            "The target video uses a realistic multi-camera sitcom style "
                            "with warm indoor lighting."),
    "documentary":        ("Documentary, handheld, natural light",
                            "The target video has a documentary handheld feel, shot in "
                            "natural available light with a slightly desaturated, "
                            "observational look."),
    "moody_noir":         ("Moody noir, high contrast",
                            "The target video uses a moody film-noir style with high "
                            "contrast lighting and deep shadows."),
    "bright_commercial":  ("Bright commercial, clean",
                            "The target video has a bright, clean commercial look with "
                            "even lighting and vivid color saturation."),
    "gritty_action":      ("Gritty handheld action",
                            "The target video has a gritty, kinetic handheld style with "
                            "desaturated tones and high-contrast lighting."),
    "dreamy_soft_focus":  ("Dreamy, soft focus",
                            "The target video has a dreamy, soft-focus quality with "
                            "diffused lighting and pastel color grading."),
    "scifi_neon":         ("Sci-fi neon, cool tones",
                            "The target video has a futuristic sci-fi look with cool blue "
                            "and neon accent lighting against dark environments."),
    "golden_hour":        ("Warm golden-hour",
                            "The target video is bathed in warm golden-hour sunlight with "
                            "soft long shadows and a warm color grade."),
    "cold_clinical":      ("Cold, clinical, flat light",
                            "The target video has a cold, clinical look with flat, even "
                            "lighting and a desaturated blue-grey color palette."),
    "horror_low_key":     ("Horror, low-key lighting",
                            "The target video uses low-key horror lighting with deep "
                            "shadows, sparse practical light sources, and a desaturated "
                            "palette."),
    "vintage_film":       ("Vintage film grain",
                            "The target video has a vintage film look with visible grain, "
                            "warm color shifts, and slightly soft focus."),
    "high_key_bright":    ("High-key bright & airy",
                            "The target video has a high-key, bright and airy look with "
                            "soft shadows and a light, even exposure."),
    "epic_widescreen":    ("Epic widescreen, dramatic",
                            "The target video has an epic, widescreen cinematic look with "
                            "dramatic lighting and rich, saturated color."),
    "found_footage":      ("Found-footage, raw",
                            "The target video has a raw found-footage aesthetic with "
                            "handheld camera movement and naturalistic, unpolished "
                            "lighting."),
    "anime_stylized":     ("Stylized, anime-inflected color",
                            "The target video has a stylized, anime-inflected color "
                            "palette with crisp lighting and saturated highlights."),
    "muted_pastel":       ("Muted pastel, editorial",
                            "The target video has a muted pastel, editorial-fashion look "
                            "with soft diffused lighting and a restrained color palette."),
    "harsh_fluorescent":  ("Harsh fluorescent, institutional",
                            "The target video is lit with harsh, flat fluorescent lighting "
                            "typical of institutional interiors, with a slightly "
                            "green-tinted color cast."),
}


def slot_index(slot_name: str) -> int:
    """'ref_images.ref_image_2' -> 2"""
    return int(slot_name.rsplit("_", 1)[1])


def _name_pattern(name: str) -> re.Pattern:
    """
    Matches the character's full name, or just their first name as a
    shorthand fallback -- so "Michael" tags correctly even when the
    character is named "Michael Scott" and you don't want to type the
    surname every time. Full name is tried first in the alternation so
    "Michael Scott" matches as one unit rather than tagging "Michael" alone
    and leaving "Scott" untouched.
    """
    parts = name.split()
    first = parts[0] if parts else name
    if len(parts) > 1:
        alt = f"(?:{re.escape(name)}|{re.escape(first)})"
    else:
        alt = re.escape(name)
    return re.compile(r"\b" + alt + r"\b")


def _tag_names_in_text(text: str, name_subject_pairs) -> str:
    """Replace every occurrence of each given name with its bare <Subject N>
    tag, matching the guide's own convention (its examples use the tag as
    the sentence's subject directly, not name-plus-parenthetical).
    name_subject_pairs: iterable of (name, subject_number) -- callers build
    this from characters and/or the location, since the guide tags both
    ("<Subject 3> eating a cookie in <Subject 1>")."""
    for name, n in name_subject_pairs:
        if n is None or not name:
            continue
        pattern = _name_pattern(name)
        text = pattern.sub(f"<Subject {n}>", text)
    return text


def _group_beats_into_shots(beats):
    """
    Groups a sequence's beats into shots. A beat starts a new shot unless
    its is_new_shot flag is explicitly False, in which case it folds into
    the shot started by the beat before it -- e.g. an action beat and the
    dialogue beat right after it can share one [Shot N] instead of each
    getting its own. The very first beat always starts shot 1 regardless of
    its own flag, since there's no earlier shot for it to fold into.

    Returns a list of shot groups; each group is a list of
    (original_beat_index, beat) tuples, in original order.
    """
    shots = []
    for i, beat in enumerate(beats):
        starts_new = (i == 0) or bool(getattr(beat, "is_new_shot", True))
        if starts_new:
            shots.append([])
        shots[-1].append((i, beat))
    return shots


def compile_prompt(*, location, characters, sequence, scene,
                    location_picture_slot=None,
                    prev_frame_picture_slot=None,
                    character_slots=None,
                    attire_by_char_id=None):
    """
    location: Location instance
    characters: ordered list of Character instances used in this sequence
    sequence: Sequence instance being generated (reads .beats)
    scene: Scene instance (reads .non_diegetic_music)
    location_picture_slot: resolved slot name (e.g. "ref_images.ref_image_0")
        the location's reference image was wired into, or None if not wired
    prev_frame_picture_slot: resolved slot name the previous-frame image was
        wired into, or None if this is the first sequence in the scene
    character_slots: dict of character_id -> {"face": slot_name_or_None,
        "attire": slot_name_or_None, "voice": slot_name_or_None}
    attire_by_char_id: dict of character_id -> AttireOption (or None) --
        the attire actually worn this scene, whose description (if any) gets
        folded into that character's Subject definition alongside their base
        appearance_description.

    Returns the complete prompt string, ready to drop wholesale into the
    workflow's prompt widget.
    """
    character_slots = character_slots or {}
    attire_by_char_id = attire_by_char_id or {}

    # ---------- Subject numbering (our own bookkeeping, order-independent
    # of physical sockets) ----------
    subject_number_by_char_id = {}
    next_subject = 1
    location_subject_number = None
    if location.reference_image or location.visual_description:
        location_subject_number = next_subject
        next_subject += 1
    for character in characters:
        subject_number_by_char_id[character.id] = next_subject
        next_subject += 1

    # Flat (name, subject_number) pairs for tagging free text (beat content,
    # the summary premise) -- covers both characters and the location, since
    # the guide tags the environment in summary too ("<Subject 3> ... in <Subject 1>").
    name_subject_pairs = [(c.name, subject_number_by_char_id.get(c.id)) for c in characters]
    if location_subject_number is not None:
        name_subject_pairs.append((location.name, location_subject_number))

    # ---------- Speaker (Sx) numbering: fresh per sequence, assigned in
    # order of first appearance among this sequence's dialogue beats ----------
    speaker_number_by_char_id = {}
    next_speaker = 1
    for beat in sequence.beats:
        if beat.kind == "dialogue" and beat.character_id not in speaker_number_by_char_id:
            speaker_number_by_char_id[beat.character_id] = next_speaker
            next_speaker += 1

    characters_by_id = {c.id: c for c in characters}
    speaking_char_ids = set(speaker_number_by_char_id.keys())

    # ---------- Per-character shot appearance tracking: a beat starts a new
    # shot unless it's folded into the previous one via is_new_shot=False.
    # A character "appears" in a shot if they speak in it, or their name is
    # mentioned (tagged) in an action beat, anywhere within that shot's
    # (possibly multi-beat) group. ----------
    shot_groups = _group_beats_into_shots(sequence.beats)
    total_shots = len(shot_groups) or 1
    beat_index_to_shot_n = {
        idx: shot_n for shot_n, group in enumerate(shot_groups, start=1) for idx, _beat in group
    }
    character_shot_numbers = {c.id: [] for c in characters}
    for i, beat in enumerate(sequence.beats):
        shot_n = beat_index_to_shot_n[i]
        if beat.kind == "dialogue":
            if (beat.character_id in character_shot_numbers
                    and shot_n not in character_shot_numbers[beat.character_id]):
                character_shot_numbers[beat.character_id].append(shot_n)
        elif beat.kind == "action":
            for character in characters:
                if character.name and _name_pattern(character.name).search(beat.text or ""):
                    if shot_n not in character_shot_numbers[character.id]:
                        character_shot_numbers[character.id].append(shot_n)

    def _shot_labels(shot_numbers):
        return ", ".join(f"[Shot {n}]" for n in shot_numbers)

    # First shot each character actually appears in (speaks or is named),
    # used to prepend a one-time introduction sentence at that shot only --
    # later appearances just use the bare <Subject N> tag, no redescription.
    first_appearance_shot_by_char_id = {
        cid: shots[0] for cid, shots in character_shot_numbers.items() if shots
    }
    character_desc_by_id = {}  # populated below, reused for those intro sentences

    subject_lines = []
    summary_subject_mentions = []
    retention_lines = []
    task_types = []

    # ---------- Previous frame: standalone <Picture N> entry ----------
    if prev_frame_picture_slot is not None:
        pic_n = slot_index(prev_frame_picture_slot) + 1
        subject_lines.append(
            f"<Picture {pic_n}> is the first frame of [Shot 1], continuing directly "
            f"from the end of the previous sequence."
        )
        retention_lines.append(
            f"<Picture {pic_n}> ([Shot 1] first frame): fully_preserved - the frame "
            f"is used unmodified as the exact starting point of this shot."
        )
        task_types.append(TASK_KEYFRAME)

    # ---------- Location: <Subject N> citing its <Picture N> ----------
    if location_subject_number is not None:
        n = location_subject_number
        if location_picture_slot is not None:
            pic_n = slot_index(location_picture_slot) + 1
            citation = f"<Picture {pic_n}>"
        else:
            citation = None
        desc = location.visual_description or location.name
        if citation:
            subject_lines.append(f"<Subject {n}> is the {location.name} environment in {citation}, {desc}.")
        else:
            subject_lines.append(f"<Subject {n}> is the {location.name} environment, {desc}.")
        all_shots_label = _shot_labels(range(1, total_shots + 1))
        retention_lines.append(
            f"<Subject {n}> (appears in {all_shots_label}): fully_preserved - the "
            f"environment's defining features are retained."
        )
        summary_subject_mentions.append(f"<Subject {n}>")
        task_types.append(TASK_REFERENCE)

    # ---------- Characters: <Subject N> citing face/attire <Picture N>s,
    # plus <Audio N> if they speak this sequence ----------
    for character in characters:
        n = subject_number_by_char_id[character.id]
        slots = character_slots.get(character.id, {})
        picture_citations = []
        for key in ("face", "attire"):
            slot_name = slots.get(key)
            if slot_name is not None:
                picture_citations.append(f"<Picture {slot_index(slot_name) + 1}>")
        citation_text = " and ".join(picture_citations) if picture_citations else None

        desc_parts = []
        if character.appearance_description:
            desc_parts.append(character.appearance_description)
        chosen_attire = attire_by_char_id.get(character.id)
        if chosen_attire is not None and getattr(chosen_attire, "description", ""):
            desc_parts.append(chosen_attire.description)
        desc = ", ".join(desc_parts)
        character_desc_by_id[character.id] = desc
        if citation_text and desc:
            subject_lines.append(f"<Subject {n}> is {character.name}, appearing in {citation_text}, {desc}.")
        elif citation_text:
            subject_lines.append(f"<Subject {n}> is {character.name}, appearing in {citation_text}.")
        else:
            subject_lines.append(f"<Subject {n}> is {character.name}, {desc}.")

        speaks = character.id in speaking_char_ids
        shots_for_char = character_shot_numbers.get(character.id) or []
        shots_label = _shot_labels(shots_for_char) if shots_for_char else "[Shot 1]"
        retention_desc = "fully_preserved - the character's identity and appearance are retained"
        # Per the guide: "Do not write (Sx) in retention_analysis" -- (Sx) belongs
        # only in subject_definitions and detailed_description, never here.
        retention_lines.append(f"<Subject {n}> (appears in {shots_label}): {retention_desc}.")
        summary_subject_mentions.append(f"<Subject {n}>")
        if picture_citations:
            task_types.append(TASK_REFERENCE)

        voice_slot = slots.get("voice")
        if voice_slot is not None and speaks:
            audio_n = slot_index(voice_slot) + 1
            sx = speaker_number_by_char_id[character.id]
            # subject_definitions DOES reuse (Sx) here per the guide ("write <Subject N> (Sx)")
            subject_lines.append(f"<Audio {audio_n}> is the voice-timbre reference for <Subject {n}> (S{sx}).")
            # retention_analysis does NOT -- same "no (Sx) in retention_analysis" rule as above.
            retention_lines.append(
                f"<Audio {audio_n}>: reference - its vocal timbre guides the delivery "
                f"of <Subject {n}> without copying the original signal."
            )
            task_types.append(TASK_AUDIO_REF)

    # ---------- summary: auto-computed [task-type] bracket + the scene's own
    # premise sentence (a Scene-level field, since summary describes the
    # scene's premise/reference relationships, reused across every sequence
    # in it -- not something re-derived per generation) ----------
    seen_types = []
    for t in task_types:
        if t not in seen_types:
            seen_types.append(t)
    prefix = "[" + " + ".join(seen_types) + "]" if seen_types else "[reference generation]"
    premise = (scene.summary_premise or "").strip()
    if premise:
        # The guide requires summary to use the same <Subject N>/<Picture N>/etc
        # labels rather than introducing new ones -- so character/location names
        # typed into the premise get replaced with their bare tags, same as
        # detailed_description does.
        premise = _tag_names_in_text(premise, name_subject_pairs)
    else:
        # Fallback for scenes that haven't set a premise yet -- a plain
        # subject listing, matching the guide's need for a summary sentence
        # without inventing narrative content we don't have.
        mention_text = ", ".join(summary_subject_mentions) if summary_subject_mentions else "the scene"
        premise = f"The target video shows {mention_text}."
    summary = f"{prefix} {premise}"

    # ---------- detailed_description: every beat is its own shot ----------
    # Style opening is a Scene-level field (dropdown preset + custom override,
    # same pattern as delivery), since visual style is consistent scene-wide.
    opening = (scene.style_opening or "").strip()
    if not opening:
        opening = "The target video maintains a consistent, naturalistic visual style matching the established scene."
    shot_blocks = []

    for shot_n, group in enumerate(shot_groups, start=1):
        content_parts = []
        for _idx, beat in group:
            if beat.kind == "action":
                piece = _tag_names_in_text(beat.text, name_subject_pairs)
            elif beat.kind == "dialogue":
                character = characters_by_id.get(beat.character_id)
                if character is None:
                    piece = ""
                else:
                    n = subject_number_by_char_id.get(character.id)
                    sx = speaker_number_by_char_id.get(character.id)
                    lang = beat.language or "English"
                    delivery = (beat.delivery or "").strip()
                    says_clause = f"says {delivery}," if delivery else "says,"
                    piece = f"<Subject {n}> (S{sx}) {says_clause} <d>[{lang}] {beat.line}</d>"
            else:
                piece = ""
            if piece:
                content_parts.append(piece)
        content = " ".join(content_parts)

        # Lead-in sentences before this shot's own content, in order:
        # (1) Shot 1 only: frame-continuity citation if chaining from a
        #     previous sequence, then the location's establishing sentence.
        # (2) Any shot: a one-time introduction for each character whose
        #     FIRST appearance in the sequence is this shot, reusing their
        #     subject_definitions description -- per the guide's "describe at
        #     first appearance, reuse the label without redefining it after
        #     that" rule. Later shots featuring the same character get no
        #     redescription, just the bare tag via _tag_names_in_text/dialogue.
        lead_in_parts = []
        if shot_n == 1:
            if prev_frame_picture_slot is not None:
                pic_n = slot_index(prev_frame_picture_slot) + 1
                lead_in_parts.append(f"The shot begins from <Picture {pic_n}>.")
            if location_subject_number is not None:
                location_desc = location.visual_description or location.name
                lead_in_parts.append(f"The shot establishes <Subject {location_subject_number}>, {location_desc}.")

        for character in characters:
            if first_appearance_shot_by_char_id.get(character.id) != shot_n:
                continue
            n = subject_number_by_char_id[character.id]
            char_desc = character_desc_by_id.get(character.id, "")
            sx_part = f" (S{speaker_number_by_char_id[character.id]})" if character.id in speaking_char_ids else ""
            if char_desc:
                lead_in_parts.append(f"<Subject {n}>{sx_part}, {char_desc}, appears in this shot.")
            else:
                lead_in_parts.append(f"<Subject {n}>{sx_part} appears in this shot.")

        lead_in = " ".join(lead_in_parts)

        if shot_n == 1:
            header = "[Shot 1]"  # never carries a timestamp, per the guide
        else:
            # The timestamp of the beat that actually STARTED this shot --
            # beats folded into it via is_new_shot=False don't get their own
            # header, so their own timestamp (which should be blank anyway,
            # enforced server-side) never comes into play here.
            starting_beat = group[0][1]
            ts = (starting_beat.timestamp or "").strip()
            header = f"[Shot {shot_n}] At {ts}," if ts else f"[Shot {shot_n}]"
        block = " ".join(p for p in (header, lead_in, content) if p).strip()

        shot_blocks.append(block)

    detailed_description = opening + "\n" + "\n".join(shot_blocks)

    # ---------- overall_soundscape / non_diegetic_music ----------
    overall_soundscape = location.soundscape_description.strip() if location.soundscape_description else "N/A"
    non_diegetic_music = scene.non_diegetic_music.strip() if scene.non_diegetic_music else "N/A"

    sections = [
        ("subject_definitions", "\n".join(subject_lines)),
        ("summary", summary),
        ("retention_analysis", "\n".join(retention_lines)),
        ("detailed_description", detailed_description),
        ("overall_soundscape", overall_soundscape),
        ("non_diegetic_music", non_diegetic_music),
    ]
    return "\n\n".join(f"{title}:\n{body}" for title, body in sections)


def compile_lean_prompt(*, location, characters, sequence, scene,
                         location_picture_slot=None,
                         prev_frame_picture_slot=None,
                         character_slots=None,
                         attire_by_char_id=None):
    """
    Leaner alternative to compile_prompt(), modeled on MiniMax H3's BASE
    prompt guide (T2VA/I2VA/FL2VA/L2VA) instead of the full-reference
    rewrite guide.

    Rationale: H3 has no local prompt-rewriter stage -- the elaborate
    subject_definitions / [task-type] bracket / retention_analysis
    scaffolding is normally PRODUCED by a hosted rewriter model (Context-IR),
    not something the open-weight base checkpoint necessarily learned to
    parse as literal raw input. This format drops that scaffolding in favor
    of natural prose, while still citing <Picture N> and <Audio N> directly
    -- those tags are NOT rewrite-guide-specific, they're how the Ref2VA
    node itself binds prompt text to wired reference media (confirmed via
    the node's own documentation), so they're kept regardless of format.

    The base guide was written for single/dual-keyframe tasks (I2VA/FL2VA/
    L2VA) and never describes multi-asset per-character referencing at all
    -- there's no documented convention for "here's a face image, an attire
    image, and a voice clip, all for the same character." The choices below
    are our own adaptation, not a literal implementation of either guide:

    - No <Subject N> abstraction. Characters and the location are referred to
      by their actual names throughout, matching the base guide's own style
      (it never uses abstract subject labels).
    - Each character/location's <Picture N>/<Audio N> citation happens inline,
      folded into their first-appearance sentence, using the base guide's
      own "preserving X, Y, Z" idiom (lifted directly from its I2VA example:
      "preserving her appearance, clothing, seat position...") as the
      functional replacement for retention_analysis's preservation markers.
    - Chaining from a previous sequence uses the base guide's OWN documented
      I2VA alignment-instruction line, since our previous-frame chaining
      really is a literal first-frame anchor, not an identity/style
      reference -- a closer documented fit than the rewrite guide's
      subject_definitions style.
    - Neither scene.summary_premise nor scene.style_opening has a section to
      live in here (this three-field structure has no `summary` field, and
      the base guide's own convention folds style into Shot 1's sentence
      rather than giving it its own paragraph). Both are placed as their
      own standalone, unlabeled paragraphs -- premise first, then style --
      between the alignment preamble (if any) and the core fields, per
      explicit request, rather than folded into Shot 1's own sentence.
    - Dialogue uses the base guide's own confirmed punctuation: "says:"
      with a colon, not the rewrite guide's "says," with a comma. Real,
      deliberate difference between the two source documents.
    - retention_analysis, subject_definitions, and the [task-type] bracket
      are dropped entirely -- deliberately, not left out by oversight.

    Returns the complete prompt string (alignment-instruction preamble, if
    any, followed by the three core fields), ready to drop wholesale into
    the workflow's prompt widget.
    """
    character_slots = character_slots or {}
    attire_by_char_id = attire_by_char_id or {}
    characters_by_id = {c.id: c for c in characters}

    # Speaker numbering, fresh per sequence (same rationale as the full format:
    # each sequence is its own independent generation, no persistent speaker
    # memory across sequences to preserve).
    speaker_number_by_char_id = {}
    next_speaker = 1
    for beat in sequence.beats:
        if beat.kind == "dialogue" and beat.character_id not in speaker_number_by_char_id:
            speaker_number_by_char_id[beat.character_id] = next_speaker
            next_speaker += 1

    # Per-character first-appearance shot tracking (same logic as the full
    # format): a character "appears" in a shot if they speak in it or their
    # name is mentioned in an action beat's text, anywhere within that
    # shot's (possibly multi-beat) group.
    shot_groups = _group_beats_into_shots(sequence.beats)
    beat_index_to_shot_n = {
        idx: shot_n for shot_n, group in enumerate(shot_groups, start=1) for idx, _beat in group
    }
    character_shot_numbers = {c.id: [] for c in characters}
    for i, beat in enumerate(sequence.beats):
        shot_n = beat_index_to_shot_n[i]
        if beat.kind == "dialogue":
            if (beat.character_id in character_shot_numbers
                    and shot_n not in character_shot_numbers[beat.character_id]):
                character_shot_numbers[beat.character_id].append(shot_n)
        elif beat.kind == "action":
            for character in characters:
                if character.name and _name_pattern(character.name).search(beat.text or ""):
                    if shot_n not in character_shot_numbers[character.id]:
                        character_shot_numbers[character.id].append(shot_n)
    first_appearance_shot_by_char_id = {
        cid: shots[0] for cid, shots in character_shot_numbers.items() if shots
    }

    def _character_intro(character):
        slots = character_slots.get(character.id, {})
        picture_citations = []
        for key in ("face", "attire"):
            slot_name = slots.get(key)
            if slot_name is not None:
                picture_citations.append(f"<Picture {slot_index(slot_name) + 1}>")
        citation_text = " and ".join(picture_citations) if picture_citations else None

        voice_slot = slots.get("voice")
        voice_clause = None
        if voice_slot is not None:
            voice_clause = f"voiced by <Audio {slot_index(voice_slot) + 1}>"

        desc_parts = []
        if character.appearance_description:
            desc_parts.append(character.appearance_description)
        chosen_attire = attire_by_char_id.get(character.id)
        if chosen_attire is not None and getattr(chosen_attire, "description", ""):
            desc_parts.append(chosen_attire.description)
        desc = ", ".join(desc_parts)

        ref_parts = []
        if citation_text:
            ref_parts.append(f"shown in {citation_text}")
        if voice_clause:
            ref_parts.append(voice_clause)
        ref_clause = " and ".join(ref_parts)

        if ref_clause and desc:
            return f"{character.name}, {ref_clause}, preserving {desc}"
        elif ref_clause:
            return f"{character.name}, {ref_clause}"
        return character.name

    def _location_intro():
        desc = location.visual_description or location.name
        if location_picture_slot is not None:
            pic_n = slot_index(location_picture_slot) + 1
            return f"the {location.name} shown in <Picture {pic_n}>, preserving {desc}"
        return f"the {location.name}, {desc}"

    shot_blocks = []
    for shot_n, group in enumerate(shot_groups, start=1):
        content_parts = []
        for _idx, beat in group:
            if beat.kind == "action":
                piece = beat.text or ""
            elif beat.kind == "dialogue":
                character = characters_by_id.get(beat.character_id)
                if character is None:
                    piece = ""
                else:
                    sx = speaker_number_by_char_id.get(character.id)
                    lang = beat.language or "English"
                    delivery = (beat.delivery or "").strip()
                    delivery_clause = f" {delivery}" if delivery else ""
                    piece = f"{character.name} (S{sx}) says{delivery_clause}: <d>[{lang}] {beat.line}</d>"
            else:
                piece = ""
            if piece:
                content_parts.append(piece)
        content = " ".join(content_parts)

        lead_in_parts = []

        if shot_n == 1:
            # Location's establishing sentence opens Shot 1. Both
            # scene.summary_premise and scene.style_opening are placed
            # separately, as their own standalone paragraphs between the
            # alignment preamble and the core fields -- see assembly below.
            lead_in_parts.append(f"A shot establishes {_location_intro()}.")

        for character in characters:
            if first_appearance_shot_by_char_id.get(character.id) == shot_n:
                lead_in_parts.append(f"{_character_intro(character)} appears.")

        lead_in = " ".join(lead_in_parts)

        if shot_n == 1:
            header = "[Shot 1]"  # never carries a timestamp, per the guide
        else:
            # The timestamp of the beat that actually STARTED this shot --
            # beats folded into it via is_new_shot=False don't get their own
            # header, so their own timestamp (which should be blank anyway,
            # enforced server-side) never comes into play here.
            starting_beat = group[0][1]
            ts = (starting_beat.timestamp or "").strip()
            header = f"[Shot {shot_n}] At {ts}," if ts else f"[Shot {shot_n}]"

        block = " ".join(p for p in (header, lead_in, content) if p).strip()
        shot_blocks.append(block)

    integrated_multimodal_description = "\n".join(shot_blocks)

    preamble = ""
    if prev_frame_picture_slot is not None:
        pic_n = slot_index(prev_frame_picture_slot) + 1
        preamble = (
            f"For the target video, at 0.00 seconds into the target video, "
            f"<Picture {pic_n}> (from [Shot 1]) is fully referenced.\n\n"
        )

    # scene.summary_premise has no section to live in (this format has no
    # `summary` field), so it's placed as its own standalone paragraph,
    # positioned after the alignment preamble (if any) and before the core
    # fields -- unlabeled, matching the preamble's own bare-paragraph style
    # rather than inventing a header the base guide doesn't document.
    premise = (scene.summary_premise or "").strip()
    premise_block = f"{premise}\n\n" if premise else ""

    # scene.style_opening likewise has no section here -- also its own
    # standalone paragraph, positioned after the premise and still before
    # the core fields (per explicit request), rather than folded into
    # Shot 1's own sentence as the base guide's own examples do it.
    style = (scene.style_opening or "").strip()
    style_block = f"{style}\n\n" if style else ""

    overall_soundscape = location.soundscape_description.strip() if location.soundscape_description else "N/A"
    non_diegetic_music = scene.non_diegetic_music.strip() if scene.non_diegetic_music else "N/A"

    sections = [
        ("integrated_multimodal_description", integrated_multimodal_description),
        ("overall_soundscape", overall_soundscape),
        ("non_diegetic_music", non_diegetic_music),
    ]
    body = "\n\n".join(f"{title}:\n{content}" for title, content in sections)
    return preamble + premise_block + style_block + body
