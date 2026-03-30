"""
generator.py - AI content and image generation for AI Social Bot Simulator.

Supports:
  • Google Gemini API  (text)
  • Hugging Face Inference API  (images via Stable Diffusion)
  • Demo/mock mode  (no API keys required – great for testing)
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import re
import time
from pathlib import Path
from typing import Optional, Tuple

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    aiohttp = None   # type: ignore
    _HAS_AIOHTTP = False

from bots import BotProfile
from config import cfg

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

POST_SYSTEM_PROMPT = (
    "You are a professional on a social media platform similar to LinkedIn. "
    "Write an authentic, engaging post that sounds natural and human. "
    "Vary your style – sometimes share a lesson learned, a project update, "
    "an opinion on tech trends, a mini-tutorial, or a personal milestone. "
    "Do NOT use generic phrases like 'Excited to announce' or 'Thrilled to share'. "
    "Keep it under 280 words. No hashtag spam – at most 3 relevant hashtags. "
    "Return ONLY the post text, nothing else."
)

def _build_post_prompt(bot: BotProfile) -> str:
    skills_str = ", ".join(bot.skills[:5])
    return (
        f"Your profile: {bot.name}, {bot.subheading}. "
        f"Location: {bot.location}. "
        f"Core skills: {skills_str}. "
        f"Current project: {bot.project}. "
        f"About you: {bot.about[:200]} "
        f"\n\nWrite a unique, organic social media post based on your work and expertise."
    )


def _build_image_prompt(post_content: str, bot: BotProfile) -> str:
    """Derive a concise image prompt from the post text."""
    # Extract key technical nouns (simple heuristic)
    words = re.findall(r"[A-Z][a-zA-Z]+|[a-z]{4,}", post_content)
    tech_words = [w for w in words if len(w) > 3][:5]
    keywords = " ".join(tech_words) if tech_words else bot.skills[0]

    style = random.choice([
        "photorealistic, professional workspace, soft lighting",
        "isometric 3D illustration, clean background",
        "flat design illustration, vibrant colors",
        "dark mode UI screenshot, glowing elements",
        "cinematic, tech conference stage, keynote slide",
        "abstract data visualization, neon on dark",
    ])
    return (
        f"A high-quality image representing: {keywords}. "
        f"Style: {style}. "
        "No text, no watermarks, professional quality."
    )


# ── Mock generators (demo mode) ───────────────────────────────────────────────

_MOCK_POSTS = [
    (
        "Just refactored our entire {skill} pipeline from scratch. "
        "The key insight? Complexity is almost always a symptom of unclear requirements. "
        "When you truly understand the problem, the solution simplifies itself. "
        "Shipping clean code feels better than shipping fast code every time. #softwareengineering"
    ),
    (
        "3 years ago I couldn't write a proper {skill} function. "
        "Today I architected a system handling 50K requests/minute. "
        "The only secret: consistency. Code a little every day. Break things. Fix them. Repeat. "
        "#growth #coding"
    ),
    (
        "Hot take: most performance problems aren't about {skill} at all. "
        "They're about misunderstanding your data access patterns. "
        "Profile first. Optimize second. Never guess. #backendengineering"
    ),
    (
        "We just open-sourced our internal {skill} toolkit. "
        "It's not perfect – but it's the tool we wish existed when we started. "
        "Link in bio. Contributions welcome. "
        "#opensource #developer"
    ),
    (
        "The best engineering decision I made this quarter: saying no to a feature. "
        "Every line of {skill} code you DON'T write is one less thing to maintain. "
        "Product velocity is subtraction, not addition. #engineeringmanagement"
    ),
]


async def _mock_generate_post(bot: BotProfile) -> str:
    await asyncio.sleep(0.05)          # simulate tiny latency
    template = random.choice(_MOCK_POSTS)
    skill = random.choice(bot.skills[:3])
    return template.format(skill=skill)


async def _mock_generate_image(prompt: str, path: Path) -> bool:
    """Write a tiny valid PNG placeholder so downstream code doesn't break."""
    await asyncio.sleep(0.05)
    # Minimal 1×1 red PNG
    PNG_1X1 = bytes([
        0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A,
        0x00,0x00,0x00,0x0D,0x49,0x48,0x44,0x52,
        0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,
        0x08,0x02,0x00,0x00,0x00,0x90,0x77,0x53,
        0xDE,0x00,0x00,0x00,0x0C,0x49,0x44,0x41,
        0x54,0x08,0xD7,0x63,0xF8,0xCF,0xC0,0x00,
        0x00,0x00,0x02,0x00,0x01,0xE2,0x21,0xBC,
        0x33,0x00,0x00,0x00,0x00,0x49,0x45,0x4E,
        0x44,0xAE,0x42,0x60,0x82,
    ])
    path.write_bytes(PNG_1X1)
    return True


# ── Gemini text generation ────────────────────────────────────────────────────

async def _gemini_generate_post(bot: BotProfile, session: aiohttp.ClientSession) -> str:
    """Call the Gemini REST API to generate a post."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg.GEMINI_MODEL}:generateContent?key={cfg.GEMINI_API_KEY}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": POST_SYSTEM_PROMPT + "\n\n" + _build_post_prompt(bot)}
                ]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": cfg.LLM_MAX_TOKENS,
            "temperature": cfg.LLM_TEMPERATURE,
        },
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Gemini API error %d: %s", resp.status, body[:200])
                return await _mock_generate_post(bot)
            data = await resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return text.strip()
    except Exception as exc:
        logger.warning("Gemini request failed (%s). Using mock.", exc)
        return await _mock_generate_post(bot)


# ── Hugging Face image generation ─────────────────────────────────────────────

async def _hf_generate_image(
    prompt: str, path: Path, session: aiohttp.ClientSession
) -> bool:
    """Call the HF Inference API to generate an image and save it."""
    headers = {"Authorization": f"Bearer {cfg.HF_API_TOKEN}"}
    payload = {"inputs": prompt, "parameters": {"width": cfg.IMAGE_WIDTH, "height": cfg.IMAGE_HEIGHT}}

    try:
        async with session.post(
            cfg.HF_IMAGE_API_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("HF image API error %d: %s", resp.status, body[:200])
                return await _mock_generate_image(prompt, path)
            image_bytes = await resp.read()
            path.write_bytes(image_bytes)
            logger.debug("Image saved: %s (%d bytes)", path, len(image_bytes))
            return True
    except Exception as exc:
        logger.warning("HF image request failed (%s). Using mock.", exc)
        return await _mock_generate_image(prompt, path)


# ── Public interface ──────────────────────────────────────────────────────────

class ContentGenerator:
    """
    Orchestrates LLM post generation + image generation for a single bot.
    Re-uses a shared aiohttp session for connection pooling when available.
    Falls back to mock generation if aiohttp is not installed.
    """

    def __init__(self, session=None):
        self._session = session
        self._owns_session = session is None and _HAS_AIOHTTP

    async def __aenter__(self):
        if self._owns_session and _HAS_AIOHTTP:
            connector = aiohttp.TCPConnector(limit=cfg.MAX_CONCURRENT_REQUESTS)
            self._session = aiohttp.ClientSession(connector=connector)
        return self

    async def __aexit__(self, *_):
        if self._owns_session and self._session:
            await self._session.close()

    async def generate_post(self, bot: BotProfile) -> str:
        """Generate a social-media post for the given bot."""
        if cfg.DEMO_MODE or cfg.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY" or not _HAS_AIOHTTP:
            return await _mock_generate_post(bot)
        return await _gemini_generate_post(bot, self._session)

    async def generate_image(self, post_content: str, bot: BotProfile) -> Tuple[bool, Path]:
        """
        Generate an image matching the post and save it.
        Returns (success, path).
        """
        images_dir = Path(cfg.IMAGE_FOLDER)
        images_dir.mkdir(exist_ok=True)
        image_path = images_dir / f"{bot.bot_id}.png"

        prompt = _build_image_prompt(post_content, bot)

        if cfg.DEMO_MODE or cfg.HF_API_TOKEN == "YOUR_HF_API_TOKEN" or not _HAS_AIOHTTP:
            ok = await _mock_generate_image(prompt, image_path)
        else:
            ok = await _hf_generate_image(prompt, image_path, self._session)

        return ok, image_path

    async def generate_for_bot(self, bot: BotProfile) -> Tuple[str, Path]:
        """Convenience: generate post + image for one bot."""
        post = await self.generate_post(bot)
        _, img_path = await self.generate_image(post, bot)
        return post, img_path
