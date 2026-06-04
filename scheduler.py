"""
scheduler.py — Scheduling engine for AI Social Bot Simulator.

Divides bots into batches, introduces configurable delays between them,
and manages the async event loop.  Each batch runs concurrently up to the
MAX_CONCURRENT_REQUESTS cap; batches themselves run sequentially.

Changes from v1
---------------
- After generating a post, optionally uploads to Tayog via TayogClient
- Supports ENABLE_TAYOG_UPLOAD and ENABLE_LOCAL_STORAGE flags
- Tracks upload results inside Post objects
- Stores image metadata (width, height, mime_type, file_size) in Post
- TayogClient lifecycle is correctly managed via async context manager
- aiohttp session is shared between ContentGenerator and TayogClient
  to maximise connection reuse; ownership is tracked and closed once
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    aiohttp = None  # type: ignore
    _HAS_AIOHTTP = False

from bots import BotProfile
from config import cfg
from generator import ContentGenerator
from storage import Post, get_storage
from tayog_client import (
    BotUserMapping,
    TayogClient,
    UploadAnalytics,
    extract_image_metadata,
)

logger = logging.getLogger(__name__)

# ── Types ─────────────────────────────────────────────────────────────────────
PostCallback = Callable[[Post, int, int], Awaitable[None]]


# ── Core runner ───────────────────────────────────────────────────────────────

async def _process_bot(
    bot: BotProfile,
    generator: ContentGenerator,
    bot_index: int,
    total_bots: int,
    callback: Optional[PostCallback],
    tayog_client: Optional[TayogClient],
) -> Optional[Post]:
    """Generate content for a single bot, optionally upload to Tayog, return a Post."""
    try:
        post_content, image_path = await generator.generate_for_bot(bot)

        post = Post(
            bot_id=bot.bot_id,
            name=bot.name,
            content=post_content,
            image_path=str(image_path),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # ── Extract and store image metadata ──────────────────────────────────
        if image_path and str(image_path):
            meta = extract_image_metadata(str(image_path))
            post.image_width = meta.width
            post.image_height = meta.height
            post.image_mime_type = meta.mime_type
            post.image_file_size = meta.file_size

        # ── Upload to Tayog ───────────────────────────────────────────────────
        if tayog_client is not None:
            result = await tayog_client.upload_post(
                bot_id=bot.bot_id,
                content=post_content,
                image_path=str(image_path) if image_path else None,
            )
            post.tayog_post_id = result.tayog_post_id
            post.tayog_upload_status = result.tayog_upload_status
            post.tayog_upload_timestamp = result.tayog_upload_timestamp

        # ── Local storage ─────────────────────────────────────────────────────
        if cfg.ENABLE_LOCAL_STORAGE:
            get_storage().save_post(post)

        if callback:
            await callback(post, bot_index, total_bots)

        return post

    except Exception as exc:
        logger.error(
            "Bot %s (%s) failed: %s", bot.bot_id, bot.name, exc, exc_info=True
        )
        return None


async def _run_batch(
    batch: List[BotProfile],
    batch_num: int,
    total_batches: int,
    generator: ContentGenerator,
    start_index: int,
    total_bots: int,
    callback: Optional[PostCallback],
    semaphore: asyncio.Semaphore,
    tayog_client: Optional[TayogClient],
) -> List[Post]:
    """Run a batch of bots concurrently, throttled by a semaphore."""
    logger.info(
        "▶  Batch %d/%d  |  %d bots  |  starting…",
        batch_num, total_batches, len(batch),
    )

    async def _guarded(bot: BotProfile, idx: int) -> Optional[Post]:
        async with semaphore:
            return await _process_bot(
                bot, generator, idx, total_bots, callback, tayog_client
            )

    tasks = [
        asyncio.create_task(_guarded(bot, start_index + i))
        for i, bot in enumerate(batch)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    posts: List[Post] = []
    for r in results:
        if isinstance(r, Post):
            posts.append(r)
        elif isinstance(r, Exception):
            logger.warning("Batch item raised an unhandled exception: %s", r)
        # None return (handled error in _process_bot) is silently skipped

    logger.info(
        "✓  Batch %d/%d done  |  %d/%d posts saved",
        batch_num, total_batches, len(posts), len(batch),
    )
    return posts


# ── Public scheduler ──────────────────────────────────────────────────────────

class BotScheduler:
    """
    Schedules and executes content generation for all bots.

    Parameters
    ----------
    bots : list[BotProfile]
        All bots to schedule.
    batch_size : int
        How many bots per batch.
    delay_between_bots : float
        Intra-batch throttle applied via the semaphore (informational;
        real throttle is MAX_CONCURRENT_REQUESTS).
    delay_between_batches : float
        Pause between consecutive batches in seconds.
    callback : PostCallback | None
        Async function called after each post is processed.
        Signature: async def cb(post, bot_index, total_bots) -> None
    """

    def __init__(
        self,
        bots: List[BotProfile],
        batch_size: int = cfg.BATCH_SIZE,
        delay_between_bots: float = cfg.DELAY_BETWEEN_BOTS,
        delay_between_batches: float = cfg.DELAY_BETWEEN_BATCHES,
        callback: Optional[PostCallback] = None,
    ):
        self.bots = bots
        self.batch_size = batch_size
        self.delay_between_bots = delay_between_bots
        self.delay_between_batches = delay_between_batches
        self.callback = callback
        self._all_posts: List[Post] = []

    # ── Run ───────────────────────────────────────────────────────────────────

    async def run_async(self) -> List[Post]:
        """Main coroutine: process all bots in scheduled batches."""
        total = len(self.bots)
        batches = [
            self.bots[i : i + self.batch_size]
            for i in range(0, total, self.batch_size)
        ]
        total_batches = len(batches)

        logger.info(
            "Scheduler started | %d bots | %d batches | batch_size=%d | "
            "inter-batch_delay=%.1fs",
            total, total_batches, self.batch_size, self.delay_between_batches,
        )

        semaphore = asyncio.Semaphore(cfg.MAX_CONCURRENT_REQUESTS)

        # Build a single shared aiohttp session for both content generation
        # and Tayog uploads to maximise TCP connection reuse.
        http_session: Optional["aiohttp.ClientSession"] = None
        if _HAS_AIOHTTP:
            connector = aiohttp.TCPConnector(
                limit=max(cfg.MAX_CONCURRENT_REQUESTS, cfg.MAX_CONCURRENT_UPLOADS),
                enable_cleanup_closed=True,
            )
            http_session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=120),
            )

        # ── Build Tayog client if enabled ─────────────────────────────────────
        tayog_client: Optional[TayogClient] = None

        if cfg.ENABLE_TAYOG_UPLOAD:
            if not cfg.TAYOG_SECRET_KEY:
                logger.warning(
                    "ENABLE_TAYOG_UPLOAD=True but TAYOG_SECRET_KEY is empty — "
                    "all uploads will fail authentication."
                )
            mapping = BotUserMapping(cfg.TAYOG_BOT_MAPPING_FILE)

            if mapping.mapped_count == 0:
                logger.warning(
                    "bot_user_mapping.json has no valid entries — "
                    "all Tayog uploads will be skipped."
                )

            tayog_client = TayogClient(
                base_url=cfg.TAYOG_BASE_URL,
                secret_key=cfg.TAYOG_SECRET_KEY,
                mapping=mapping,
                max_retries=cfg.TAYOG_MAX_RETRIES,
                max_concurrent=cfg.MAX_CONCURRENT_UPLOADS,
                dry_run=cfg.DRY_RUN_MODE,
                session=http_session,      # share session; client does NOT own it
            )
            # Manually enter the context (session ownership stays with us)
            await tayog_client.__aenter__()
            logger.info(
                "Tayog upload enabled | base_url=%s | dry_run=%s | mappings=%d",
                cfg.TAYOG_BASE_URL, cfg.DRY_RUN_MODE, mapping.mapped_count,
            )
        else:
            logger.info("Tayog upload disabled — local storage only.")

        try:
            uploads_per_batch: List[int] = []

            async with ContentGenerator(session=http_session) as gen:
                for batch_num, batch in enumerate(batches, start=1):
                    start_index = (batch_num - 1) * self.batch_size
                    posts = await _run_batch(
                        batch=batch,
                        batch_num=batch_num,
                        total_batches=total_batches,
                        generator=gen,
                        start_index=start_index,
                        total_bots=total,
                        callback=self.callback,
                        semaphore=semaphore,
                        tayog_client=tayog_client,
                    )
                    self._all_posts.extend(posts)

                    batch_success = sum(
                        1 for p in posts if p.tayog_upload_status == "success"
                    )
                    uploads_per_batch.append(batch_success)

                    if batch_num < total_batches:
                        logger.info(
                            "  Pausing %.1fs before next batch…",
                            self.delay_between_batches,
                        )
                        await asyncio.sleep(self.delay_between_batches)

            if tayog_client:
                tayog_client.analytics.uploads_per_batch = uploads_per_batch

        finally:
            # Shut down Tayog client first (it does not own the session)
            if tayog_client:
                await tayog_client.__aexit__(None, None, None)
            # Then close the shared session we own
            if http_session and not http_session.closed:
                await http_session.close()

        logger.info(
            "Scheduler complete | %d/%d posts generated",
            len(self._all_posts), total,
        )

        if cfg.ENABLE_TAYOG_UPLOAD and tayog_client:
            logger.info("\n%s", tayog_client.analytics.report())

        return self._all_posts

    def run(self) -> List[Post]:
        """Synchronous wrapper: creates a fresh event loop and runs run_async()."""
        return asyncio.run(self.run_async())

    @property
    def posts(self) -> List[Post]:
        return self._all_posts
