"""
storage.py — Persistent storage for AI Social Bot Simulator.

Thread-safe JSON storage with atomic writes.  Posts are appended using a
write-to-temp-then-rename strategy that prevents corruption even under
concurrent access from multiple threads.

Design notes
------------
* Thread safety  : threading.Lock() guards every read-modify-write cycle.
* Atomic writes  : tmp-file rename means readers never see a partial write.
* Corruption protection : read errors fall back to an empty list; the bad
  file is preserved at <posts>.corrupt.<timestamp> for post-mortem review.
* Backward compatibility : Post.from_dict() silently ignores unknown fields
  and fills missing optional fields with defaults, so existing posts.json
  files from v1 load without errors.

Scalability note
----------------
For > 50 000 posts, migrate to SQLite (see ARCHITECTURE.md). The public API
of PostStorage is designed to be a drop-in replacement.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, asdict, fields as dc_fields
from pathlib import Path
from typing import Dict, List, Optional

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

    # ── Tayog upload result fields (populated after upload) ───────────────────
    tayog_post_id: Optional[str] = None
    tayog_upload_status: str = "pending"   # pending | success | failed | skipped
    tayog_upload_timestamp: Optional[str] = None

    # ── Image metadata (populated when image is available) ────────────────────
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    image_mime_type: Optional[str] = None
    image_file_size: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Post":
        """Tolerant constructor: ignores unknown keys, fills missing ones with defaults."""
        known = {f.name for f in dc_fields(cls)}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


# ── Storage engine ────────────────────────────────────────────────────────────

class PostStorage:
    """
    Thread-safe JSON storage for posts.

    Uses an in-process threading.Lock plus atomic rename to keep the JSON
    file consistent even under concurrent usage from multiple threads.
    """

    def __init__(self, filepath: str = cfg.POSTS_FILE):
        self._path = Path(filepath)
        self._lock = threading.Lock()
        self._ensure_file()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _ensure_file(self) -> None:
        if not self._path.exists():
            self._path.write_text("[]", encoding="utf-8")
            logger.debug("Created posts file: %s", self._path)

    def _read_all(self) -> List[dict]:
        """Read and parse the JSON file. On corruption, quarantines the file."""
        try:
            text = self._path.read_text(encoding="utf-8")
            if not text.strip():
                return []
            data = json.loads(text)
            if not isinstance(data, list):
                raise ValueError("posts file root is not a JSON array")
            return data
        except json.JSONDecodeError as exc:
            self._quarantine(reason=f"json_decode_error: {exc}")
            return []
        except OSError as exc:
            logger.error("Cannot read posts file %s: %s", self._path, exc)
            return []

    def _quarantine(self, reason: str) -> None:
        """Rename the corrupt file and start fresh so the simulator can continue."""
        ts = int(time.time())
        corrupt = self._path.with_name(f"{self._path.stem}.corrupt.{ts}{self._path.suffix}")
        try:
            shutil.copy2(self._path, corrupt)
            logger.error(
                "Corrupt posts file quarantined to %s (%s). Starting fresh.",
                corrupt, reason,
            )
        except OSError:
            pass
        # Reset to a valid empty file
        try:
            self._path.write_text("[]", encoding="utf-8")
        except OSError as exc:
            logger.critical("Cannot reset posts file after corruption: %s", exc)

    def _write_all(self, posts: List[dict]) -> None:
        """Atomic write: write to temp file then rename."""
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(posts, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("Failed to write posts file: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
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
        """Append multiple posts in one atomic write (more efficient for batches)."""
        if not new_posts:
            return
        with self._lock:
            posts = self._read_all()
            posts.extend(p.to_dict() for p in new_posts)
            self._write_all(posts)
        logger.info("Bulk-saved %d posts", len(new_posts))

    def update_post_tayog_result(
        self,
        bot_id: str,
        timestamp: str,
        tayog_post_id: Optional[str],
        tayog_upload_status: str,
        tayog_upload_timestamp: Optional[str],
    ) -> None:
        """
        Update the Tayog upload result fields for a specific post identified by
        bot_id + timestamp.

        Note: if the same bot generates multiple posts in the same second, the
        first match wins. This is acceptable for the current scale; at higher
        volumes a UUID per post is recommended.
        """
        with self._lock:
            posts = self._read_all()
            updated = False
            for p in posts:
                if p.get("bot_id") == bot_id and p.get("timestamp") == timestamp:
                    p["tayog_post_id"] = tayog_post_id
                    p["tayog_upload_status"] = tayog_upload_status
                    p["tayog_upload_timestamp"] = tayog_upload_timestamp
                    updated = True
                    break
            if not updated:
                logger.warning(
                    "update_post_tayog_result: no post found for bot=%s ts=%s",
                    bot_id, timestamp,
                )
            self._write_all(posts)

    def load_all(self) -> List[Post]:
        """Return all stored posts as Post objects."""
        with self._lock:
            raw = self._read_all()
        return [Post.from_dict(d) for d in raw]

    def count(self) -> int:
        """Return the number of stored posts without deserialising them."""
        with self._lock:
            return len(self._read_all())

    def clear(self) -> None:
        """Wipe all stored posts."""
        with self._lock:
            self._write_all([])
        logger.info("Posts file cleared.")

    def get_by_bot(self, bot_id: str) -> List[Post]:
        """Return all posts generated by a specific bot."""
        return [p for p in self.load_all() if p.bot_id == bot_id]

    def stats(self) -> Dict[str, object]:
        """Return aggregate statistics for the current posts file."""
        all_posts = self.load_all()
        unique_bots = len({p.bot_id for p in all_posts})
        status_counts: Dict[str, int] = {}
        for p in all_posts:
            status_counts[p.tayog_upload_status] = (
                status_counts.get(p.tayog_upload_status, 0) + 1
            )
        return {
            "total_posts": len(all_posts),
            "unique_bots": unique_bots,
            "tayog_uploaded": status_counts.get("success", 0),
            "tayog_failed": status_counts.get("failed", 0),
            "tayog_pending": status_counts.get("pending", 0),
            "tayog_skipped": status_counts.get("skipped", 0),
            "storage_file": str(self._path.resolve()),
            "file_size_kb": round(
                self._path.stat().st_size / 1024, 2
            ) if self._path.exists() else 0,
        }


# ── Module-level singleton ────────────────────────────────────────────────────

_storage: Optional[PostStorage] = None
_storage_lock = threading.Lock()


def get_storage() -> PostStorage:
    """Return the module-level PostStorage singleton (thread-safe initialisation)."""
    global _storage
    if _storage is None:
        with _storage_lock:
            if _storage is None:   # double-checked locking
                _storage = PostStorage()
    return _storage
