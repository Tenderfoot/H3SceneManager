import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.models import Character, Setting, Sequence, Scene, Beat, AttireOption
from engine.template_engine import generate_sequence_workflow, TemplateEngineError
from engine.prompt_compiler import compile_prompt

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "templates", "Grant_Template_Workflow.json")


def load_template():
    with open(TEMPLATE_PATH) as f:
        return json.load(f)


def check_workflow_integrity(wf):
    node_ids = {n["id"] for n in wf["nodes"]}
    assert len(node_ids) == len(wf["nodes"]), "duplicate node ids!"
    link_ids = [l[0] for l in wf["links"]]
    assert len(link_ids) == len(set(link_ids)), "duplicate link ids!"
    for link in wf["links"]:
        _, src, _, dst, _, _ = link
        assert src in node_ids, f"link references missing source node {src}"
        assert dst in node_ids, f"link references missing target node {dst}"
    for node in wf["nodes"]:
        for inp in node.get("inputs", []):
            if inp.get("link") is not None:
                assert inp["link"] in link_ids, f"node {node['id']} input references missing link {inp['link']}"


def run():
    template = load_template()

    setting = Setting.create(
        "Star Trek Hallway",
        reference_image="/abs/path/hallway.png",
        visual_description="a sleek starship corridor with pulsing blue accent lighting and metallic paneling",
        soundscape_description="A low ambient ship hum and the occasional distant electronic chirp continue throughout.",
    )

    grant_casual = AttireOption.create(label="Off-duty", image_path="/abs/path/grant_casual.png",
                                        description="a loose grey henley and worn jeans", is_default=False)
    grant_uniform = AttireOption.create(label="Uniform", image_path="/abs/path/grant_attire.png",
                                         description="a pressed grey Starfleet-style uniform", is_default=True)
    grant = Character.create(
        "Grant", face_image="/abs/path/grant_face.png",
        voice_audio="/abs/path/grant_voice.wav",
        attire_options=[grant_casual, grant_uniform],
        appearance_description="a man in his 30s with short dark hair",
    )
    zara_vest = AttireOption.create(label="Tactical", image_path="/abs/path/zara_attire.png",
                                     description="a dark tactical vest over a black jumpsuit", is_default=True)
    zara = Character.create(
        "Zara", face_image="/abs/path/zara_face.png",
        voice_audio="/abs/path/zara_voice.wav",
        attire_options=[zara_vest],
        appearance_description="a woman with silver-streaked hair",
    )

    scene = Scene.create(
        "Corridor Standoff", setting_id=setting.id,
        character_castings=[],  # not used directly by the engine test below
        non_diegetic_music="A tense, low string ostinato builds slowly underneath the scene.",
        summary_premise="Grant confronts Zara in the corridor after discovering the breach, "
                         "and the standoff turns physical.",
        style_preset="scifi_neon",
        style_opening="The target video has a futuristic sci-fi look with cool blue and neon "
                       "accent lighting against dark environments.",
    )

    # attire actually worn this scene: Grant wears his off-duty option (NOT
    # his default), Zara has no explicit choice so falls back to her default.
    attire_by_char_id = {
        grant.id: grant_casual,
        zara.id: zara.default_attire(),
    }

    seq0 = Sequence(id="seq_0001", index=0, duration=8.0, beats=[
        Beat.create("action", text="Grant draws his phaser as Zara backs against the wall."),
        Beat.create("dialogue", character_id=grant.id, line="Don't move."),
    ])
    seq1 = Sequence(id="seq_0002", index=1, duration=6.0, beats=[
        Beat.create("action", text="Zara knocks the phaser from Grant's hand."),
        Beat.create("dialogue", character_id=zara.id, line="You should have listened.",
                     timestamp="00:02.500"),
        Beat.create("action", text="Grant staggers back, glaring at Zara.", timestamp="00:04.800"),
    ])

    # --- Test 1: single character, first sequence (no previous frame) ---
    wf1, prefix1 = generate_sequence_workflow(
        template, setting=setting, characters=[grant], sequence=seq0, scene=scene,
        attire_by_char_id=attire_by_char_id,
        previous_output_path=None,
    )
    check_workflow_integrity(wf1)
    print(f"[OK] 1-character, first sequence. prefix={prefix1}")

    # Grant's non-default (off-duty) attire should be what actually got wired,
    # not his marked-default uniform -- confirms per-scene attire choice wins.
    grant_attire_node = next(n for n in wf1["nodes"] if str(n.get("title", "")).startswith("Attire: Grant"))
    assert grant_attire_node["widgets_values"][0] == grant_casual.image_path, \
        "expected the scene's chosen (off-duty) attire, not the character's default"
    print("[OK] scene-level attire choice overrides character default")

    # --- Test 2: two characters, second sequence (chains from previous) ---
    wf2, prefix2 = generate_sequence_workflow(
        template, setting=setting, characters=[grant, zara], sequence=seq1, scene=scene,
        attire_by_char_id=attire_by_char_id,
        previous_output_path="/abs/path/output/video/scene_x/00_seq_0001_00001.mp4",
    )
    check_workflow_integrity(wf2)
    print(f"[OK] 2-character, chained sequence. prefix={prefix2}")

    # Spot-check: sink node should now have extra sockets beyond the template's originals
    sink = next(n for n in wf2["nodes"] if n["type"] == "MiniMaxH3ReferenceToVideo")
    image_slots = [i["name"] for i in sink["inputs"] if i["name"].startswith("ref_images.")]
    audio_slots = [i["name"] for i in sink["inputs"] if i["name"].startswith("ref_audios.")]
    print("image slots:", image_slots)
    print("audio slots:", audio_slots)

    # Spot-check: one real group box per character, correctly titled, non-overlapping
    char_groups = [g for g in wf2["groups"] if g["title"].startswith("Character (")]
    assert len(char_groups) == 2, f"expected 2 character groups, got {len(char_groups)}"
    titles = {g["title"] for g in char_groups}
    assert titles == {"Character (Grant)", "Character (Zara)"}, titles
    b0 = char_groups[0]["bounding"]
    b1 = char_groups[1]["bounding"]
    assert b0[0] != b1[0], "character group boxes should not sit at the same x position"
    gap = abs(b1[0] - b0[0]) - b0[2]
    assert gap > 0, f"character group boxes overlap or touch (gap={gap})"
    print(f"[OK] 2 distinct, non-overlapping character group boxes (gap={gap}px)")
    assert not any(g["title"] == "{{ Character N }}" for g in wf2["groups"]), \
        "template placeholder group should have been removed"
    print("[OK] template Character N group removed from output")

    # Every duplicated character node should actually fall inside its group's box.
    # Speaking characters get 3 nodes (Face/Attire/Voice); non-speakers get 2
    # (no Voice node, since seq1 has Zara speak but not Grant).
    speaking_in_seq1 = {b.character_id for b in seq1.beats if b.kind == "dialogue"}
    name_to_id = {"Grant": grant.id, "Zara": zara.id}

    def nodes_in_box(nodes, box):
        bx, by, bw, bh = box
        return [n for n in nodes if bx <= n["pos"][0] <= bx + bw and by <= n["pos"][1] <= by + bh]
    for g in char_groups:
        contained = nodes_in_box(wf2["nodes"], g["bounding"])
        char_name = g["title"].split("(", 1)[1].rstrip(")")
        matching = [n for n in contained if char_name in n.get("title", "")]
        expected = 3 if name_to_id.get(char_name) in speaking_in_seq1 else 2
        assert len(matching) == expected, \
            f"expected {expected} nodes inside {g['title']}, found {len(matching)}: {[n.get('title') for n in contained]}"
    print("[OK] each character's nodes (3 if speaking, 2 if silent this sequence) fall inside its own group box")

    # Spot-check duration
    for node in wf2["nodes"]:
        if node.get("title") == "Float (Duration)":
            assert node["widgets_values"][0] == 6.0
            print("[OK] duration set correctly")

    # ---- Compiled prompt checks ----
    prompt_node = next(n for n in wf2["nodes"] if n.get("title") == "Input Text (Prompt)")
    prompt_text = prompt_node["widgets_values"][0]
    print("\n--- compiled prompt (sequence 2, chained, 2 characters) ---")
    print(prompt_text)
    print("--- end compiled prompt ---\n")

    for section in ["subject_definitions:", "summary:", "retention_analysis:",
                     "detailed_description:", "overall_soundscape:", "non_diegetic_music:"]:
        assert section in prompt_text, f"missing section {section}"
    assert "is the first frame of [Shot 1], continuing directly" in prompt_text
    assert "The shot begins from <Picture" in prompt_text
    assert "<Subject" in prompt_text and "knocks the phaser from" in prompt_text
    assert "<Subject 2>" in prompt_text, "Grant's name should be replaced by his bare Subject tag"
    assert "Grant knocks" not in prompt_text and "Grant staggers" not in prompt_text, \
        "character names should be REPLACED by the bare tag in action text, not left in place"
    assert "<Subject 3> (S1) says, <d>[English] You should have listened.</d>" in prompt_text
    assert "A tense, low string ostinato" in prompt_text
    assert "pulsing blue accent lighting" in prompt_text
    print("[OK] compiled prompt contains all six sections and correct tagging/dialogue")

    # ---- Multi-shot checks: 3 beats -> [Shot 1], [Shot 2], [Shot 3] ----
    assert "[Shot 1]" in prompt_text and "[Shot 2]" in prompt_text and "[Shot 3]" in prompt_text
    # Shot 1 never carries a timestamp
    shot1_line = [l for l in prompt_text.splitlines() if l.startswith("[Shot 1]")][0]
    assert "At " not in shot1_line, f"[Shot 1] should never have a timestamp: {shot1_line}"
    # Shot 2 and 3 use each beat's own explicit timestamp verbatim
    shot2_line = [l for l in prompt_text.splitlines() if l.startswith("[Shot 2]")][0]
    shot3_line = [l for l in prompt_text.splitlines() if l.startswith("[Shot 3]")][0]
    assert "At 00:02.500," in shot2_line, shot2_line
    assert "At 00:04.800," in shot3_line, shot3_line
    # retention_analysis should list the specific shots each subject appears in.
    # Grant is only explicitly present (spoken-to/mentioned) in shots 1 and 3 --
    # NOT shot 2, where only Zara speaks and Grant isn't named.
    grant_retention_line = [l for l in prompt_text.splitlines()
                             if l.startswith("<Subject 2>") and "appears in" in l][0]
    assert "[Shot 1]" in grant_retention_line and "[Shot 3]" in grant_retention_line, grant_retention_line
    assert "[Shot 2]" not in grant_retention_line, \
        f"Grant isn't mentioned in beat 2's dialogue-only text, shouldn't be listed there: {grant_retention_line}"
    print("[OK] every beat renders as its own [Shot N], timestamps verbatim, per-shot retention tracking correct")

    # ---- Documentation-adherence checks (from the guide audit) ----
    retention_section = prompt_text.split("retention_analysis:\n", 1)[1].split("\n\ndetailed_description:")[0]
    assert "(S1)" not in retention_section and "(S2)" not in retention_section, \
        f"retention_analysis must never contain (Sx) per the guide: {retention_section}"
    assert "appears throughout" not in prompt_text, \
        "should use 'appears in' consistently, matching the guide's own example, not 'appears throughout'"
    print("[OK] no (Sx) in retention_analysis, consistent 'appears in' wording")

    summary_line = [l for l in prompt_text.splitlines() if l.startswith("[")][0]
    assert summary_line.startswith("[keyframe completion"), summary_line
    assert "and the standoff turns physical." in summary_line
    assert "<Subject 2> confronts <Subject 3>" in summary_line, \
        f"character names in summary_premise should be replaced with bare Subject tags: {summary_line}"
    assert "Grant" not in summary_line and "Zara" not in summary_line, \
        f"summary must not introduce new labels (names) per the guide: {summary_line}"
    assert "single continuous shot" not in prompt_text, "stale/wrong claim should be gone"
    print("[OK] summary uses auto-computed bracket + scene.summary_premise with names tagged, no stale claim")

    assert "The target video has a futuristic sci-fi look with cool blue and neon" in prompt_text, \
        "detailed_description should open with the scene's style_opening, not generic filler"
    assert "maintains a consistent, naturalistic visual style matching the established scene" not in prompt_text
    print("[OK] detailed_description opens with the scene's chosen style, not generic filler")

    assert "The shot establishes <Subject 1>, a sleek starship corridor" in prompt_text, \
        "Shot 1 should prepend the setting's establishing sentence, reusing its subject_definitions description"
    print("[OK] setting's establishing sentence correctly prepended to [Shot 1]")

    # ---- Character first-appearance redescription: once, at first shot only ----
    shot1_text = [l for l in prompt_text.splitlines() if l.startswith("[Shot 1]")][0]
    assert "a man in his 30s with short dark hair" in shot1_text, \
        f"Grant's redescription should appear at his first appearance (Shot 1): {shot1_text}"
    assert "a woman with silver-streaked hair" in shot1_text, \
        f"Zara's redescription should appear at her first appearance (Shot 1): {shot1_text}"
    # Zara speaks later in the sequence, so her Shot-1 introduction should carry (S1)
    assert "<Subject 3> (S1), a woman with silver-streaked hair" in prompt_text, \
        "a character who speaks later in the sequence should get (Sx) on their intro sentence too"
    # Neither redescription should repeat in later shots -- just the bare tag
    shot3_text = [l for l in prompt_text.splitlines() if l.startswith("[Shot 3]")][0]
    assert "short dark hair" not in shot3_text and "silver-streaked hair" not in shot3_text, \
        f"redescription should NOT repeat in later shots: {shot3_text}"
    assert prompt_text.count("a man in his 30s with short dark hair") == 2, \
        "the description should appear exactly twice total: once in subject_definitions, once at first appearance"
    print("[OK] character redescription happens once, at first appearance only, with (Sx) when they speak later")

    # ---- Direct unit check: first appearance in a LATER shot, not Shot 1 ----
    late_setting = Setting.create("Empty Room", visual_description="a bare white room")
    late_char = Character.create("Nadia", appearance_description="a tall woman with a red coat")
    late_seq = Sequence(id="seq_late", index=0, duration=8.0, beats=[
        Beat.create("action", text="The room sits empty and quiet."),
        Beat.create("action", text="Nadia steps into the doorway."),
    ])
    late_scene = Scene.create("Late Entrance", setting_id=late_setting.id)
    late_prompt = compile_prompt(
        setting=late_setting, characters=[late_char], sequence=late_seq, scene=late_scene,
        character_slots={}, attire_by_char_id={},
    )
    shot1_late = [l for l in late_prompt.splitlines() if l.startswith("[Shot 1]")][0]
    shot2_late = [l for l in late_prompt.splitlines() if l.startswith("[Shot 2]")][0]
    assert "tall woman with a red coat" not in shot1_late, \
        f"Nadia isn't in Shot 1's text, shouldn't be introduced there: {shot1_late}"
    assert "tall woman with a red coat" in shot2_late, \
        f"Nadia's first appearance is Shot 2, should be introduced there: {shot2_late}"
    print("[OK] first-appearance introduction correctly attaches to a later shot, not always Shot 1")

    # --- Test 3: character with no attire chosen at all -> no Attire node ---
    no_attire_char = Character.create(
        "Extra", face_image="/abs/path/extra_face.png", voice_audio="/abs/path/extra_voice.wav",
        attire_options=[],  # nothing to choose from
    )
    seq2 = Sequence(id="seq_0003", index=0, duration=8.0, beats=[
        Beat.create("action", text="Extra watches from the doorway."),
    ])
    wf3, _ = generate_sequence_workflow(
        template, setting=setting, characters=[no_attire_char], sequence=seq2, scene=scene,
        attire_by_char_id={},  # no entry at all for this character
        previous_output_path=None,
    )
    check_workflow_integrity(wf3)
    assert not any(str(n.get("title", "")).startswith("Attire: Extra") for n in wf3["nodes"]), \
        "expected no Attire node for a character with no attire chosen"
    print("[OK] character with no attire chosen gets no Attire node")

    # --- Test 4: character with no voice_audio at all -> no Voice node, no <Audio N> line ---
    silent_char = Character.create(
        "Silent Guy", face_image="/abs/path/silent_face.png", voice_audio="",  # no voice reference
        attire_options=[AttireOption.create(label="D", image_path="/abs/path/silent_attire.png", is_default=True)],
    )
    seq3 = Sequence(id="seq_0004", index=0, duration=8.0, beats=[
        Beat.create("action", text="Silent Guy enters without a word."),
    ])
    wf4, _ = generate_sequence_workflow(
        template, setting=setting, characters=[silent_char], sequence=seq3, scene=scene,
        attire_by_char_id={silent_char.id: silent_char.default_attire()},
        previous_output_path=None,
    )
    check_workflow_integrity(wf4)
    assert not any(str(n.get("title", "")).startswith("Voice: Silent Guy") for n in wf4["nodes"]), \
        "expected no Voice node for a character with no voice_audio"
    silent_prompt = next(n for n in wf4["nodes"] if n.get("title") == "Input Text (Prompt)")["widgets_values"][0]
    assert "<Audio" not in silent_prompt, \
        f"expected no <Audio N> reference at all for a voiceless character: {silent_prompt}"
    print("[OK] character with no voice_audio gets no Voice node and no <Audio N> prompt line")

    # --- Test 5: exceeding the audio-reference cap (3) raises cleanly --
    # needs characters who actually SPEAK, since voice is now only wired for
    # speakers -- a silent cast never touches the audio cap at all (Test 6).
    many_chars = []
    many_attire = {}
    for i in range(5):
        c = Character.create(f"Cast{i}", face_image=f"/abs/face{i}.png", voice_audio=f"/abs/voice{i}.wav",
                              attire_options=[AttireOption.create(label="D", image_path=f"/abs/attire{i}.png",
                                                                   is_default=True)])
        many_chars.append(c)
        many_attire[c.id] = c.default_attire()
    # 4 of the 5 speak -- exceeds the 3-audio cap
    seq4 = Sequence(id="seq_0005", index=0, duration=8.0, beats=[
        Beat.create("dialogue", character_id=many_chars[0].id, line="One."),
        Beat.create("dialogue", character_id=many_chars[1].id, line="Two."),
        Beat.create("dialogue", character_id=many_chars[2].id, line="Three."),
        Beat.create("dialogue", character_id=many_chars[3].id, line="Four."),
    ])
    try:
        generate_sequence_workflow(template, setting=setting, characters=many_chars, sequence=seq4,
                                    scene=scene, attire_by_char_id=many_attire)
        raise AssertionError("expected TemplateEngineError for exceeding the audio-reference cap")
    except TemplateEngineError as e:
        assert "ref_audios" in str(e) and "hard cap of 3" in str(e), f"unexpected error message: {e}"
    print("[OK] exceeding MiniMax H3's reference caps raises TemplateEngineError instead of silently overflowing")

    # --- Test 6: the actual fix -- a cast with voice_audio set on everyone
    # doesn't hit the audio cap as long as only some of them speak in THIS
    # sequence, and non-speaking characters correctly get no Voice node at
    # all. Uses its own smaller cast so this only exercises audio-slot
    # economy, not the (unrelated) image cap from Test 5's 5-character cast. ---
    small_cast = []
    small_attire = {}
    for i in range(3):
        c = Character.create(f"Small{i}", face_image=f"/abs/sface{i}.png", voice_audio=f"/abs/svoice{i}.wav",
                              attire_options=[AttireOption.create(label="D", image_path=f"/abs/sattire{i}.png",
                                                                   is_default=True)])
        small_cast.append(c)
        small_attire[c.id] = c.default_attire()
    seq5 = Sequence(id="seq_0006", index=0, duration=8.0, beats=[
        Beat.create("dialogue", character_id=small_cast[0].id, line="Only one speaks."),
        Beat.create("action", text="The rest just stand there."),
    ])
    wf5, _ = generate_sequence_workflow(template, setting=setting, characters=small_cast, sequence=seq5,
                                         scene=scene, attire_by_char_id=small_attire)
    check_workflow_integrity(wf5)
    voice_node_titles = [n["title"] for n in wf5["nodes"] if str(n.get("title", "")).startswith("Voice:")]
    assert voice_node_titles == ["Voice: Small0"], \
        f"expected only the one speaking character to get a Voice node, got: {voice_node_titles}"
    print("[OK] a cast with voice_audio set on everyone doesn't hit the audio cap when only some of them speak "
          "this sequence -- non-speakers correctly get no Voice node at all")

    # dump for manual inspection
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "manifests")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "_selftest_wf2.json"), "w") as f:
        json.dump(wf2, f, indent=2)
    print("Wrote sample output to data/manifests/_selftest_wf2.json for inspection")


if __name__ == "__main__":
    run()
