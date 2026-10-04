import os
import re
import tempfile
from pathlib import Path

from nearscapes.config import get_settings


class LocalStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().storage_root
        self.sources = self.root / "sources"
        self.cache = self.root / "cache"
        self.exports = self.root / "exports"
        self.tmp = self.root / "tmp"
        for path in (self.sources, self.cache, self.exports, self.tmp):
            path.mkdir(parents=True, exist_ok=True)

    def source_path(self, sha256: str) -> Path:
        directory = self.sources / sha256[:2] / sha256
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "source"

    def cache_dir(self, sha256: str) -> Path:
        directory = self.cache / sha256[:2] / sha256
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def export_dir(self, sha256: str) -> Path:
        directory = self.exports / sha256[:2] / sha256
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def audacity_project_path(self, sha256: str, filename: str) -> Path:
        stem = Path(filename).stem or "recording"
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .") or "recording"
        return self.export_dir(sha256) / f"{safe}.analysis.aup3"

    def create_upload_temp(self) -> Path:
        """Create upload scratch space on the same filesystem as persistent storage."""
        fd, name = tempfile.mkstemp(prefix="nearscapes-upload-", dir=self.tmp)
        os.close(fd)
        return Path(name)
