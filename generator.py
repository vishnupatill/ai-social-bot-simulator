"""
generator.py - AI content and image generation for AI Social Bot Simulator.

Supports:
  • Google Gemini API  (text – 70–100 word posts on real-world research topics)
  • Hugging Face Inference API  (images via FLUX.1-schnell / SDXL fallback)
  • Post-processing  (PIL crop to remove watermark artifacts)
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

try:
    from PIL import Image as PILImage
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from bots import BotProfile
from config import cfg

logger = logging.getLogger(__name__)

# ── Research topic domains ────────────────────────────────────────────────────

RESEARCH_TOPICS = [
    # Artificial Intelligence
    "large language model alignment and safety",
    "multimodal AI and vision-language models",
    "retrieval-augmented generation (RAG) in production",
    "AI agents and autonomous decision-making systems",
    "federated learning for privacy-preserving AI",
    "transformer architecture efficiency improvements",
    "AI hallucination mitigation strategies",
    "open-source vs proprietary LLM trade-offs",
    "reinforcement learning from human feedback (RLHF)",
    "AI regulation and governance frameworks",
    # Cybersecurity
    "zero-trust network architecture adoption",
    "AI-powered threat detection and response",
    "supply chain attacks and software integrity",
    "quantum-safe cryptography migration challenges",
    "ransomware resilience and incident response",
    "adversarial machine learning and model robustness",
    "identity and access management in cloud environments",
    "vulnerability disclosure and responsible patching",
    # Healthcare
    "AI-assisted diagnostic imaging in radiology",
    "federated health data sharing and patient privacy",
    "wearable sensor data for chronic disease monitoring",
    "drug discovery acceleration using generative AI",
    "clinical NLP for electronic health record analysis",
    "digital twin models for personalized medicine",
    "mental health tech and AI-guided therapy tools",
    "genomics data pipelines and variant interpretation",
    # Climate Change
    "machine learning for climate model downscaling",
    "smart grid optimization with renewable energy sources",
    "carbon capture monitoring using satellite imagery",
    "AI for wildfire prediction and early warning systems",
    "ocean heat content measurement and sea level rise",
    "precision agriculture to reduce agricultural emissions",
    "energy-efficient data center design and green computing",
    "lifecycle analysis of EV batteries and recycling",
    # Data Science
    "causal inference vs correlation in predictive modeling",
    "real-time feature engineering with streaming pipelines",
    "data quality and observability in ML pipelines",
    "graph neural networks for relational data",
    "synthetic data generation for privacy and augmentation",
    "AutoML and neural architecture search trade-offs",
    "responsible AI and algorithmic bias auditing",
    "data mesh vs data lakehouse architectural patterns",
    # Emerging Technologies
    "quantum computing error correction milestones",
    "edge AI deployment on resource-constrained devices",
    "6G network architecture and terahertz communications",
    "neuromorphic computing and brain-inspired processors",
    "digital twins for smart city infrastructure",
    "autonomous vehicle perception stack challenges",
    "blockchain interoperability and cross-chain protocols",
    "spatial computing and mixed reality for enterprise",
]

# ── Prompts ───────────────────────────────────────────────────────────────────

# ── Hashtag pools by domain (used to seed topic-relevant tags) ───────────────
HASHTAG_POOLS = {
    "AI":            ["#AI", "#MachineLearning", "#DeepLearning", "#NeuralNetworks", "#GenerativeAI"],
    "Cybersecurity": ["#CyberSecurity", "#InfoSec", "#DataPrivacy", "#ZeroTrust", "#ThreatIntel"],
    "Climate":       ["#ClimateTech", "#Sustainability", "#ClimateAction", "#GreenTech", "#NetZero"],
    "Biotech":       ["#Biotech", "#Genomics", "#CRISPR", "#LifeSciences", "#PrecisionMedicine"],
    "Quantum":       ["#QuantumComputing", "#QuantumTech", "#QubitResearch"],
    "Robotics":      ["#Robotics", "#Automation", "#HRI", "#SwarmRobotics"],
    "Neuroscience":  ["#Neuroscience", "#BrainMachine", "#CogSci", "#Neuroimaging"],
    "DataScience":   ["#DataScience", "#BigData", "#Analytics", "#MLOps"],
    "default":       ["#Research", "#Innovation", "#TechTrends", "#FutureOfWork", "#STEM"],
}

# ── Optional mention pool (usernames that exist in Tayog) ─────────────────────
# Populate with real @usernames from your Tayog instance.
MENTION_POOL: list[str] = [
    # "@researcher_jane",
    # "@analyst_bob",
]


def _pick_hashtags(topic: str, count: int = 2) -> list[str]:
    """Return 1-3 unique hashtags relevant to the topic."""
    import random as _rnd
    count = max(1, min(count, 3))
    # Try to match a domain keyword
    tags: list[str] = []
    for domain, pool in HASHTAG_POOLS.items():
        if domain.lower() in topic.lower():
            tags = pool[:]
            break
    if not tags:
        tags = HASHTAG_POOLS["default"][:]
    _rnd.shuffle(tags)
    return list(dict.fromkeys(tags))[:count]   # deduplicated


def _pick_mentions(count: int = 1) -> list[str]:
    """Return up to  random mention handles from MENTION_POOL."""
    import random as _rnd
    if not MENTION_POOL or not cfg.ENABLE_MENTIONS:
        return []
    pool = MENTION_POOL[:]
    _rnd.shuffle(pool)
    return pool[:count]


POST_SYSTEM_PROMPT = (
    "You are a professional researcher or senior practitioner posting on a LinkedIn-style platform. "
    "Write an authentic, insightful post on the given research topic. "
    "The post MUST be between {min_words} and {max_words} words — count carefully. "
    "Sound like a real expert sharing a genuine observation, opinion, or recent finding. "
    "Vary your format: sometimes share a key insight, a nuanced critique, a field update, "
    "or a practical implication of recent research. "
    "Avoid generic opener phrases like 'Excited to announce' or 'Thrilled to share'. "
    "Include 1–3 relevant hashtags derived from the topic. No duplicate hashtags. "
    "Do NOT add a word count label. "
    "Return ONLY the post text, nothing else."
).format(min_words=cfg.POST_MIN_WORDS, max_words=cfg.POST_MAX_WORDS)


def _build_post_prompt(bot: BotProfile) -> str:
    topic = random.choice(RESEARCH_TOPICS)
    skills_str = ", ".join(bot.skills[:4])
    return (
        f"Your profile: {bot.name}, {bot.subheading}. "
        f"Core expertise: {skills_str}. "
        f"Topic to post about: {topic}. "
        f"About you (for context only): {bot.about[:180]} "
        f"\n\nWrite a unique, organic social media post on that topic. "
        f"The post must be between {cfg.POST_MIN_WORDS} and {cfg.POST_MAX_WORDS} words."
    )


def _count_words(text: str) -> int:
    return len(text.split())


def _build_image_prompt(post_content: str, bot: BotProfile) -> str:
    """
    Build a unique, highly realistic photography prompt per bot and post.

    Design principles:
    - Seeded randomisation from bot_id + post hash guarantees per-bot uniqueness
      while remaining deterministic (same inputs → same prompt).
    - Domain detection maps post topics to real-world scenes, not digital art.
    - All vocabulary is photographic; no CGI / illustration / render terms.
    - A strong negative prompt is embedded for the SDXL path; FLUX gets it too.
    """
    import hashlib

    # Seed from bot identity + post content so every (bot, post) pair is unique
    seed_str = f"{bot.bot_id}:{post_content[:120]}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % (2 ** 31)
    rng = random.Random(seed)

    content_lower = post_content.lower()

    # ── 1. Domain-specific scene pools ───────────────────────────────────────
    # Each domain has multiple concrete, real-world scene descriptions.
    # Scenes are chosen per-bot from the pool so identical topics still differ.

    if any(w in content_lower for w in [
        "climate", "carbon", "energy", "wildfire", "ocean", "emission",
        "renewable", "agriculture", "satellite", "sea level", "grid"
    ]):
        scene_pool = [
            "solar panel farm stretching across a desert plain at dawn, maintenance technician walking between rows",
            "university climate research station with weather instruments on a rooftop, overcast sky",
            "coastal erosion survey team setting up measurement equipment on a rocky shoreline",
            "wind turbine field at dusk, golden light casting long shadows across open farmland",
            "agricultural drone hovering over crop rows, farmer watching from the edge of the field",
            "flooded river valley photographed from a low-flying research aircraft, muddy water between trees",
            "geothermal power plant surrounded by steam vents in an Icelandic volcanic landscape",
            "scientist collecting core ice samples in a polar research tent, equipment scattered around",
        ]
        negative_extra = "volcano eruption, cartoon earth, space render, satellite CGI globe"

    elif any(w in content_lower for w in [
        "health", "medical", "clinical", "genomic", "drug", "patient",
        "radiology", "therapy", "wearable", "diagnosis", "hospital", "biomedical"
    ]):
        scene_pool = [
            "hospital radiology suite with MRI machine powered on, dim blue-tinted ambient lighting",
            "clinical laboratory bench with pipettes, centrifuge, and sample trays under fluorescent light",
            "researcher in white coat examining printed genomic sequencing results at a standing desk",
            "drug discovery lab with rows of automated liquid-handling robots and glassware",
            "doctor reviewing patient scan on a large diagnostic monitor in a darkened reading room",
            "wearable health device prototype on a workbench next to a laptop displaying biometric graphs",
            "nursing station in a modern hospital with staff at desks, soft overhead lighting",
            "pharmaceutical cold storage room with rows of labelled sample vials on metal shelving",
        ]
        negative_extra = "cartoon anatomy, illustrated body, 3D medical render, neon skeleton"

    elif any(w in content_lower for w in [
        "cyber", "security", "threat", "cryptograph", "vulnerab",
        "ransomware", "firewall", "incident", "intrusion", "zero-trust", "penetration"
    ]):
        scene_pool = [
            "security operations center at night, analysts seated at curved desks with multiple screens showing dashboards",
            "close-up of a server rack with blinking LEDs inside a dimly lit data center corridor",
            "IT professional plugging a network cable into a patch panel, depth of field blurring the background racks",
            "cybersecurity analyst writing on a whiteboard covered in network diagrams, office daylight behind",
            "open laptop on a desk showing a terminal with scrolling code, coffee cup beside it, morning light",
            "physical access control panel and badge reader mounted on a concrete wall in a secure facility",
            "cable management trench in a data center floor, technician kneeling beside it with a flashlight",
            "wide shot of a corporate network operations center with a large wall display and seated team",
        ]
        negative_extra = "hacker in hoodie illustration, glowing skull, cyber neon glow art, comic"

    elif any(w in content_lower for w in [
        "quantum", "neuromorph", "6g", "spatial", "autonomous",
        "edge ai", "robotics", "mixed reality", "blockchain", "chip", "semiconductor"
    ]):
        scene_pool = [
            "semiconductor fabrication cleanroom with technicians in full bunny suits at lithography equipment",
            "robotics research lab floor with a wheeled autonomous robot navigating between shelving units",
            "university electronics lab bench with oscilloscopes, breadboards, and soldering equipment",
            "close-up of a circuit board being inspected under a stereo microscope, tweezers in frame",
            "outdoor 5G antenna tower being serviced by a telecoms engineer on a lift platform",
            "quantum computing cryostat chamber open during maintenance, dilution refrigerator visible inside",
            "augmented reality headset tethered to a workstation in a sparse white prototype lab",
            "autonomous vehicle sensor rig on a car roof in a parking lot, engineers standing nearby",
        ]
        negative_extra = "glowing hologram, sci-fi spaceship, CGI robot, neon wireframe, tron grid"

    elif any(w in content_lower for w in [
        "data", "pipeline", "model", "machine learning", "dataset",
        "analytics", "feature", "training", "benchmark", "inference", "automl"
    ]):
        scene_pool = [
            "data scientist at a sit-stand desk with two monitors displaying Jupyter notebooks, afternoon light",
            "team whiteboard session with sticky notes and a marker-drawn flowchart, casual office setting",
            "rack of GPU servers inside a colocation facility, blinking lights, cool air visible as slight haze",
            "researcher presenting results on a projector screen in a small university seminar room",
            "pair of engineers reviewing code side-by-side on laptops at a coffee shop corner table",
            "open-plan technology office with developers working at desks, plants and natural window light",
            "annotator workstation with multiple reference images on screen and a data labelling interface",
            "server room hallway with a technician rolling a rack cart, overhead strip lighting",
        ]
        negative_extra = "floating data visualization art, holographic UI, CGI network globe, neon graph"

    else:
        # Generic AI / research fallback — still concrete real-world scenes
        scene_pool = [
            "AI research lab interior with whiteboards, desks, and researchers in discussion",
            "university computer science seminar with students and a lecturer, projector screen visible",
            "startup open office with standing desks, plants, and large windows letting in morning light",
            "conference room with researchers around a table, laptops open, papers spread out",
            "library study area with a researcher surrounded by printed papers and an open laptop",
            "rooftop terrace of a tech campus, engineer working on a laptop in afternoon sun",
            "collaborative workspace with a mix of desks and sofas, casual professional atmosphere",
            "graduate student desk covered in technical papers, coffee mug, desk lamp switched on",
        ]
        negative_extra = "robot, hologram, futuristic spaceship, cartoon character, sci-fi illustration"

    # ── 2. Pick a unique scene from the pool ─────────────────────────────────
    scene = rng.choice(scene_pool)

    # ── 3. Camera body & lens (varied, real models) ───────────────────────────
    camera_body = rng.choice([
        "Canon EOS R5", "Nikon Z9", "Sony A7R V", "Fujifilm GFX 100S",
        "Leica M11", "Sony A1", "Nikon D850", "Canon 5D Mark IV",
    ])
    lens = rng.choice([
        "24mm f/1.8 prime", "35mm f/1.4 prime", "50mm f/1.2 prime",
        "85mm f/1.8 portrait lens", "24–70mm f/2.8 zoom at 35mm",
        "16mm f/2.8 wide-angle", "70mm f/2.8 macro",
    ])

    # ── 4. Time of day / lighting condition ───────────────────────────────────
    lighting = rng.choice([
        "early morning soft diffused light from large windows",
        "midday overhead fluorescent office lighting",
        "late afternoon golden-hour sunlight through venetian blinds",
        "overcast daylight, even shadowless illumination",
        "evening indoor incandescent warm tone",
        "blue-hour twilight through floor-to-ceiling glass",
        "harsh midday sun with strong directional shadows",
        "desk lamp casting a warm pool of light in an otherwise dim room",
    ])

    # ── 5. Camera angle / composition ─────────────────────────────────────────
    angle = rng.choice([
        "eye-level straight-on shot",
        "slight low-angle looking up",
        "high-angle overview looking down at 30 degrees",
        "wide establishing shot",
        "medium close-up with background bokeh",
        "over-the-shoulder perspective",
        "side profile composition",
        "tight close-up with extreme shallow depth of field",
    ])

    # ── 6. Realistic photographic imperfections ───────────────────────────────
    imperfection = rng.choice([
        "very slight lens barrel distortion at edges",
        "subtle film grain, ISO 1600",
        "natural lens flare from a light source in frame",
        "slight chromatic aberration on high-contrast edges",
        "minor focus breathing, background softly out of focus",
        "real-world dust motes visible in a shaft of light",
        "natural vignetting at corners from the wide aperture",
        "micro motion blur on hands suggesting activity",
    ])

    # ── 7. Shared negative prompt text ────────────────────────────────────────
    negative_prompt = (
        "watermark, text overlay, logo, signature, brand name, "
        "digital art, illustration, painting, drawing, cartoon, anime, "
        "3D render, CGI, computer graphics, ray tracing, synthetic, "
        "unrealistic texture, plastic skin, over-saturated, HDR glow, "
        "lens distortion artefact, duplicate elements, tiling pattern, "
        "low resolution, blurry face, deformed anatomy, extra limbs, "
        f"{negative_extra}"
    )

    # ── 8. Assemble final prompt ───────────────────────────────────────────────
    positive_prompt = (
        f"Photorealistic candid photograph: {scene}. "
        f"Real-life scene, natural colors, no CGI. "
        f"Shot on {camera_body} with a {lens}. "
        f"{lighting}. "
        f"{angle}. "
        f"Photojournalism quality, {imperfection}. "
        f"Authentic, unposed, documentary-style."
    )

    # Store negative prompt as an attribute on the returned string so the
    # HF payload builder can read it without changing the function signature.
    # We embed it with a separator that the caller strips out.
    return f"{positive_prompt}|||NEG|||{negative_prompt}"


# ── Mock generators (demo mode) ───────────────────────────────────────────────

_MOCK_POSTS = [
    (
        "The conversation around {topic} has matured considerably over the past year. "
        "What used to be speculative is now backed by reproducible benchmarks. "
        "The gap between academic papers and production deployment is narrowing faster than most anticipated. "
        "Teams that invested early in robust data infrastructure are seeing compounding returns now. "
        "The bottleneck has shifted from model capability to reliable evaluation and operational tooling. "
        "We are at an inflection point where engineering discipline matters as much as research novelty. "
        "The next wave of progress will come from better abstractions, not just bigger models. "
        "Worth tracking closely if you are building in this space. #research #technology"
    ),
    (
        "After six months working deeply with {topic}, three things stand out as consistently underestimated. "
        "First, the data quality problem is an order of magnitude harder than the modeling problem. "
        "Second, interpretability is not optional when deploying in regulated industries. "
        "Third, the latency-accuracy tradeoff is rarely discussed honestly in published benchmarks. "
        "Real-world deployment surfaces constraints that controlled experiments simply cannot replicate. "
        "The field needs more practitioner voices alongside the research community to close this gap effectively. "
        "Happy to discuss what has worked and what has not in the comments. #datascience #engineering"
    ),
    (
        "A common misconception about {topic} is that it is primarily a technical challenge. "
        "In practice, the hardest problems are organizational and epistemological. "
        "Who defines success? Who owns the ground truth labels? Who audits the outputs? "
        "These questions determine whether a project delivers real value or just impressive demos. "
        "The teams I have seen succeed treat model development and stakeholder alignment as equally important workstreams. "
        "Technical excellence without institutional trust collapses under the weight of its own complexity. "
        "Building the right process is the moat, not the algorithm. #AI #professionalinsights"
    ),
    (
        "Recent advances in {topic} are forcing a reconsideration of assumptions we held for a decade. "
        "The scaling hypothesis continues to generate useful predictions, but its limits are becoming visible. "
        "Efficiency improvements at inference time are outpacing raw compute growth in practical impact. "
        "Smaller, well-trained, domain-specific models frequently outperform generalist giants on targeted tasks. "
        "The implication for research investment strategy is significant but not yet reflected in most roadmaps. "
        "Practitioners who understand this dynamic will have a meaningful advantage in the next eighteen months. "
        "The paradigm is shifting from performance at any cost to performance per dollar. #machinelearning"
    ),
    (
        "The reproducibility problem in {topic} research is more serious than the field acknowledges publicly. "
        "A significant portion of benchmark improvements do not transfer to out-of-distribution real-world data. "
        "Evaluation methodology is often designed, consciously or not, to favor novel approaches over robust baselines. "
        "Peer review rarely catches this because reviewers face the same incentive structures as authors. "
        "Some research groups are pushing for open evaluation infrastructure and shared test beds as a structural fix. "
        "This is the right direction. Science requires falsifiability, and that requires shared, tamper-resistant benchmarks. "
        "Progress is only meaningful if it is real. #research #openscience"
    ),
]


async def _mock_generate_post(bot: BotProfile) -> str:
    await asyncio.sleep(0.05)
    topic = random.choice(RESEARCH_TOPICS)
    template = random.choice(_MOCK_POSTS)
    text = template.format(topic=topic)
    # Ensure word count is within 70–100 range for mock posts too
    words = text.split()
    if len(words) > cfg.POST_MAX_WORDS:
        text = " ".join(words[:cfg.POST_MAX_WORDS])
    return text


async def _mock_generate_image(prompt: str, path: Path) -> bool:
    """Write a tiny valid PNG placeholder so downstream code doesn't break."""
    await asyncio.sleep(0.05)
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


# ── Image post-processing ─────────────────────────────────────────────────────

def _postprocess_image(image_bytes: bytes, crop_bottom_ratio: float = 0.05) -> bytes:
    """
    Post-process generated image bytes:
      1. Decode the image with PIL.
      2. Crop a strip from the bottom to remove any watermark artifacts.
      3. Re-encode as high-quality PNG and return bytes.

    Falls back to raw bytes if PIL is unavailable or decoding fails.
    """
    if not _HAS_PIL or crop_bottom_ratio <= 0:
        return image_bytes

    try:
        img = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size

        if crop_bottom_ratio > 0:
            crop_h = int(h * crop_bottom_ratio)
            # Crop bottom strip; also crop a 1-pixel border all around for clean edges
            img = img.crop((1, 1, w - 1, h - crop_h))

        # Slight sharpness enhancement for professional look
        try:
            from PIL import ImageEnhance
            img = ImageEnhance.Sharpness(img).enhance(1.15)
            img = ImageEnhance.Contrast(img).enhance(1.05)
        except Exception:
            pass  # Enhancement is optional

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True, compress_level=6)
        processed = buf.getvalue()
        logger.debug("Image post-processed: %d → %d bytes", len(image_bytes), len(processed))
        return processed

    except Exception as exc:
        logger.warning("Image post-processing failed (%s); using original bytes.", exc)
        return image_bytes


# ── Gemini text generation ────────────────────────────────────────────────────

async def _gemini_generate_post(bot: BotProfile, session: aiohttp.ClientSession) -> str:
    """Call the Gemini REST API to generate a 70–100 word post."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{cfg.GEMINI_MODEL}:generateContent"
    )
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": cfg.GEMINI_API_KEY,
    }
    payload = {
        "system_instruction": {
            "parts": [{"text": POST_SYSTEM_PROMPT}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": _build_post_prompt(bot)}]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": cfg.LLM_MAX_TOKENS,
            "temperature": cfg.LLM_TEMPERATURE,
        },
    }
    try:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Gemini API error %d: %s", resp.status, body[:200])
                return await _mock_generate_post(bot)
            data = await resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

            # Enforce word count bounds
            words = text.split()
            if len(words) > cfg.POST_MAX_WORDS:
                # Trim to max, ending at sentence boundary if possible
                truncated = " ".join(words[:cfg.POST_MAX_WORDS])
                last_period = truncated.rfind(".")
                if last_period > len(truncated) * 0.7:
                    text = truncated[:last_period + 1]
                else:
                    text = truncated
            elif len(words) < cfg.POST_MIN_WORDS:
                # Too short – regenerate with mock for reliability
                logger.debug("Post too short (%d words); using mock fallback.", len(words))
                return await _mock_generate_post(bot)

            return text
    except Exception as exc:
        logger.warning("Gemini request failed (%s). Using mock.", exc)
        return await _mock_generate_post(bot)


# ── Hugging Face image generation ─────────────────────────────────────────────

async def _hf_generate_image(
    prompt: str, path: Path, session: aiohttp.ClientSession
) -> bool:
    """
    Call the HF router endpoint (router.huggingface.co/hf-inference) for image generation.
    Uses FLUX.1-schnell (primary) or SDXL (fallback).
    Post-processes the image to remove watermark artifacts before saving.

    The `prompt` argument may contain an embedded negative prompt separated by
    '|||NEG|||' — this function splits it and uses each part correctly.
    """
    # Split positive and negative prompts produced by _build_image_prompt
    if "|||NEG|||" in prompt:
        positive_prompt, negative_prompt = prompt.split("|||NEG|||", 1)
        positive_prompt = positive_prompt.strip()
        negative_prompt = negative_prompt.strip()
    else:
        positive_prompt = prompt.strip()
        negative_prompt = (
            "watermark, text overlay, logo, digital art, illustration, 3D render, "
            "CGI, cartoon, anime, oversaturated, blurry, duplicate, unrealistic"
        )

    headers = {
        "Authorization": f"Bearer {cfg.HF_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "image/png",
    }

    # The hf-inference router accepts the standard text-to-image body.
    # Keep parameters minimal — the router ignores or rejects unknown fields.
    payload_flux = {
        "inputs": positive_prompt,
        "parameters": {
            "width": cfg.IMAGE_WIDTH,
            "height": cfg.IMAGE_HEIGHT,
            "num_inference_steps": 4,
        },
    }

    payload_sdxl = {
        "inputs": positive_prompt,
        "parameters": {
            "width": min(cfg.IMAGE_WIDTH, 1024),
            "height": min(cfg.IMAGE_HEIGHT, 1024),
            "num_inference_steps": 25,
            "negative_prompt": negative_prompt,
        },
    }

    async def _try_endpoint(api_url: str, payload: dict) -> Optional[bytes]:
        try:
            async with session.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    # Validate it looks like an image (PNG/JPEG magic bytes)
                    if len(data) > 100 and (data[:4] == b'\x89PNG' or data[:2] == b'\xff\xd8'):
                        return data
                    logger.warning("HF endpoint returned 200 but non-image data (%d bytes)", len(data))
                    return None
                body = await resp.text()
                logger.warning(
                    "HF image API %s → %d: %s",
                    api_url.split("/")[-1], resp.status, body[:300]
                )
                return None
        except Exception as exc:
            logger.warning("HF image request failed for %s: %s", api_url.split("/")[-1], exc)
            return None

    # Try primary (FLUX.1-schnell) first, then fallback (SDXL)
    image_bytes = await _try_endpoint(cfg.HF_IMAGE_API_URL, payload_flux)
    if not image_bytes:
        logger.info("FLUX endpoint failed, falling back to SDXL.")
        image_bytes = await _try_endpoint(cfg.HF_IMAGE_API_URL_FALLBACK, payload_sdxl)

    if not image_bytes:
        logger.warning("Both HF image endpoints failed; using mock placeholder.")
        return await _mock_generate_image(prompt, path)

    # Post-process: crop watermark strip and enhance image quality
    clean_bytes = _postprocess_image(image_bytes, crop_bottom_ratio=cfg.IMAGE_CROP_BOTTOM_RATIO)
    path.write_bytes(clean_bytes)
    logger.debug("Image saved: %s (%d bytes after post-processing)", path, len(clean_bytes))
    return True


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
        """Generate a 70–100 word social-media post on a real-world research topic."""
        if cfg.DEMO_MODE or cfg.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY" or not _HAS_AIOHTTP:
            return await _mock_generate_post(bot)
        return await _gemini_generate_post(bot, self._session)

    async def generate_image(self, post_content: str, bot: BotProfile) -> Tuple[bool, Path]:
        """
        Generate a high-quality photorealistic image matching the post.
        Post-processes the result to remove watermarks/artifacts.
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
