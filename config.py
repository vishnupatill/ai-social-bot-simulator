"""
config.py — Central configuration for AI Social Bot Simulator.

All settings are read from environment variables with safe defaults.
Hardcoded API keys have been removed; missing critical keys emit startup
warnings rather than silent failures.

Environment variables are loaded from a .env file if python-dotenv is installed.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Optional .env loading ─────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)   # never overwrite already-set shell vars
except ImportError:
    pass   # python-dotenv is optional; env vars must be set manually

logger = logging.getLogger(__name__)

# ── Helper ────────────────────────────────────────────────────────────────────

def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError):
        return default


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── Config dataclass ──────────────────────────────────────────────────────────

@dataclass
class Config:
    # ── API Keys ──────────────────────────────────────────────────────────────
    # SECURITY: Never hard-code values here. Set via environment variables.
    GEMINI_API_KEY: str = field(
        default_factory=lambda: _env_str("GEMINI_API_KEY")
    )
    HF_API_TOKEN: str = field(
        default_factory=lambda: _env_str("HF_API_TOKEN")
    )

    # ── LLM Settings ──────────────────────────────────────────────────────────
    GEMINI_MODEL: str = field(
        default_factory=lambda: _env_str("GEMINI_MODEL", "gemini-2.0-flash")
    )
    LLM_MAX_TOKENS: int = field(
        default_factory=lambda: _env_int("LLM_MAX_TOKENS", 300)
    )
    LLM_TEMPERATURE: float = field(
        default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.85)
    )

    # ── Image Generation ──────────────────────────────────────────────────────
    HF_IMAGE_MODEL: str = "black-forest-labs/FLUX.1-schnell"
    HF_IMAGE_API_URL: str = (
        "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
    )
    HF_IMAGE_MODEL_FALLBACK: str = "stabilityai/stable-diffusion-xl-base-1.0"
    HF_IMAGE_API_URL_FALLBACK: str = (
        "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0"
    )
    IMAGE_WIDTH: int = field(
        default_factory=lambda: _env_int("IMAGE_WIDTH", 1024)
    )
    IMAGE_HEIGHT: int = field(
        default_factory=lambda: _env_int("IMAGE_HEIGHT", 1024)
    )
    IMAGE_FOLDER: str = field(
        default_factory=lambda: _env_str("IMAGE_FOLDER", "images")
    )
    IMAGE_CROP_BOTTOM_RATIO: float = field(
        default_factory=lambda: _env_float("IMAGE_CROP_BOTTOM_RATIO", 0.05)
    )

    # ── Bot Settings ──────────────────────────────────────────────────────────
    TOTAL_BOTS: int = field(
        default_factory=lambda: _env_int("TOTAL_BOTS", 10)
    )
    BATCH_SIZE: int = field(
        default_factory=lambda: _env_int("BATCH_SIZE", 1)
    )
    DELAY_BETWEEN_BOTS: float = field(
        default_factory=lambda: _env_float("DELAY_BETWEEN_BOTS", 1.5)
    )
    DELAY_BETWEEN_BATCHES: float = field(
        default_factory=lambda: _env_float("DELAY_BETWEEN_BATCHES", 4.0)
    )
    MAX_CONCURRENT_REQUESTS: int = field(
        default_factory=lambda: _env_int("MAX_CONCURRENT_REQUESTS", 5)
    )

    # ── Post Content Settings ─────────────────────────────────────────────────
    POST_MIN_WORDS: int = field(
        default_factory=lambda: _env_int("POST_MIN_WORDS", 70)
    )
    POST_MAX_WORDS: int = field(
        default_factory=lambda: _env_int("POST_MAX_WORDS", 100)
    )

    # ── Storage ───────────────────────────────────────────────────────────────
    POSTS_FILE: str = field(
        default_factory=lambda: _env_str("POSTS_FILE", "posts.json")
    )
    BOTS_FILE: str = field(
        default_factory=lambda: _env_str("BOTS_FILE", "bots.json")
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = field(
        default_factory=lambda: _env_str("LOG_LEVEL", "INFO")
    )
    LOG_FILE: str = field(
        default_factory=lambda: _env_str("LOG_FILE", "simulator.log")
    )
    LOG_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    # ── Demo / Dev mode ───────────────────────────────────────────────────────
    DEMO_MODE: bool = field(
        default_factory=lambda: _env_bool("DEMO_MODE", False)
    )

    # ── Tayog Integration ─────────────────────────────────────────────────────
    TAYOG_BASE_URL: str = field(
        default_factory=lambda: _env_str("TAYOG_BASE_URL", "https://yourapp.com")
    )
    TAYOG_SECRET_KEY: str = field(
        default_factory=lambda: _env_str("TAYOG_SECRET_KEY")
    )
    TAYOG_BOT_MAPPING_FILE: str = field(
        default_factory=lambda: _env_str("TAYOG_BOT_MAPPING_FILE", "bot_user_mapping.json")
    )

    # Upload feature flags
    ENABLE_TAYOG_UPLOAD: bool = field(
        default_factory=lambda: _env_bool("ENABLE_TAYOG_UPLOAD", True)
    )
    ENABLE_LOCAL_STORAGE: bool = field(
        default_factory=lambda: _env_bool("ENABLE_LOCAL_STORAGE", True)
    )

    # Dry-run: validates payloads without actually sending to Tayog
    DRY_RUN_MODE: bool = field(
        default_factory=lambda: _env_bool("DRY_RUN_MODE", False)
    )

    # Mention generation
    ENABLE_MENTIONS: bool = field(
        default_factory=lambda: _env_bool("ENABLE_MENTIONS", False)
    )

    # Upload concurrency and retry
    MAX_CONCURRENT_UPLOADS: int = field(
        default_factory=lambda: _env_int("MAX_CONCURRENT_UPLOADS", 3)
    )
    TAYOG_MAX_RETRIES: int = field(
        default_factory=lambda: _env_int("TAYOG_MAX_RETRIES", 4)
    )

    # Upload log file
    TAYOG_UPLOAD_LOG: str = field(
        default_factory=lambda: _env_str("TAYOG_UPLOAD_LOG", "tayog_upload.log")
    )

    # ── Scheduler ─────────────────────────────────────────────────────────────
    SCHEDULER_TICK_BOTS: int = field(
        default_factory=lambda: _env_int("SCHEDULER_TICK_BOTS", 5)
    )
    SCHEDULER_TICK_INTERVAL: float = field(
        default_factory=lambda: _env_float("SCHEDULER_TICK_INTERVAL", 3.0)
    )

    def validate(self) -> list[str]:
        """
        Validate configuration and return a list of warning messages.
        Does NOT raise — callers decide whether to abort.
        """
        warnings: list[str] = []

        if not self.GEMINI_API_KEY:
            warnings.append(
                "GEMINI_API_KEY is not set — text generation will use demo/mock mode."
            )
        if not self.HF_API_TOKEN:
            warnings.append(
                "HF_API_TOKEN is not set — image generation will use placeholder images."
            )
        if self.ENABLE_TAYOG_UPLOAD and not self.TAYOG_SECRET_KEY:
            warnings.append(
                "ENABLE_TAYOG_UPLOAD=True but TAYOG_SECRET_KEY is not set — "
                "all uploads will fail authentication."
            )
        if self.ENABLE_TAYOG_UPLOAD and self.TAYOG_BASE_URL == "https://yourapp.com":
            warnings.append(
                "TAYOG_BASE_URL is still the default placeholder — "
                "set TAYOG_BASE_URL to your actual Tayog instance."
            )
        if self.BATCH_SIZE < 1:
            warnings.append("BATCH_SIZE must be >= 1; resetting to 1.")
            self.BATCH_SIZE = 1
        if self.TOTAL_BOTS < 1:
            warnings.append("TOTAL_BOTS must be >= 1; resetting to 1.")
            self.TOTAL_BOTS = 1

        return warnings

    def emit_startup_warnings(self) -> None:
        """Call once at startup to log all configuration issues."""
        for msg in self.validate():
            logger.warning("⚠  CONFIG: %s", msg)


# ── Singleton instance ────────────────────────────────────────────────────────
cfg = Config()
