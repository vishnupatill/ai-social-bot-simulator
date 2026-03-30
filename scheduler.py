"""
scheduler.py - Scheduling engine for AI Social Bot Simulator.

Divides bots into batches, introduces configurable delays between them,
and manages the async event loop.  Each batch runs concurrently up to the
MAX_CONCURRENT_REQUESTS cap; batches themselves run sequentially to avoid
hammering rate-limited APIs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import List, Callable, Awaitable, Optional

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

logger = logging.getLogger(__name__)

# ── Types ─────────────────────────────────────────────────────────────────────
PostCallback = Callable[[Post, int, int], Awaitable[None]]  # (post, idx, total) → None


# ── Core runner ───────────────────────────────────────────────────────────────

async def _process_bot(
    bot: BotProfile,
    generator: ContentGenerator,
    bot_index: int,
    total_bots: int,
    callback: Optional[PostCallback],
) -> Optional[Post]:
    """Generate content for a single bot and return a Post."""
    try:
        post_content, image_path = await generator.generate_for_bot(bot)

        post = Post(
            bot_id=bot.bot_id,
            name=bot.name,
            content=post_content,
            image_path=str(image_path),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Persist immediately so partial runs are not lost
        get_storage().save_post(post)

        if callback:
            await callback(post, bot_index, total_bots)

        return post

    except Exception as exc:
        logger.error("Bot %s (%s) failed: %s", bot.bot_id, bot.name, exc, exc_info=True)
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
) -> List[Post]:
    """Run a batch of bots concurrently, throttled by a semaphore."""
    logger.info(
        "▶  Batch %d/%d  |  %d bots  |  starting…",
        batch_num, total_batches, len(batch)
    )

    async def _guarded(bot: BotProfile, idx: int):
        async with semaphore:
            return await _process_bot(bot, generator, idx, total_bots, callback)

    tasks = [
        asyncio.create_task(_guarded(bot, start_index + i))
        for i, bot in enumerate(batch)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    posts = []
    for r in results:
        if isinstance(r, Post):
            posts.append(r)
        elif isinstance(r, Exception):
            logger.warning("Batch item raised: %s", r)

    logger.info("✓  Batch %d/%d done  |  %d/%d posts saved", batch_num, total_batches, len(posts), len(batch))
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
        How many bots per batch (default: cfg.BATCH_SIZE).
    delay_between_bots : float
        Intra-batch delay in seconds (applied via semaphore pacing).
    delay_between_batches : float
        Inter-batch pause in seconds.
    callback : PostCallback | None
        Async function called after each post is saved.
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
        """Main entry: process all bots in scheduled batches."""
        total = len(self.bots)
        batches = [
            self.bots[i : i + self.batch_size]
            for i in range(0, total, self.batch_size)
        ]
        total_batches = len(batches)

        logger.info(
            "Scheduler started | %d bots | %d batches | batch_size=%d | delay=%.1fs/%.1fs",
            total, total_batches, self.batch_size,
            self.delay_between_bots, self.delay_between_batches,
        )

        semaphore = asyncio.Semaphore(cfg.MAX_CONCURRENT_REQUESTS)

        if _HAS_AIOHTTP:
            connector = aiohttp.TCPConnector(limit=cfg.MAX_CONCURRENT_REQUESTS)
            http_session = aiohttp.ClientSession(connector=connector)
        else:
            http_session = None

        try:
            from generator import ContentGenerator
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
                    )
                    self._all_posts.extend(posts)

                    if batch_num < total_batches:
                        logger.info("  Pausing %.1fs before next batch…", self.delay_between_batches)
                        await asyncio.sleep(self.delay_between_batches)
        finally:
            if http_session and _HAS_AIOHTTP:
                await http_session.close()

        logger.info("Scheduler complete | %d/%d posts generated", len(self._all_posts), total)
        return self._all_posts

    def run(self) -> List[Post]:
        """Synchronous wrapper around run_async()."""
        return asyncio.run(self.run_async())

    @property
    def posts(self) -> List[Post]:
        return self._all_posts
