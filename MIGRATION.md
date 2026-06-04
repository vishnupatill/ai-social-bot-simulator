# Tayog Integration — Migration Guide

## Overview

This guide covers upgrading the AI Social Bot Simulator to publish generated
posts directly to Tayog's production API while keeping the existing local JSON
storage as an optional backup.

---

## Files Changed

| File | Change |
|---|---|
| `config.py` | Added Tayog config block (URL, secret, flags, retry settings) |
| `storage.py` | `Post` dataclass extended with 3 Tayog result fields; `from_dict` is backward-compatible |
| `scheduler.py` | Instantiates `TayogClient` when `ENABLE_TAYOG_UPLOAD=True`; uploads each post after generation |
| `generator.py` | Expanded hashtag pool + `_pick_hashtags()` / `_pick_mentions()` helpers |
| `main.py` | Added `--tayog-stats`, `--dry-run`, `--no-tayog` CLI flags |

## New Files

| File | Purpose |
|---|---|
| `tayog_client.py` | Async Tayog API client (auth, multipart upload, retry, analytics, logging) |
| `bot_user_mapping.json` | Maps simulator `bot_id` → real Tayog `userId` |

---

## Step-by-Step Setup

### 1. Set environment variables

```bash
export TAYOG_BASE_URL="https://your-tayog-domain.com"
export TAYOG_SECRET_KEY="your_x_secret_key_value"

# Optional flags (defaults shown)
export ENABLE_TAYOG_UPLOAD="true"
export ENABLE_LOCAL_STORAGE="true"
export DRY_RUN_MODE="false"
```

### 2. Fill `bot_user_mapping.json`

For every bot you want to publish as, add a mapping:

```json
{
  "bot_001": "clxyz_tayog_user_id_1",
  "bot_002": "clxyz_tayog_user_id_2"
}
```

> **How to find Tayog user IDs:** Query your Prisma/MongoDB directly, or add a
> temporary `/api/debug/users` endpoint that returns `{ id, email }` pairs.

Bots with no mapping entry are **silently skipped** (logged at WARN level).

### 3. Install dependencies

```bash
pip install aiohttp pillow
```

### 4. Dry-run first

```bash
python main.py --dry-run --bots 5
```

This validates every payload (content not empty, files present, mapping
resolved) without actually hitting the Tayog API. Check `tayog_upload.log`
for any `SKIPPED` entries.

### 5. Full run

```bash
python main.py --bots 10
```

### 6. Check analytics

```bash
python main.py --tayog-stats
```

---

## Configuration Reference

All new keys live in `config.py` under `Config`:

| Key | Default | Description |
|---|---|---|
| `TAYOG_BASE_URL` | `https://yourapp.com` | Base URL of your Tayog instance |
| `TAYOG_SECRET_KEY` | `""` | Value for `x_secrect` header |
| `TAYOG_BOT_MAPPING_FILE` | `bot_user_mapping.json` | Path to the mapping file |
| `ENABLE_TAYOG_UPLOAD` | `true` | Upload to Tayog after generation |
| `ENABLE_LOCAL_STORAGE` | `true` | Also save to `posts.json` locally |
| `DRY_RUN_MODE` | `false` | Build payloads but don't send |
| `ENABLE_MENTIONS` | `False` | Append random `@mention` from `MENTION_POOL` |
| `MAX_CONCURRENT_UPLOADS` | `3` | Semaphore cap for Tayog upload concurrency |
| `TAYOG_MAX_RETRIES` | `4` | Retry attempts (exponential backoff: 1s→2s→4s→8s) |
| `TAYOG_UPLOAD_LOG` | `tayog_upload.log` | Structured upload audit log |

---

## Architecture Diagram

```
BotProfile objects
       │
       ▼
 ContentGenerator
  (Gemini LLM + HuggingFace image)
       │
       ├─────────────────────────────────┐
       ▼                                 ▼
 TayogClient                       PostStorage
  (aiohttp multipart)              (posts.json)
  ├── BotUserMapping                    │
  ├── Semaphore throttle                │
  ├── Exponential retry                 │
  ├── UploadAnalytics                   │
  └── UploadLogger (tayog_upload.log)   │
       │                                │
       ▼                                ▼
  Tayog API                       Local filesystem
  POST /api/posts/new/v1          (optional backup)
  → MongoDB + R2 storage
```

---

## Backward Compatibility

`Post.from_dict()` now tolerates records missing the three new fields
(`tayog_post_id`, `tayog_upload_status`, `tayog_upload_timestamp`), so existing
`posts.json` files load without errors. Old posts default to
`tayog_upload_status = "pending"`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| All posts `SKIPPED` | Missing mapping entries | Populate `bot_user_mapping.json` |
| HTTP 401 | Wrong `TAYOG_SECRET_KEY` | Check `x_secrect` env var matches `.env` |
| HTTP 404 | userId doesn't exist in Tayog DB | Verify user IDs against Prisma/Mongo |
| Timeout retries | Slow network or large images | Reduce `IMAGE_WIDTH/HEIGHT` or increase `TAYOG_MAX_RETRIES` |
| `aiohttp not found` | Missing dep | `pip install aiohttp` |
