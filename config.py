"""
config.py - Central configuration for AI Social Bot Simulator
All API keys, model settings, and runtime parameters live here.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    # ── API Keys ──────────────────────────────────────────────────────────────
    # Set these via environment variables or replace the defaults below.
    GEMINI_API_KEY: str = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
    )
    # Hugging Face token for free image generation (Stable Diffusion)
    HF_API_TOKEN: str = field(
        default_factory=lambda: os.getenv("HF_API_TOKEN", "YOUR_HF_API_TOKEN")
    )

    # ── LLM Settings ──────────────────────────────────────────────────────────
    GEMINI_MODEL: str = "gemini-1.5-flash"          # fast & free-tier friendly
    LLM_MAX_TOKENS: int = 300
    LLM_TEMPERATURE: float = 0.9                    # higher = more creative

    # ── Image Generation ─────────────────────────────────────────────────────
    # Hugging Face Inference API endpoint (free, no credit card required)
    HF_IMAGE_MODEL: str = "stabilityai/stable-diffusion-xl-base-1.0"
    HF_IMAGE_API_URL: str = (
        f"https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
    )
    IMAGE_WIDTH: int = 512
    IMAGE_HEIGHT: int = 512
    IMAGE_FOLDER: str = "images"

    # ── Bot Settings ──────────────────────────────────────────────────────────
    TOTAL_BOTS: int = 1000                          # total bots in the system
    BATCH_SIZE: int = 10                            # bots processed per batch
    DELAY_BETWEEN_BOTS: float = 2.0                # seconds between bots in a batch
    DELAY_BETWEEN_BATCHES: float = 5.0             # seconds between batches
    MAX_CONCURRENT_REQUESTS: int = 5               # async concurrency cap

    # ── Storage ───────────────────────────────────────────────────────────────
    POSTS_FILE: str = "posts.json"
    BOTS_FILE: str = "bots.json"                   # cached bot profiles

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "simulator.log"
    LOG_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    # ── Demo / Dev mode ───────────────────────────────────────────────────────
    # When True, uses mock LLM/image calls so you can test without API keys.
    DEMO_MODE: bool = False

    # ── Scheduler ─────────────────────────────────────────────────────────────
    # How many bots to activate per scheduler tick
    SCHEDULER_TICK_BOTS: int = 5
    # Seconds between scheduler ticks
    SCHEDULER_TICK_INTERVAL: float = 3.0


# Singleton instance – import this everywhere
cfg = Config()
