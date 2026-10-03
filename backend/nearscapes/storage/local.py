from pathlib import Path

from nearscapes.config import get_settings


class LocalStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().storage_root
        self.sources = self.root / "sources"
        self.cache = self.root / "cache"
        self.exports = self.root / "exports"
        for path in (self.sources, self.cache, self.exports):
            path.mkdir(parents=True, exist_ok=True)

    def source_path(self, sha256: str) -> Path:
        directory = self.sources / sha256[:2] / sha256
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "source"

    def cache_dir(self, sha256: str) -> Path:
        directory = self.cache / sha256[:2] / sha256
        directory.mkdir(parents=True, exist_ok=True)
        return directory
