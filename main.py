"""
main.py — Entry point for AI Social Bot Simulator.

Usage
-----
    python main.py [options]

Options
-------
    --bots      N       Number of bots to activate (default: cfg.TOTAL_BOTS)
    --batch     N       Batch size (default: cfg.BATCH_SIZE)
    --delay     N       Seconds between batches (default: cfg.DELAY_BETWEEN_BATCHES)
    --demo              Force demo/mock mode (no API keys needed)
    --clear             Clear existing posts before running
    --stats             Print storage stats and exit
    --tayog-stats       Print Tayog upload analytics and exit
    --dry-run           Build Tayog payloads but do not send
    --no-tayog          Disable Tayog upload for this run
    --help              Show this help message

Examples
--------
    python main.py --bots 50 --demo          # Quick demo with 50 bots
    python main.py --bots 1000 --batch 20    # Full production run
    python main.py --stats                   # Show current DB stats
    python main.py --tayog-stats             # Show Tayog upload analytics
    python main.py --dry-run --bots 5        # Validate payloads only
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List

# ── Bootstrap config and logging BEFORE any other project imports ─────────────
from config import cfg


def _setup_logging(level: str = cfg.LOG_LEVEL) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(cfg.LOG_FILE, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=log_level,
        format=cfg.LOG_FORMAT,
        handlers=handlers,
    )
    # Quiet noisy third-party loggers
    for noisy in ("aiohttp", "asyncio", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger("main")

# ── Emit startup configuration warnings ───────────────────────────────────────
cfg.emit_startup_warnings()

# ── Project imports ────────────────────────────────────────────────────────────
from bots import generate_all_bots, BotProfile
from scheduler import BotScheduler
from storage import Post, get_storage

# ── ANSI colours ──────────────────────────────────────────────────────────────
CYAN    = "\033[96m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
BOLD    = "\033[1m"
RESET   = "\033[0m"
DIM     = "\033[2m"
MAGENTA = "\033[95m"
RED     = "\033[91m"


# ── Terminal helpers ──────────────────────────────────────────────────────────

def _banner() -> None:
    print(f"""
{BOLD}{CYAN}
╔══════════════════════════════════════════════════════════╗
║          AI SOCIAL BOT SIMULATOR  v2.1                  ║
║     Autonomous Research-Focused Social Agents            ║
╚══════════════════════════════════════════════════════════╝
{RESET}""")


def _print_post(post: Post, idx: int, total: int) -> None:
    bar = f"{CYAN}{'─' * 60}{RESET}"
    pct = int((idx + 1) / total * 100)
    header = (
        f"{BOLD}{GREEN}[{idx + 1:>4}/{total}]  {post.name}{RESET}  "
        f"{DIM}({post.bot_id[:8]}…)  {pct}%{RESET}"
    )
    ts = f"{DIM}{post.timestamp[:19].replace('T', ' ')} UTC{RESET}"
    img = f"{MAGENTA}📷 {post.image_path}{RESET}"
    tayog = ""
    if post.tayog_upload_status == "success":
        tayog = f"{GREEN}✓ Tayog postId={post.tayog_post_id}{RESET}"
    elif post.tayog_upload_status == "failed":
        tayog = f"{RED}✗ Tayog upload failed{RESET}"

    print(f"\n{bar}")
    print(header)
    print(ts)
    print()
    print(post.content)
    print()
    print(img)
    if tayog:
        print(tayog)


async def _on_post(post: Post, idx: int, total: int) -> None:
    """Async callback fired after each post is processed."""
    _print_post(post, idx, total)


def _print_stats() -> None:
    storage = get_storage()
    stats = storage.stats()
    print(f"\n{BOLD}Storage stats:{RESET}")
    for k, v in stats.items():
        print(f"  {k:<22} {v}")


def _print_tayog_stats() -> None:
    """Print Tayog upload status breakdown from local posts.json."""
    storage = get_storage()
    all_posts = storage.load_all()

    if not all_posts:
        print(f"\n{YELLOW}No posts found in storage.{RESET}")
        return

    statuses: dict[str, int] = {}
    latencies: list[float] = []

    for p in all_posts:
        statuses[p.tayog_upload_status] = statuses.get(p.tayog_upload_status, 0) + 1

    success_total = statuses.get("success", 0)
    success_rate = (
        round(success_total / len(all_posts) * 100, 1) if all_posts else 0.0
    )

    print(f"\n{BOLD}Tayog Upload Stats:{RESET}")
    print(f"  {'total':<14} {len(all_posts)}")
    for status, count in sorted(statuses.items()):
        print(f"  {status:<14} {count}")
    print(f"  {'success_rate':<14} {success_rate}%")

    uploaded_ids = [p.tayog_post_id for p in all_posts if p.tayog_post_id]
    if uploaded_ids:
        print(f"\n  Sample post URLs (latest 5):")
        for pid in uploaded_ids[-5:]:
            print(f"    {cfg.TAYOG_BASE_URL}/posts/{pid}")


# ── Argument parser ───────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Social Bot Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--bots",        type=int,   default=cfg.TOTAL_BOTS,            help="Number of bots")
    parser.add_argument("--batch",       type=int,   default=cfg.BATCH_SIZE,            help="Batch size")
    parser.add_argument("--delay",       type=float, default=cfg.DELAY_BETWEEN_BATCHES, help="Seconds between batches")
    parser.add_argument("--demo",        action="store_true",                            help="Demo mode (mock APIs)")
    parser.add_argument("--clear",       action="store_true",                            help="Clear posts before run")
    parser.add_argument("--stats",       action="store_true",                            help="Show stats and exit")
    parser.add_argument("--tayog-stats", action="store_true",                            help="Show Tayog upload analytics and exit")
    parser.add_argument("--dry-run",     action="store_true",                            help="Validate Tayog payloads without uploading")
    parser.add_argument("--no-tayog",    action="store_true",                            help="Disable Tayog upload for this run")
    return parser.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    # ── Stats shortcuts (exit early) ──────────────────────────────────────────
    if args.stats:
        _print_stats()
        return

    if args.tayog_stats:
        _print_tayog_stats()
        return

    _banner()

    # ── Apply CLI overrides ───────────────────────────────────────────────────
    cfg.TOTAL_BOTS = args.bots
    cfg.BATCH_SIZE = args.batch
    cfg.DELAY_BETWEEN_BATCHES = args.delay

    if args.demo:
        cfg.DEMO_MODE = True
        logger.info("DEMO MODE active — using mock APIs")

    if args.dry_run:
        cfg.DRY_RUN_MODE = True
        logger.info("DRY-RUN MODE active — Tayog payloads built but not sent")

    if args.no_tayog:
        cfg.ENABLE_TAYOG_UPLOAD = False
        logger.info("Tayog upload disabled for this run via --no-tayog flag")

    # ── Ensure output directories exist ──────────────────────────────────────
    Path(cfg.IMAGE_FOLDER).mkdir(parents=True, exist_ok=True)

    # ── Optionally clear previous run ─────────────────────────────────────────
    storage = get_storage()
    if args.clear:
        storage.clear()
        logger.info("Posts cleared.")

    # ── Print run parameters ──────────────────────────────────────────────────
    mode = (
        f"{YELLOW}DEMO (mock APIs){RESET}"
        if cfg.DEMO_MODE
        else f"{GREEN}LIVE (real APIs){RESET}"
    )
    dry = f"{YELLOW}YES — no uploads sent{RESET}" if cfg.DRY_RUN_MODE else "No"

    print(f"  Mode          : {mode}")
    print(f"  Bots          : {cfg.TOTAL_BOTS}")
    print(f"  Batch size    : {cfg.BATCH_SIZE}")
    print(f"  Batch delay   : {cfg.DELAY_BETWEEN_BATCHES}s")
    print(f"  Concurrency   : {cfg.MAX_CONCURRENT_REQUESTS}")
    print(f"  Posts file    : {cfg.POSTS_FILE}")
    print(f"  Images folder : {cfg.IMAGE_FOLDER}/")
    print(f"  Log file      : {cfg.LOG_FILE}")
    print(f"  Tayog upload  : {cfg.ENABLE_TAYOG_UPLOAD}")
    print(f"  Local storage : {cfg.ENABLE_LOCAL_STORAGE}")
    print(f"  Dry-run       : {dry}")
    print()

    start_time = time.perf_counter()

    # ── Generate bot profiles ─────────────────────────────────────────────────
    print(f"{BOLD}Generating / loading bot profiles…{RESET}")
    bots = generate_all_bots(cfg.TOTAL_BOTS)
    print(f"  {len(bots)} bots ready.\n")

    # ── Run the scheduler ─────────────────────────────────────────────────────
    scheduler = BotScheduler(
        bots=bots,
        batch_size=cfg.BATCH_SIZE,
        delay_between_bots=cfg.DELAY_BETWEEN_BOTS,
        delay_between_batches=cfg.DELAY_BETWEEN_BATCHES,
        callback=_on_post,
    )

    try:
        posts = scheduler.run()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted by user. Partial results saved.{RESET}")
        posts = scheduler.posts

    elapsed = time.perf_counter() - start_time

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{BOLD}{CYAN}{'═' * 60}{RESET}")
    print(f"{BOLD}  Run complete!{RESET}")
    print(f"  Posts generated : {len(posts)}")
    print(f"  Elapsed time    : {elapsed:.1f}s")
    print(f"  Posts file      : {cfg.POSTS_FILE}")
    print(f"  Images folder   : {cfg.IMAGE_FOLDER}/")
    _print_stats()
    print(f"{BOLD}{CYAN}{'═' * 60}{RESET}\n")

    logger.info("Run finished — %d posts in %.1fs", len(posts), elapsed)


if __name__ == "__main__":
    main()
