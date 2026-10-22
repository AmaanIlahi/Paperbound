import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

from paperbound import config


class Storage(Protocol):
    def save(self, key: str, data: bytes) -> None: ...
    def open(self, key: str) -> BinaryIO: ...
    def url(self, key: str) -> str: ...
    def delete_prefix(self, prefix: str) -> None: ...


class LocalStorage:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir.resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.base_dir / key).resolve()
        if not path.is_relative_to(self.base_dir):
            raise ValueError(f"invalid storage key: {key}")
        return path

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def url(self, key: str) -> str:
        return f"/files/{key}"

    def delete_prefix(self, prefix: str) -> None:
        """Remove a directory of files (everything stored for one document)."""
        path = self._path(prefix)
        if path != self.base_dir and path.is_dir():
            shutil.rmtree(path)


def get_storage() -> Storage:
    return LocalStorage(config.STORAGE_DIR)
