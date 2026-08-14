"""
Minimal JSON-file storage. Each entity is one file: <data_dir>/<id>.json
No database, no locking -- this is a single-user local tool.
"""
import json
import os


class JsonStore:
    def __init__(self, directory: str, model_cls):
        self.directory = directory
        self.model_cls = model_cls
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, obj_id: str) -> str:
        return os.path.join(self.directory, f"{obj_id}.json")

    def save(self, obj) -> None:
        with open(self._path(obj.id), "w") as f:
            json.dump(obj.to_dict(), f, indent=2)

    def load(self, obj_id: str):
        path = self._path(obj_id)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return self.model_cls.from_dict(json.load(f))

    def delete(self, obj_id: str) -> bool:
        path = self._path(obj_id)
        if os.path.exists(path):
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
