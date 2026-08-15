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
class Character:
    id: str
    name: str
    face_image: str = ""      # absolute path to a reference face image
    voice_audio: str = ""     # absolute path to a reference voice clip
    category: str = ""        # free-form, user-defined grouping (e.g. "Protagonists",
                               # "Season 2 Cast") -- not a fixed vocabulary, just whatever
                               # categories the user has typed for other characters so far
    appearance_description: str = ""  # visual details for subject_definitions: hair,
                                       # build, face, clothing -- anything you want the
                                       # model to preserve about this character's look,
                                       # as plain text (no separate attire reference image)
    properties: dict = field(default_factory=dict)  # free-form (personality, notes, etc.)

    @staticmethod
    def create(name, **kwargs) -> "Character":
        return Character(id=new_id("char"), name=name, **kwargs)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        # Old JSON on disk may still carry attire_options/attire_image from
        # before attire was removed as a concept entirely -- _known_fields
        # below silently drops them, same as any other retired field.
        return Character(**_known_fields(Character, d))


@dataclass
class Location:
    id: str
    name: str
    reference_image: str = ""   # absolute path to a reference image of the location
    category: str = ""          # free-form, user-defined grouping, independent of
                                 # Character's category list (own namespace, not shared)
    visual_description: str = ""     # visual details for subject_definitions
    soundscape_description: str = "" # prose for overall_soundscape
    properties: dict = field(default_factory=dict)  # free-form, anything else

    @staticmethod
    def create(name, **kwargs) -> "Location":
        return Location(id=new_id("loc"), name=name, **kwargs)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return Location(**_known_fields(Location, d))


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
    is_new_shot: bool = True     # whether this beat starts a NEW [Shot N], or folds into
                                  # the shot started by the beat before it (e.g. an action
                                  # beat immediately followed by a dialogue beat in the same
                                  # shot). Defaults True so old data (saved before this field
                                  # existed) keeps its original "every beat is its own shot"
                                  # behavior unchanged. A sequence's first beat is always
                                  # treated as starting shot 1 regardless of this flag --
                                  # there's no earlier shot for it to fold into.
    timestamp: str = ""          # MM:SS.mmm offset into the sequence where this beat's
                                  # shot occurs. Only meaningful when is_new_shot is True --
                                  # a beat that folds into the previous shot has no [Shot N]
                                  # header of its own to attach a timestamp to, and callers
                                  # are expected to keep it blank in that case (enforced
                                  # server-side in app.py, not here). Also ignored for the
                                  # sequence's first beat (always rendered as plain
                                  # "[Shot 1]" per the guide). Free text, not validated
                                  # (ordering, range, or format) -- garbage in, garbage out.

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
    """One character's presence in a scene, plus a couple of per-scene
    settings for how they're generated."""
    character_id: str
    include_voice: bool = True  # whether this character's voice_audio reference
                                 # gets wired in for this scene. Only matters when
                                 # they'd otherwise get a Voice node at all (they
                                 # need voice_audio set AND a dialogue beat in the
                                 # sequence) -- this is an additional opt-out on
                                 # top of those, e.g. for generating fresh TTS
                                 # instead of voice-cloning this character here.

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return CharacterCasting(**_known_fields(CharacterCasting, d))


@dataclass
class Scene:
    id: str
    name: str
    location_id: str = ""
    character_castings: list = field(default_factory=list)  # list of CharacterCasting
    non_diegetic_music: str = ""   # prose description of background score, if any
    summary_premise: str = ""      # narrative sentence for the guide's `summary:` section
                                    # (the [task-type] bracket is auto-computed and prepended;
                                    # this is just the plain-English premise after it)
    style_preset: str = ""         # preset key, or "custom", or "" (no style opening set)
    style_opening: str = ""        # resolved 1-2 sentence visual style, prepended before
                                    # [Shot 1] in detailed_description, per the guide
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

        # Migrate the old `setting_id` field name (pre-Location-rename) into
        # `location_id`, so scenes saved before this rename don't lose their
        # location on next load.
        if "location_id" not in d and "setting_id" in d:
            d["location_id"] = d.pop("setting_id")
        else:
            d.pop("setting_id", None)

        # Migrate the old flat `character_ids` list into character_castings
        # (a small wrapper around character_id, kept for potential future
        # per-scene per-character settings).
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
