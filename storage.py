"""
storage.py - Persistent storage for AI Social Bot Simulator.

All posts are appended to a single JSON file atomically so concurrent
writers don't corrupt the file.  A file-lock strategy using a temp-file
rename is used instead of an OS-level lock library so no extra deps are needed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from config import cfg

logger = logging.getLogger(__name__)

# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class Post:
    bot_id: str
    name: str
    content: str
    image_path: str
    timestamp: str          # ISO-8601

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Post":
        return cls(**d)


# ── Storage engine ────────────────────────────────────────────────────────────

class PostStorage:
    """
    Thread-safe JSON storage for posts.

    Uses an in-process lock + atomic write (write-to-temp, then rename)
    to keep the JSON file consistent even under concurrent usage.
    """

    def __init__(self, filepath: str = cfg.POSTS_FILE):
        self._path = Path(filepath)
        self._lock = threading.Lock()
        self._ensure_file()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _ensure_file(self):
        if not self._path.exists():
            self._path.write_text("[]", encoding="utf-8")
            logger.debug("Created posts file: %s", self._path)

    def _read_all(self) -> List[dict]:
        try:
            text = self._path.read_text(encoding="utf-8")
            return json.loads(text) if text.strip() else []
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read posts file: %s", exc)
            return []

    def _write_all(self, posts: List[dict]):
        """Atomic write: write to temp then rename."""
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(posts, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("Failed to write posts file: %s", exc)
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise

    # ── Public API ────────────────────────────────────────────────────────────

    def save_post(self, post: Post) -> None:
        """Append a single post to the JSON file (thread-safe)."""
        with self._lock:
            posts = self._read_all()
            posts.append(post.to_dict())
            self._write_all(posts)
        logger.debug("Saved post for bot %s", post.bot_id)

    def save_posts_bulk(self, new_posts: List[Post]) -> None:
        """Append multiple posts in one write (more efficient for batches)."""
        if not new_posts:
            return
        with self._lock:
            posts = self._read_all()
            posts.extend(p.to_dict() for p in new_posts)
            self._write_all(posts)
        logger.info("Bulk-saved %d posts", len(new_posts))

    def load_all(self) -> List[Post]:
        """Return all stored posts."""
        with self._lock:
            raw = self._read_all()
        return [Post.from_dict(d) for d in raw]

    def count(self) -> int:
        with self._lock:
            return len(self._read_all())

    def clear(self) -> None:
        """Wipe all stored posts (useful for fresh runs)."""
        with self._lock:
            self._write_all([])
        logger.info("Posts file cleared.")

    def get_by_bot(self, bot_id: str) -> List[Post]:
        all_posts = self.load_all()
        return [p for p in all_posts if p.bot_id == bot_id]

    def stats(self) -> dict:
        all_posts = self.load_all()
        unique_bots = len({p.bot_id for p in all_posts})
        return {
            "total_posts": len(all_posts),
            "unique_bots": unique_bots,
            "storage_file": str(self._path.resolve()),
            "file_size_kb": round(self._path.stat().st_size / 1024, 2) if self._path.exists() else 0,
        }


# ── Module-level singleton ─────────────────────────────────────────────────────

_storage: Optional[PostStorage] = None


def get_storage() -> PostStorage:
    global _storage
    if _storage is None:
        _storage = PostStorage()
    return _storage
