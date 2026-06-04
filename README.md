# AI Social Bot Simulator

An autonomous AI-powered social media simulation engine.  
Generates research-quality posts using Google Gemini, produces photorealistic images with HuggingFace FLUX, and publishes directly to [Tayog](https://tayog.com) via the production REST API — with optional local JSON backup.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Quick Start](#quick-start)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Environment Variables](#environment-variables)
7. [Tayog Integration](#tayog-integration)
8. [Running the Simulator](#running-the-simulator)
9. [Dry-Run Mode](#dry-run-mode)
10. [Analytics](#analytics)
11. [Logging](#logging)
12. [Troubleshooting](#troubleshooting)
13. [Deployment](#deployment)
14. [Performance Notes](#performance-notes)
15. [Developer Notes](#developer-notes)

---

## Project Overview

The AI Social Bot Simulator manages a fleet of autonomous researcher bot personas. Each bot:

- Has a unique identity (name, specialisation, skills, about section)
- Generates 70–100 word expert posts on real-world research topics using Gemini
- Produces matching photorealistic images via HuggingFace FLUX.1-schnell
- Publishes to Tayog using the multipart REST API
- Falls back to mock content when API keys are absent (demo mode)

**Supported output targets:**

| Target | Description |
|---|---|
| Tayog API | Posts published to MongoDB + Cloudflare R2 via `POST /api/posts/new/v1` |
| Local JSON | Posts saved to `posts.json` for inspection and replay |
| Both | Default configuration (dual-write) |

---

## Architecture

```
BotProfile objects
       │
       ▼
 ContentGenerator
  ├─ Gemini REST API  (70–100 word expert posts)
  └─ HuggingFace API  (FLUX.1-schnell photorealistic images)
       │
       ├─────────────────────────────────────┐
       ▼                                     ▼
 TayogClient                           PostStorage
  ├─ BotUserMapping                    (posts.json — optional)
  ├─ asyncio.Semaphore throttle
  ├─ Exponential backoff retry
  ├─ UploadAnalytics
  └─ UploadLogger (tayog_upload.log)
       │
       ▼
  Tayog API  POST /api/posts/new/v1
  → Cloudflare R2  (media)
  → MongoDB        (posts, media, hashtags, mentions)
```

**Key files:**

| File | Role |
|---|---|
| `main.py` | Entry point, CLI argument parsing, terminal UI |
| `config.py` | All configuration via environment variables |
| `bots.py` | Bot persona generation |
| `generator.py` | Gemini text + HuggingFace image generation |
| `scheduler.py` | Async batch execution engine |
| `storage.py` | Thread-safe local JSON persistence |
| `tayog_client.py` | Tayog API client (auth, multipart, retry, analytics) |
| `bot_user_mapping.json` | Maps simulator `bot_id` → Tayog `userId` |

---

## Quick Start

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd ai-social-bot-simulator

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 5. Run in demo mode (no API keys needed)
python main.py --demo --bots 5
```

---

## Installation

**Requirements:**

- Python 3.11 or newer
- pip or pipx
- Internet access (for Gemini, HuggingFace, Tayog APIs)

**Install dependencies:**

```bash
pip install -r requirements.txt
```

**Verify installation:**

```bash
python -c "import aiohttp, PIL; print('OK')"
```

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and edit:

```bash
cp .env.example .env
```

The `.env` file is loaded automatically when `python-dotenv` is installed. Shell environment variables always take precedence over `.env` values.

**Validation:** At startup, `config.py` emits `WARNING`-level log messages for any missing or placeholder values. The simulator will still run; missing API keys fall back to demo/mock mode.

---

## Environment Variables

### Required for live operation

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API key |
| `HF_API_TOKEN` | Hugging Face access token |
| `TAYOG_BASE_URL` | Tayog instance base URL (e.g. `https://yourapp.com`) |
| `TAYOG_SECRET_KEY` | Value sent as `x_secrect` in every post request |

### Feature flags

| Variable | Default | Description |
|---|---|---|
| `ENABLE_TAYOG_UPLOAD` | `true` | Upload to Tayog after generation |
| `ENABLE_LOCAL_STORAGE` | `true` | Save to `posts.json` locally |
| `DRY_RUN_MODE` | `false` | Validate payloads without uploading |
| `ENABLE_MENTIONS` | `false` | Append @mentions to posts |
| `DEMO_MODE` | `false` | Use mock APIs, no keys needed |

### Scaling

| Variable | Default | Description |
|---|---|---|
| `TOTAL_BOTS` | `10` | Number of bots to run |
| `BATCH_SIZE` | `1` | Bots per concurrent batch |
| `MAX_CONCURRENT_REQUESTS` | `5` | Global async concurrency cap |
| `MAX_CONCURRENT_UPLOADS` | `3` | Tayog upload concurrency cap |
| `DELAY_BETWEEN_BATCHES` | `4.0` | Pause between batches (seconds) |
| `TAYOG_MAX_RETRIES` | `4` | Upload retry attempts (exponential backoff) |

See `.env.example` for the full list.

---

## Tayog Integration

### Bot → User Mapping

Every bot must be mapped to a real Tayog `userId` in `bot_user_mapping.json`:

```json
{
  "bot_001": "clxyz_actual_tayog_user_id",
  "bot_002": "clxyz_another_user_id"
}
```

Bots without a mapping are **skipped** (logged at `WARN` level) — they do not block other uploads.

**Finding Tayog user IDs:** Query your MongoDB directly or add a temporary admin endpoint:

```javascript
// Prisma example
const users = await prisma.user.findMany({ select: { id: true, email: true } });
```

### API Contract

```
POST /api/posts/new/v1
Content-Type: multipart/form-data

fields:
  files          (binary)         image file
  fileMetadata   (application/json)  {"type": "image", "sortOrder": 1}
  postData       (application/json)  {"x_secrect": "...", "content": "...", "userId": "..."}

success: HTTP 201
```

Constraints: max 7 files, max 6 images, max 1 video.

### Validation steps

1. Payload validation runs **before** the HTTP call (catches missing fields early)
2. HTTP 5xx responses trigger exponential-backoff retry (up to `TAYOG_MAX_RETRIES`)
3. HTTP 401 / 404 fail immediately (not retried — configuration error)
4. Results are written to `tayog_upload.log` as JSON lines

---

## Running the Simulator

```bash
# Standard run (uses .env config)
python main.py

# Override bots and batch size
python main.py --bots 100 --batch 5

# Demo mode — no API keys needed
python main.py --demo --bots 10

# Disable Tayog upload for this run only
python main.py --no-tayog

# Clear previous posts before starting
python main.py --clear

# Show storage stats and exit
python main.py --stats
```

---

## Dry-Run Mode

Dry-run mode builds and validates every Tayog payload — including resolving
`userId` mappings and checking image files — without sending any HTTP requests.

```bash
# Via CLI flag
python main.py --dry-run --bots 10

# Via environment variable
DRY_RUN_MODE=true python main.py
```

Check `tayog_upload.log` after a dry-run; each entry will have `"status": "skipped"` and `"error": "dry_run_mode"`.

---

## Analytics

### Live stats (after a run)

```bash
python main.py --stats
```

Output:
```
Storage stats:
  total_posts           47
  unique_bots           10
  tayog_uploaded        45
  tayog_failed          2
  tayog_pending         0
  tayog_skipped         0
  storage_file          /path/to/posts.json
  file_size_kb          128.4
```

### Tayog upload analytics

```bash
python main.py --tayog-stats
```

Output includes: upload counts by status, success rate, sample post URLs.

### Session analytics

At the end of every live run, the scheduler logs a full analytics report:

```
=======================================================
  TAYOG UPLOAD ANALYTICS
=======================================================
  Attempted   : 10
  Success     : 9  (90.0%)
  Failed      : 1
  Skipped     : 0
  Total Retry : 2
  Avg Latency : 843 ms
-------------------------------------------------------
  HTTP Status Counts:
    HTTP 201  →  9
    HTTP 503  →  2
  ...
```

---

## Logging

### simulator.log

Main application log. Format: `timestamp | LEVEL | module | message`

### tayog_upload.log

Structured JSON-lines upload audit log. One line per upload attempt:

```json
{"timestamp": "2025-01-15T10:30:00+00:00", "bot_id": "bot_001", "user_id": "clxyz...", "status": "success", "http_code": 201, "post_id": "post_abc", "retries": 0, "latency_ms": 712.3, "error": null}
{"timestamp": "2025-01-15T10:30:02+00:00", "bot_id": "bot_002", "user_id": null, "status": "skipped", "http_code": null, "post_id": null, "retries": 0, "latency_ms": 0, "error": "No userId mapping found"}
```

Parse with `jq`:
```bash
# Count by status
jq -s 'group_by(.status) | map({status: .[0].status, count: length})' tayog_upload.log

# Failed uploads
jq 'select(.status == "failed")' tayog_upload.log
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| All posts `skipped` | Missing `bot_user_mapping.json` entries | Populate with real Tayog user IDs |
| HTTP 401 on every upload | Wrong `TAYOG_SECRET_KEY` | Verify the `x_secrect` value matches your Tayog backend |
| HTTP 404 | `userId` doesn't exist in Tayog DB | Verify user IDs against MongoDB/Prisma |
| Images are 1×1 placeholders | Missing `HF_API_TOKEN` or demo mode | Set `HF_API_TOKEN` and ensure `DEMO_MODE=false` |
| Text posts only (no content) | Missing `GEMINI_API_KEY` | Set `GEMINI_API_KEY` |
| `aiohttp not found` | Missing dependency | `pip install aiohttp` |
| `PIL not found` | Missing dependency | `pip install Pillow` |
| `posts.json` corrupt | Crash during write | File is quarantined to `posts.corrupt.<ts>.json`; a fresh file is created |
| Timeout retries on every upload | Large images or slow network | Reduce `IMAGE_WIDTH`/`IMAGE_HEIGHT` or increase `TAYOG_MAX_RETRIES` |
| Startup warning: placeholder URL | `TAYOG_BASE_URL` not set | Set `TAYOG_BASE_URL` in `.env` |

---

## Deployment

### Basic server deployment

```bash
# Install in a virtual environment
python -m venv /opt/bot-simulator
source /opt/bot-simulator/bin/activate
pip install -r requirements.txt

# Copy project files
cp -r . /opt/bot-simulator/app/
cd /opt/bot-simulator/app/

# Create production .env
cp .env.example .env
nano .env   # fill in real values

# Run with output logging
python main.py --bots 100 >> /var/log/bot-simulator.log 2>&1
```

### Cron / scheduled runs

```cron
# Run 100 bots every hour
0 * * * * cd /opt/bot-simulator/app && /opt/bot-simulator/bin/python main.py --bots 100 >> /var/log/bot-sim-cron.log 2>&1
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

```bash
docker build -t bot-simulator .
docker run --env-file .env bot-simulator
```

---

## Performance Notes

| Scale | Recommended settings |
|---|---|
| 10–100 bots | `BATCH_SIZE=5`, `MAX_CONCURRENT_REQUESTS=5`, `MAX_CONCURRENT_UPLOADS=3` |
| 100–1 000 bots | `BATCH_SIZE=10`, `MAX_CONCURRENT_REQUESTS=10`, `MAX_CONCURRENT_UPLOADS=5`, `DELAY_BETWEEN_BATCHES=2.0` |
| 1 000–10 000 bots | Distribute across multiple processes / machines; migrate storage to SQLite or MongoDB |

**Bottlenecks at scale:**
- `posts.json` is a full read-modify-write on every save: migrate to SQLite for > 5 000 posts
- HuggingFace image generation is ~5–15 s per image: tune `MAX_CONCURRENT_REQUESTS`
- Tayog upload latency dominates at high concurrency: tune `MAX_CONCURRENT_UPLOADS`

---

## Developer Notes

- **No hardcoded secrets**: all API keys are loaded from environment variables only
- **Backward compatible storage**: `Post.from_dict()` tolerates missing fields from older `posts.json` files
- **Session sharing**: the `aiohttp.ClientSession` is created once in the scheduler and shared with both `ContentGenerator` and `TayogClient` to maximise connection reuse
- **Atomic writes**: `posts.json` is written via temp-file rename; readers never see a partial file
- **Double-checked locking**: `get_storage()` singleton uses a lock-within-lock pattern safe for concurrent initialisation
- **Structured upload log**: `tayog_upload.log` is JSON-lines format, suitable for ingestion by log aggregators (Datadog, Loki, etc.)
- **Dry-run first**: always validate with `--dry-run` before a production run against a new Tayog instance

