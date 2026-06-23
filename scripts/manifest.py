"""Manifest helpers for resumable local course collection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ManifestItem:
    id: str
    kind: str
    title: str
    url: str = ""
    source_frame_url: str = ""
    status: str = "pending"
    local_path: str = ""
    sha256: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


class CourseManifest:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: dict[str, ManifestItem] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for raw in data.get("items", []):
            item = ManifestItem(**raw)
            self.items[item.id] = item

    def save(self) -> None:
        payload = {
            "schema_version": 1,
            "updated_at": utc_now(),
            "items": [asdict(item) for item in sorted(self.items.values(), key=lambda x: (x.kind, x.title, x.id))],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        tmp.replace(self.path)

    def stable_id(self, kind: str, title: str, url: str) -> str:
        return sha1_text(f"{kind}\n{title}\n{url}")[:20]

    def upsert(self, kind: str, title: str, url: str = "", source_frame_url: str = "", metadata: dict[str, Any] | None = None) -> ManifestItem:
        item_id = self.stable_id(kind, title, url)
        existing = self.items.get(item_id)
        now = utc_now()
        if existing:
            existing.title = title or existing.title
            existing.url = url or existing.url
            existing.source_frame_url = source_frame_url or existing.source_frame_url
            if metadata:
                existing.metadata.update(metadata)
            existing.updated_at = now
            return existing
        item = ManifestItem(
            id=item_id,
            kind=kind,
            title=title,
            url=url,
            source_frame_url=source_frame_url,
            metadata=metadata or {},
        )
        self.items[item_id] = item
        return item

    def mark_done(self, item_id: str, local_path: Path, base_dir: Path) -> None:
        item = self.items[item_id]
        item.status = "done"
        item.local_path = str(local_path.resolve().relative_to(base_dir.resolve())) if local_path.exists() else str(local_path)
        item.sha256 = sha256_file(local_path) if local_path.exists() and local_path.is_file() else ""
        item.error = ""
        item.updated_at = utc_now()

    def mark_failed(self, item_id: str, error: str) -> None:
        item = self.items[item_id]
        item.status = "failed"
        item.error = str(error)[:2000]
        item.updated_at = utc_now()

    def by_kind(self, kind: str) -> list[ManifestItem]:
        return [item for item in self.items.values() if item.kind == kind]
