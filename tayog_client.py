"""
tayog_client.py — Async Tayog API client for the AI Social Bot Simulator.

Handles:
  • Authentication via x_secrect field in postData JSON
  • Multipart form-data construction matching POST /api/posts/new/v1 exactly
  • Image metadata extraction (width, height, mime_type, file_size)
  • Exponential-backoff retry on transient failures (5xx + timeouts)
  • Semaphore-based concurrency throttling
  • Structured upload logging to tayog_upload.log
  • Upload analytics per session
  • Dry-run mode (validates payload without sending)

API contract (Tayog backend):
  POST /api/posts/new/v1
  Content-Type: multipart/form-data

  Fields:
    files[]         – binary image file(s)
    fileMetadata[]  – JSON: {"type": "image", "sortOrder": <int>}
    postData        – JSON: {"x_secrect": ..., "content": ..., "userId": ...}

  Constraints:
    max 7 files total, max 6 images, max 1 video

  Success response: HTTP 201
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False
    aiohttp = None  # type: ignore

try:
    from PIL import Image as PILImage
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from config import cfg

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_FILES_PER_POST = 7
MAX_IMAGES_PER_POST = 6
MAX_VIDEOS_PER_POST = 1


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ImageMetadata:
    """Metadata extracted from an image file."""
    width: Optional[int] = None
    height: Optional[int] = None
    mime_type: Optional[str] = None
    file_size: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UploadResult:
    """Result of a single Tayog upload attempt."""
    bot_id: str
    user_id: Optional[str] = None
    tayog_post_id: Optional[str] = None
    tayog_upload_status: str = "pending"   # pending | success | failed | skipped
    tayog_upload_timestamp: Optional[str] = None
    response_code: Optional[int] = None
    retry_count: int = 0
    latency_ms: float = 0.0
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UploadAnalytics:
    """Aggregate analytics for a full upload session."""
    total_attempted: int = 0
    total_success: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    total_retries: int = 0
    total_latency_ms: float = 0.0
    http_status_counts: Dict[int, int] = field(default_factory=dict)
    posts_per_bot: Dict[str, int] = field(default_factory=dict)
    uploads_per_batch: List[int] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_attempted == 0:
            return 0.0
        return round(self.total_success / self.total_attempted * 100, 2)

    @property
    def avg_latency_ms(self) -> float:
        if self.total_success == 0:
            return 0.0
        return round(self.total_latency_ms / self.total_success, 2)

    def record(self, result: UploadResult) -> None:
        """Merge a single UploadResult into aggregate stats."""
        self.total_attempted += 1
        self.total_retries += result.retry_count

        if result.response_code is not None:
            self.http_status_counts[result.response_code] = (
                self.http_status_counts.get(result.response_code, 0) + 1
            )

        if result.tayog_upload_status == "success":
            self.total_success += 1
            self.total_latency_ms += result.latency_ms
            self.posts_per_bot[result.bot_id] = (
                self.posts_per_bot.get(result.bot_id, 0) + 1
            )
        elif result.tayog_upload_status == "skipped":
            self.total_skipped += 1
        else:
            self.total_failed += 1

    def report(self) -> str:
        lines = [
            "=" * 55,
            "  TAYOG UPLOAD ANALYTICS",
            "=" * 55,
            f"  Attempted   : {self.total_attempted}",
            f"  Success     : {self.total_success}  ({self.success_rate}%)",
            f"  Failed      : {self.total_failed}",
            f"  Skipped     : {self.total_skipped}",
            f"  Total Retry : {self.total_retries}",
            f"  Avg Latency : {self.avg_latency_ms} ms",
            "-" * 55,
            "  HTTP Status Counts:",
        ]
        for code, count in sorted(self.http_status_counts.items()):
            lines.append(f"    HTTP {code}  →  {count}")
        lines.append("-" * 55)
        lines.append("  Posts per bot (top 10):")
        top_bots = sorted(self.posts_per_bot.items(), key=lambda x: -x[1])[:10]
        for bot_id, count in top_bots:
            short = bot_id[:12]
            lines.append(f"    {short}…  →  {count} post(s)")
        if self.uploads_per_batch:
            lines.append("-" * 55)
            lines.append("  Uploads per batch:")
            for i, count in enumerate(self.uploads_per_batch, 1):
                lines.append(f"    Batch {i:>3}  →  {count}")
        lines.append("=" * 55)
        return "\n".join(lines)


# ── Image metadata extractor ──────────────────────────────────────────────────

def extract_image_metadata(image_path: str) -> ImageMetadata:
    """
    Extract width, height, mime_type, and file_size from an image file.
    Falls back gracefully if PIL is unavailable or the file is missing.
    """
    meta = ImageMetadata()
    path = Path(image_path)

    if not path.exists():
        logger.warning("Image file not found for metadata extraction: %s", image_path)
        return meta

    meta.file_size = path.stat().st_size
    mime_type, _ = mimetypes.guess_type(str(path))
    meta.mime_type = mime_type or "image/png"

    if _HAS_PIL:
        try:
            with PILImage.open(path) as img:
                meta.width, meta.height = img.size
        except Exception as exc:
            logger.debug("PIL could not read image %s: %s", image_path, exc)

    return meta


# ── Bot → Tayog user mapping ──────────────────────────────────────────────────

class BotUserMapping:
    """
    Loads and validates the bot_id → tayogUserId mapping from
    bot_user_mapping.json.

    Startup checks:
    - File exists and is valid JSON
    - Root is a JSON object (dict)
    - No placeholder values remain
    - Comment keys (starting with '_') are ignored
    """

    _PLACEHOLDER = "TAYOG_USER_ID_HERE"

    def __init__(self, mapping_file: str = "bot_user_mapping.json"):
        self._path = Path(mapping_file)
        self._map: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning(
                "bot_user_mapping.json not found at %s. "
                "All Tayog uploads will be skipped.",
                self._path.resolve(),
            )
            return

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("bot_user_mapping.json is not valid JSON: %s", exc)
            return
        except OSError as exc:
            logger.error("Cannot read bot_user_mapping.json: %s", exc)
            return

        if not isinstance(raw, dict):
            logger.error("bot_user_mapping.json must be a JSON object at the root.")
            return

        # Filter comment keys (prefixed with '_') and placeholder values
        placeholder_keys: list[str] = []
        for k, v in raw.items():
            if k.startswith("_"):
                continue   # comment key
            if str(v) == self._PLACEHOLDER:
                placeholder_keys.append(k)
                continue
            self._map[str(k)] = str(v)

        if placeholder_keys:
            logger.warning(
                "bot_user_mapping.json has %d placeholder(s) — "
                "replace TAYOG_USER_ID_HERE for: %s",
                len(placeholder_keys),
                ", ".join(placeholder_keys[:10]),
            )

        logger.info(
            "Loaded %d bot→user mappings from %s",
            len(self._map), self._path,
        )

    def get_user_id(self, bot_id: str) -> Optional[str]:
        uid = self._map.get(bot_id)
        if uid is None:
            logger.warning(
                "No Tayog userId mapped for bot_id=%s — upload skipped.", bot_id
            )
        return uid

    def all_mappings(self) -> Dict[str, str]:
        return dict(self._map)

    def validate_bots(self, bot_ids: List[str]) -> Dict[str, bool]:
        """Return a dict of bot_id → has_mapping for a list of bot IDs."""
        return {bid: bid in self._map for bid in bot_ids}

    @property
    def mapped_count(self) -> int:
        return len(self._map)


# ── Upload logger ─────────────────────────────────────────────────────────────

class UploadLogger:
    """
    Structured logger that writes one JSON-line per upload event to
    tayog_upload.log.

    Log fields per line:
    timestamp, bot_id, user_id, status, http_code, post_id,
    retries, latency_ms, error
    """

    def __init__(self, log_file: str = "tayog_upload.log"):
        self._logger = logging.getLogger("tayog.upload")
        if not self._logger.handlers:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(fh)
            self._logger.setLevel(logging.DEBUG)
            self._logger.propagate = False

    def log_result(self, result: UploadResult) -> None:
        """Write a structured JSON log line for an upload result."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bot_id": result.bot_id,
            "user_id": result.user_id,
            "status": result.tayog_upload_status,
            "http_code": result.response_code,
            "post_id": result.tayog_post_id,
            "retries": result.retry_count,
            "latency_ms": round(result.latency_ms, 2),
            "error": result.error_message,
        }
        line = json.dumps(record, ensure_ascii=False)
        if result.tayog_upload_status == "success":
            self._logger.info(line)
        elif result.tayog_upload_status == "skipped":
            self._logger.info(line)
        else:
            self._logger.error(line)


# ── Payload validator ─────────────────────────────────────────────────────────

def _validate_payload(
    user_id: str,
    content: str,
    secret_key: str,
    image_paths: List[str],
) -> List[str]:
    """
    Validate a Tayog payload before sending. Returns a list of error strings.
    An empty list means the payload is valid.
    """
    errors: list[str] = []

    if not user_id:
        errors.append("userId is required")
    if not secret_key:
        errors.append("x_secrect (secret key) is required")
    if not content or not content.strip():
        errors.append("content must not be empty")

    valid_images = [p for p in image_paths if Path(p).exists()]
    missing = [p for p in image_paths if not Path(p).exists()]
    if missing:
        errors.append(f"Image files not found: {missing}")

    if len(image_paths) > MAX_IMAGES_PER_POST:
        errors.append(
            f"Too many images: {len(image_paths)} > max {MAX_IMAGES_PER_POST}"
        )
    if len(valid_images) + len(image_paths) > MAX_FILES_PER_POST:
        errors.append(
            f"Total files exceed max {MAX_FILES_PER_POST}"
        )

    return errors


# ── Tayog API client ──────────────────────────────────────────────────────────

class TayogClient:
    """
    Async client for publishing posts to Tayog's POST /api/posts/new/v1 endpoint.

    Features
    --------
    - Multipart form-data construction matching the Tayog API contract exactly
    - Bot → Tayog user ID resolution via BotUserMapping
    - Exponential-backoff retry on 5xx and timeout errors
    - asyncio.Semaphore throttling (MAX_CONCURRENT_UPLOADS)
    - Per-upload structured JSON logging to tayog_upload.log
    - Session-level UploadAnalytics
    - Dry-run mode (builds and validates payload, does not send)
    - SSL enabled by default (set ssl=False for local dev only)
    """

    # HTTP status codes that should trigger a retry
    RETRYABLE_STATUSES = {500, 502, 503, 504, 429}

    def __init__(
        self,
        base_url: str,
        secret_key: str,
        mapping: BotUserMapping,
        max_retries: int = 4,
        max_concurrent: int = 3,
        dry_run: bool = False,
        session: Optional["aiohttp.ClientSession"] = None,
    ):
        if not _HAS_AIOHTTP:
            raise ImportError(
                "aiohttp is required for TayogClient. Install it: pip install aiohttp"
            )

        self._base_url = base_url.rstrip("/")
        self._secret = secret_key
        self._mapping = mapping
        self._max_retries = max_retries
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._dry_run = dry_run
        self._session = session
        self._owns_session = session is None
        self._analytics = UploadAnalytics()
        self._upload_logger = UploadLogger(cfg.TAYOG_UPLOAD_LOG)

    # ── Session lifecycle ─────────────────────────────────────────────────────

    async def __aenter__(self) -> "TayogClient":
        if self._owns_session:
            connector = aiohttp.TCPConnector(
                limit=10,
                ssl=True,         # always use SSL in production
                enable_cleanup_closed=True,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=60, connect=10),
            )
        return self

    async def __aexit__(self, *_) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # ── Public API ────────────────────────────────────────────────────────────

    async def upload_post(
        self,
        bot_id: str,
        content: str,
        image_path: Optional[str] = None,
    ) -> UploadResult:
        """
        Upload a single post for the given bot.

        Resolves the Tayog userId from the mapping, validates the payload,
        and sends a multipart POST with retries on transient failures.

        Parameters
        ----------
        bot_id : str
            The simulator's bot identifier (maps to a Tayog userId).
        content : str
            The post text body.
        image_path : str | None
            Path to the image file to attach. None = text-only post.

        Returns
        -------
        UploadResult with all fields populated.
        """
        async with self._semaphore:
            return await self._upload_with_retry(bot_id, content, image_path)

    @property
    def analytics(self) -> UploadAnalytics:
        return self._analytics

    # ── Internal logic ────────────────────────────────────────────────────────

    async def _upload_with_retry(
        self,
        bot_id: str,
        content: str,
        image_path: Optional[str],
    ) -> UploadResult:
        result = UploadResult(bot_id=bot_id)

        # ── Resolve userId ────────────────────────────────────────────────────
        user_id = self._mapping.get_user_id(bot_id)
        if user_id is None:
            result.tayog_upload_status = "skipped"
            result.error_message = "No userId mapping found"
            self._analytics.record(result)
            self._upload_logger.log_result(result)
            return result

        result.user_id = user_id

        # ── Payload validation ────────────────────────────────────────────────
        image_paths = [image_path] if image_path else []
        errors = _validate_payload(user_id, content, self._secret, image_paths)
        if errors:
            result.tayog_upload_status = "failed"
            result.error_message = "; ".join(errors)
            logger.error("Payload validation failed for bot=%s: %s", bot_id, result.error_message)
            self._analytics.record(result)
            self._upload_logger.log_result(result)
            return result

        # ── Dry-run short-circuit ─────────────────────────────────────────────
        if self._dry_run:
            logger.info(
                "[DRY-RUN] Valid payload for bot=%s user=%s content_len=%d image=%s",
                bot_id, user_id, len(content), image_path or "none",
            )
            result.tayog_upload_status = "skipped"
            result.error_message = "dry_run_mode"
            self._analytics.record(result)
            self._upload_logger.log_result(result)
            return result

        # ── Upload with exponential backoff ───────────────────────────────────
        attempt = 0
        delay = 1.0

        while attempt <= self._max_retries:
            try:
                t0 = time.monotonic()
                status_code, body = await self._do_upload(user_id, content, image_path)
                elapsed_ms = (time.monotonic() - t0) * 1000

                result.response_code = status_code
                result.latency_ms = elapsed_ms

                if status_code == 201:
                    result.tayog_upload_status = "success"
                    # Try common response shapes for post ID
                    result.tayog_post_id = (
                        body.get("postId")
                        or body.get("id")
                        or body.get("data", {}).get("id")
                    )
                    result.tayog_upload_timestamp = datetime.now(timezone.utc).isoformat()
                    logger.info(
                        "✓ Uploaded bot=%s → postId=%s (%.0fms)",
                        bot_id, result.tayog_post_id, elapsed_ms,
                    )
                    break

                elif status_code in self.RETRYABLE_STATUSES and attempt < self._max_retries:
                    logger.warning(
                        "Retryable HTTP %d for bot=%s (attempt %d/%d), backing off %.1fs",
                        status_code, bot_id, attempt + 1, self._max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)   # cap at 30 s
                    attempt += 1
                    result.retry_count = attempt

                else:
                    result.tayog_upload_status = "failed"
                    result.error_message = f"HTTP {status_code}: {body}"
                    logger.error(
                        "✗ Upload failed bot=%s HTTP=%d body=%s",
                        bot_id, status_code, str(body)[:200],
                    )
                    break

            except asyncio.TimeoutError:
                if attempt < self._max_retries:
                    logger.warning(
                        "Timeout for bot=%s (attempt %d/%d), backing off %.1fs",
                        bot_id, attempt + 1, self._max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)
                    attempt += 1
                    result.retry_count = attempt
                else:
                    result.tayog_upload_status = "failed"
                    result.error_message = "Request timed out after all retries"
                    break

            except aiohttp.ClientConnectorError as exc:
                result.tayog_upload_status = "failed"
                result.error_message = f"Connection error: {exc}"
                logger.error("Connection error for bot=%s: %s", bot_id, exc)
                break

            except Exception as exc:
                result.tayog_upload_status = "failed"
                result.error_message = str(exc)
                logger.error(
                    "Unexpected error uploading bot=%s: %s", bot_id, exc, exc_info=True
                )
                break

        # If we exhausted retries without setting a terminal status
        if result.tayog_upload_status == "pending":
            result.tayog_upload_status = "failed"
            result.error_message = "Exhausted all retries"

        self._analytics.record(result)
        self._upload_logger.log_result(result)
        return result

    async def _do_upload(
        self,
        user_id: str,
        content: str,
        image_path: Optional[str],
    ) -> Tuple[int, dict]:
        """
        Build and send the multipart/form-data POST request.

        Tayog API contract:
          files[]        – binary image bytes (field name must be "files")
          fileMetadata[] – JSON string: {"type": "image", "sortOrder": N}
          postData       – JSON string: {"x_secrect": ..., "content": ..., "userId": ...}

        All three fields are added to the same FormData in the correct order.
        """
        endpoint = f"{self._base_url}/api/posts/new/v1"

        post_data = {
            "x_secrect": self._secret,
            "content": content,
            "userId": user_id,
        }

        form = aiohttp.FormData()

        # ── Attach image if available ─────────────────────────────────────────
        if image_path:
            img_path = Path(image_path)
            if img_path.exists() and img_path.stat().st_size > 0:
                mime, _ = mimetypes.guess_type(str(img_path))
                mime = mime or "image/png"
                file_bytes = img_path.read_bytes()

                # files[] field — binary payload
                form.add_field(
                    "files",
                    file_bytes,
                    filename=img_path.name,
                    content_type=mime,
                )
                # fileMetadata[] field — JSON string, same index as the file
                form.add_field(
                    "fileMetadata",
                    json.dumps({"type": "image", "sortOrder": 1}),
                    content_type="application/json",
                )
            else:
                logger.warning(
                    "Image file missing or empty (%s), sending text-only post.",
                    image_path,
                )

        # ── postData field — must always be present ───────────────────────────
        form.add_field(
            "postData",
            json.dumps(post_data, ensure_ascii=False),
            content_type="application/json",
        )

        async with self._session.post(endpoint, data=form) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                text = await resp.text()
                body = {"raw": text[:500]}
            return resp.status, body
