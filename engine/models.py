"""
Plain-dataclass data models for Scene Forge.

Kept dependency-free (no pydantic) so this module can be reused as-is
from a future CLI without dragging Flask/pydantic along.
"""
from dataclasses import dataclass, field, asdict, fields
from typing import Optional
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _known_fields(cls, d: dict) -> dict:
    """
    Drop any keys in `d` that aren't fields on `cls`. Lets old JSON files on
    disk (saved before a model change) load without crashing -- stale keys
    are silently ignored rather than raising a TypeError on construction.
    """
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in valid}


@dataclass
class AttireOption:
    """One selectable outfit for a character. A character can have several;
    exactly one should be marked as_default so scenes have a sane fallback
    when a casting doesn't explicitly pick one."""
    id: str
    label: str = ""
    image_path: str = ""
    description: str = ""   # optional, folded into the Subject definition when worn
    is_default: bool = False

    @staticmethod
    def create(label="", **kwargs) -> "AttireOption":
        return AttireOption(id=new_id("attire"), label=label, **kwargs)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return AttireOption(**_known_fields(AttireOption, d))


@dataclass
class Character:
    id: str
    name: str
    face_image: str = ""      # absolute path to a reference face image
    voice_audio: str = ""     # absolute path to a reference voice clip
    category: str = ""        # free-form, user-defined grouping (e.g. "Protagonists",
                               # "Season 2 Cast") -- not a fixed vocabulary, just whatever
                               # categories the user has typed for other characters so far
    attire_options: list = field(default_factory=list)  # list of AttireOption
    appearance_description: str = ""  # visual details for subject_definitions:
                                       # hair, build, face -- NOT clothing (that
                                       # lives on the chosen AttireOption instead)
    properties: dict = field(default_factory=dict)  # free-form (personality, notes, etc.)

    @staticmethod
    def create(name, **kwargs) -> "Character":
        return Character(id=new_id("char"), name=name, **kwargs)

    def default_attire(self):
        for a in self.attire_options:
            if a.is_default:
                return a
        return self.attire_options[0] if self.attire_options else None

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        d = dict(d)
        # Migrate the old single `attire_image` field (pre-attire-options) into
        # a one-entry attire_options list, so nothing on disk from before this
        # change gets silently dropped.
        legacy_attire_image = d.pop("attire_image", None)
        raw_options = d.get("attire_options")
        if raw_options:
            options = [AttireOption.from_dict(a) if not isinstance(a, AttireOption) else a
                       for a in raw_options]
        elif legacy_attire_image:
            options = [AttireOption.create(label="Default", image_path=legacy_attire_image,
                                            is_default=True)]
        else:
            options = []
        d["attire_options"] = options
        return Character(**_known_fields(Character, d))


@dataclass
class Setting:
    id: str
    name: str
    reference_image: str = ""   # absolute path to a reference image of the location
    category: str = ""          # free-form, user-defined grouping, independent of
                                 # Character's category list (own namespace, not shared)
    visual_description: str = ""     # visual details for subject_definitions
    soundscape_description: str = "" # prose for overall_soundscape
    properties: dict = field(default_factory=dict)  # free-form, anything else

    @staticmethod
    def create(name, **kwargs) -> "Setting":
        return Setting(id=new_id("set"), name=name, **kwargs)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return Setting(**_known_fields(Setting, d))


@dataclass
class Beat:
    """
    One unit of a sequence's action: either free-form descriptive text, or a
    single line of dialogue spoken by one of the scene's characters.
    """
    id: str
    kind: str                   # "action" | "dialogue"
    text: str = ""               # used when kind == "action"
    character_id: str = ""       # used when kind == "dialogue"
    line: str = ""               # used when kind == "dialogue"
    language: str = "English"    # used when kind == "dialogue"
    delivery_preset: str = ""    # used when kind == "dialogue": preset key, or "custom", or ""
    delivery: str = ""           # used when kind == "dialogue": the resolved phrase that
                                  # actually gets woven into the compiled prompt text
    timestamp: str = ""          # MM:SS.mmm offset into the sequence where this beat's
                                  # shot occurs. Ignored for the sequence's first beat
                                  # (always rendered as plain "[Shot 1]" per the guide).
                                  # Free text, not validated -- garbage in, garbage out.

    @staticmethod
    def create(kind, **kwargs) -> "Beat":
        return Beat(id=new_id("beat"), kind=kind, **kwargs)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return Beat(**_known_fields(Beat, d))


@dataclass
class Sequence:
    id: str
    index: int                  # order within the scene, 0-based
    duration: float = 8.0       # seconds, expected 5-10
    beats: list = field(default_factory=list)  # ordered list of Beat
    status: str = "pending"     # pending | generated | rendered
    output_video_path: str = ""  # resolved absolute path once rendered (for chaining)

    def to_dict(self):
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d):
        beats = [Beat.from_dict(b) if not isinstance(b, Beat) else b
                 for b in d.get("beats", [])]
        d = {**d, "beats": beats}
        return Sequence(**_known_fields(Sequence, d))


@dataclass
class CharacterCasting:
    """One character's presence in a scene, plus which attire they're wearing
    for the whole scene. attire_id == "" means 'use that character's default'."""
    character_id: str
    attire_id: str = ""

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return CharacterCasting(**_known_fields(CharacterCasting, d))


@dataclass
class Scene:
    id: str
    name: str
    setting_id: str = ""
    character_castings: list = field(default_factory=list)  # list of CharacterCasting
    non_diegetic_music: str = ""   # prose description of background score, if any
    summary_premise: str = ""      # narrative sentence for the guide's `summary:` section
                                    # (the [task-type] bracket is auto-computed and prepended;
                                    # this is just the plain-English premise after it)
    style_preset: str = ""         # preset key, or "custom", or "" (no style opening set)
    style_opening: str = ""        # resolved 1-2 sentence visual style, prepended before
                                    # [Shot 1] in detailed_description, per the guide
    prompt_format: str = "lean"    # "full" (six-section rewrite-guide format) or "lean"
                                    # (three-field base-guide-style format). See
                                    # prompt_compiler.compile_prompt / compile_lean_prompt.
    sequences: list = field(default_factory=list)        # list of Sequence

    @staticmethod
    def create(name, **kwargs) -> "Scene":
        return Scene(id=new_id("scene"), name=name, **kwargs)

    def to_dict(self):
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d):
        d = dict(d)
        seqs = [Sequence.from_dict(s) if not isinstance(s, Sequence) else s
                for s in d.get("sequences", [])]
        d["sequences"] = seqs

        # Migrate the old flat `character_ids` list (pre-attire-casting) into
        # character_castings with no attire preference (resolves to each
        # character's default attire at generation time).
        legacy_ids = d.pop("character_ids", None)
        raw_castings = d.get("character_castings")
        if raw_castings:
            castings = [CharacterCasting.from_dict(c) if not isinstance(c, CharacterCasting) else c
                        for c in raw_castings]
        elif legacy_ids:
            castings = [CharacterCasting(character_id=cid) for cid in legacy_ids]
        else:
            castings = []
        d["character_castings"] = castings

        return Scene(**_known_fields(Scene, d))
