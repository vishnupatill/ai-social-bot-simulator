# AI Social Bot Simulator 🤖

A fully autonomous system that simulates **1 000 AI-powered social media bots** — each with a unique identity, organic posts, and AI-generated images.

---

## Features

| Feature | Detail |
|---|---|
| 🤖 Bot profiles | 1 000 unique profiles with UUID, name, skills, education, experience |
| ✍️ LLM posts | Human-like posts via Google Gemini 1.5 Flash |
| 🖼️ AI images | Stable Diffusion XL via Hugging Face Inference API (free) |
| ⚡ Async engine | Batched async execution – respects API rate limits |
| 📅 Scheduler | Configurable delays between bots and batches |
| 💾 Storage | Append-safe JSON persistence (`posts.json`) |
| 📋 Logging | File + console logging (`simulator.log`) |
| 🧪 Demo mode | Fully functional without API keys |

---

## Project Structure

```
ai_social_bot_simulator/
├── main.py          # CLI entry point
├── config.py        # API keys & all settings
├── bots.py          # Bot profile generation (1 000 agents)
├── generator.py     # LLM text + image generation
├── scheduler.py     # Batch/async scheduling engine
├── storage.py       # Thread-safe JSON persistence
├── requirements.txt
├── images/          # AI-generated images (auto-created)
├── posts.json       # All posts (auto-created)
├── bots.json        # Cached bot profiles (auto-created)
└── simulator.log    # Log file (auto-created)
```

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set API keys (optional – demo mode works without them)
```bash
export GEMINI_API_KEY="your-gemini-key"
export HF_API_TOKEN="your-hf-token"
```

**Getting free API keys:**
- **Gemini:** https://aistudio.google.com/app/apikey (free tier: 15 RPM)
- **Hugging Face:** https://huggingface.co/settings/tokens (free inference API)

### 3. Run

```bash
# Demo mode – no API keys needed, instant results
python main.py --bots 10 --demo

# Full run with real APIs (start small!)
python main.py --bots 50 --batch 5 --delay 3

# All 1 000 bots
python main.py

# Show storage stats
python main.py --stats

# Clear previous posts and rerun
python main.py --bots 20 --demo --clear
```

---

## CLI Options

| Flag | Default | Description |
|---|---|---|
| `--bots N` | 1000 | Number of bots to activate |
| `--batch N` | 10 | Bots per batch |
| `--delay N` | 5.0 | Seconds between batches |
| `--demo` | off | Use mock APIs (no keys needed) |
| `--clear` | off | Wipe posts before run |
| `--stats` | off | Print stats and exit |

---

## Output Format

### Terminal
```
────────────────────────────────────────────────────────────
[   1/50]  Arjun Sharma  (a1b2c3d4…)  2%
2025-01-15 10:30:42 UTC

Just refactored our entire Python pipeline from scratch.
The key insight? Complexity is almost always a symptom of
unclear requirements. When you truly understand the problem,
the solution simplifies itself. #softwareengineering

📷 images/a1b2c3d4-xxxx.png
```

### posts.json
```json
[
  {
    "bot_id": "a1b2c3d4-...",
    "name": "Arjun Sharma",
    "content": "Just refactored our entire Python pipeline...",
    "image_path": "images/a1b2c3d4-....png",
    "timestamp": "2025-01-15T10:30:42+00:00"
  }
]
```

---

## Rate Limit Strategy

| API | Free Tier | Strategy |
|---|---|---|
| Gemini 1.5 Flash | 15 RPM, 1M TPD | `BATCH_SIZE=10`, `DELAY_BETWEEN_BATCHES=5s` |
| HF Inference API | ~10 RPM | Same batching + semaphore cap |

Tune `config.py` → `MAX_CONCURRENT_REQUESTS` and `DELAY_BETWEEN_BATCHES` to match your tier.

---

## Configuration

All settings live in `config.py`.  Override via env vars or edit the file directly.

```python
cfg.TOTAL_BOTS = 1000
cfg.BATCH_SIZE = 10
cfg.DELAY_BETWEEN_BATCHES = 5.0
cfg.MAX_CONCURRENT_REQUESTS = 5
cfg.DEMO_MODE = False
```

---

## Architecture

```
main.py
  └─ generate_all_bots()       [bots.py]
  └─ BotScheduler.run()        [scheduler.py]
       └─ _run_batch()
            └─ ContentGenerator [generator.py]
                 ├─ generate_post()   → Gemini API / mock
                 └─ generate_image()  → HF API / mock
       └─ get_storage().save_post()   [storage.py]
```
