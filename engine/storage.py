"""
Minimal JSON-file storage. Each entity is one file, named

    <prefix>_<slug-of-name>_<hash>.json      e.g. "char_picard_3f9307.json"

-- human-readable on disk, while obj.id itself (e.g. "char_3f9307") stays
the permanent, unchanging primary key used everywhere else in the app (API
URLs, scene castings, location_id references, etc.). Renaming an entity
renames its file to match on the next save; nothing that references it by
id needs to change, because lookups are done by matching the id's hash
suffix, not the exact filename.

This also means old files already on disk under the pre-rename scheme
(just "<id>.json", e.g. "char_3f930786.json") keep loading correctly with
no migration needed -- that filename already ends with "_<hash>.json", so
the suffix match finds it, and the next save() naturally upgrades it to
the new "<prefix>_<slug>_<hash>.json" form.

No database, no locking -- this is a single-user local tool.
"""
import json
import os
import re


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return slug or "untitled"


class JsonStore:
    def __init__(self, directory: str, model_cls):
        self.directory = directory
        self.model_cls = model_cls
        os.makedirs(self.directory, exist_ok=True)

    @staticmethod
    def _split_id(obj_id: str):
        """ids are "<prefix>_<hash>", e.g. "char_3f9307" -> ("char", "3f9307").
        Prefixes are always a single word (no underscores), so splitting on
        the first "_" is safe and exact."""
        prefix, _, hash_part = obj_id.partition("_")
        return prefix, hash_part

    def _existing_path(self, obj_id: str):
        """Finds the current on-disk filename for obj_id, regardless of what
        name is (or isn't) baked into it, by matching the id's hash suffix
        -- the one part of the filename that never changes. Returns None if
        nothing matches."""
        _, hash_part = self._split_id(obj_id)
        if not hash_part:
            return None
        suffix = f"_{hash_part}.json"
        for fname in os.listdir(self.directory):
            if fname.endswith(suffix):
                return os.path.join(self.directory, fname)
        return None

    def _target_path(self, obj) -> str:
        """The filename obj SHOULD have right now, based on its current
        name -- <prefix>_<slug>_<hash>.json."""
        prefix, hash_part = self._split_id(obj.id)
        slug = _slugify(getattr(obj, "name", ""))
        return os.path.join(self.directory, f"{prefix}_{slug}_{hash_part}.json")

    def save(self, obj) -> None:
        target = self._target_path(obj)
        existing = self._existing_path(obj.id)
        if existing and existing != target:
            # Name changed since the last save (or this is an old file still
            # under the pre-rename id-only naming) -- move it to the
            # up-to-date filename rather than leaving a stale name on disk.
            os.remove(existing)
        with open(target, "w") as f:
            json.dump(obj.to_dict(), f, indent=2)

    def load(self, obj_id: str):
        path = self._existing_path(obj_id)
        if not path:
            return None
        with open(path) as f:
            return self.model_cls.from_dict(json.load(f))

    def delete(self, obj_id: str) -> bool:
        path = self._existing_path(obj_id)
        if path:
            os.remove(path)
            return True
        return False

    def list_all(self):
        out = []
        for fname in sorted(os.listdir(self.directory)):
            if fname.endswith(".json"):
                with open(os.path.join(self.directory, fname)) as f:
                    out.append(self.model_cls.from_dict(json.load(f)))
        return out
