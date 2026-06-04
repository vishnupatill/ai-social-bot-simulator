# Testing Guide — AI Social Bot Simulator

This document covers all testing scenarios: local unit testing, dry-run
validation, Tayog integration testing, load testing, and failure injection.

---

## Quick Reference

```bash
# 1. Demo run (no API keys, mock content)
python main.py --demo --bots 5

# 2. Dry-run (builds payloads, validates, does not upload)
python main.py --dry-run --bots 5

# 3. Live Tayog test (real upload, small batch)
python main.py --bots 2 --no-local  # or use .env with all keys set

# 4. Unit tests (requires pytest)
pytest tests/ -v

# 5. Load test (100 bots, demo mode, measure throughput)
python main.py --demo --bots 100 --batch 10
```

---

## 1. Local Testing (No API Keys)

Use demo mode to test the full pipeline without any external API calls.

### Setup

No additional setup. Demo mode uses built-in mock content.

### Running

```bash
python main.py --demo --bots 10
```

**What is tested:**
- Bot profile generation
- Mock text generation (pre-written post templates)
- Mock image generation (1×1 PNG placeholder)
- Storage: `posts.json` is created and populated
- Scheduler: batching, semaphore throttle, callbacks
- Terminal output rendering

**Expected output:**
- 10 posts written to `posts.json`
- `simulator.log` created
- No Tayog-related errors (upload is skipped or uses demo results)

### Verification

```bash
python main.py --stats
# Expect: total_posts=10, unique_bots=10

cat posts.json | python -m json.tool | head -50
# Expect: valid JSON array with 10 post objects
```

---

## 2. Dry-Run Testing

Validates the full Tayog payload pipeline without sending any HTTP requests.

### Setup

Populate `bot_user_mapping.json` with at least a few real or test entries:

```json
{
  "bot_001": "test_user_id_001",
  "bot_002": "test_user_id_002"
}
```

Set the required environment variables (even placeholder values work for dry-run validation):

```bash
export TAYOG_BASE_URL=https://your-tayog-domain.com
export TAYOG_SECRET_KEY=test_secret_key
export ENABLE_TAYOG_UPLOAD=true
```

### Running

```bash
python main.py --dry-run --bots 5 --demo
```

**What is tested:**
- `BotUserMapping` loading and resolution
- Payload validation (`_validate_payload`)
- `UploadResult` construction
- `UploadLogger` — writes to `tayog_upload.log`
- `UploadAnalytics` — all counters

**Expected output:**
- All posts have `tayog_upload_status = "skipped"`
- `tayog_upload.log` has one JSON line per bot: `"status": "skipped", "error": "dry_run_mode"`
- No HTTP requests are made

### Verification

```bash
cat tayog_upload.log | head -5
# Expect: JSON lines with "status": "skipped"

python main.py --tayog-stats
# Expect: stats showing skipped count = number of mapped bots
```

---

## 3. Tayog Integration Testing

### 3.1 Connectivity Check

Before a full run, verify the Tayog endpoint is reachable:

```bash
curl -X POST https://your-tayog-domain.com/api/posts/new/v1 \
  -F 'postData={"x_secrect":"your_secret","content":"test","userId":"invalid_user"}' \
  -v
# Expect: HTTP 4xx (not a connection error)
```

### 3.2 Single Bot Upload Test

```bash
# Map exactly one bot
echo '{"bot_001": "your_real_tayog_user_id"}' > bot_user_mapping.json

TAYOG_BASE_URL=https://your-tayog-domain.com \
TAYOG_SECRET_KEY=your_secret \
ENABLE_TAYOG_UPLOAD=true \
GEMINI_API_KEY=your_key \
HF_API_TOKEN=your_token \
python main.py --bots 1
```

**Expected results:**
- 1 post generated with real AI content
- 1 image generated (FLUX or SDXL)
- Post uploaded to Tayog (HTTP 201)
- `tayog_upload.log` shows `"status": "success"` with a `post_id`
- Post visible in Tayog at `TAYOG_BASE_URL/posts/<post_id>`

### 3.3 Retry Behaviour Test

To test exponential backoff, temporarily point `TAYOG_BASE_URL` at a non-responsive endpoint:

```bash
TAYOG_BASE_URL=https://httpstat.us/503 \
TAYOG_SECRET_KEY=test \
ENABLE_TAYOG_UPLOAD=true \
python main.py --demo --bots 1
```

**Expected behaviour:**
- 4 retry attempts (delays: ~1s, ~2s, ~4s, ~8s)
- Final status: `"failed"` with `"error": "HTTP 503: ..."`
- `tayog_upload.log` shows `retry_count=4`

### 3.4 Authentication Failure Test

```bash
TAYOG_BASE_URL=https://your-tayog-domain.com \
TAYOG_SECRET_KEY=wrong_secret \
ENABLE_TAYOG_UPLOAD=true \
python main.py --demo --bots 1
```

**Expected behaviour:**
- HTTP 401 received
- No retry (4xx is not retried)
- Log shows `"status": "failed", "http_code": 401`

---

## 4. Load Testing

### 4.1 Demo mode throughput

Measure scheduler throughput without API rate limits:

```bash
time python main.py --demo --bots 1000 --batch 50
```

**Baseline targets (demo mode, local machine):**
- 100 bots: < 30 s
- 1 000 bots: < 5 min

### 4.2 Concurrency stress test

```bash
# Max concurrency
MAX_CONCURRENT_REQUESTS=20 \
MAX_CONCURRENT_UPLOADS=10 \
python main.py --demo --bots 500 --batch 50
```

**Monitor for:**
- No `RuntimeError: Event loop is closed` errors
- No `asyncio.InvalidStateError`
- No lock deadlocks (scheduler should complete)
- Memory stays stable (no unbounded growth)

### 4.3 Storage stress test

Validate atomic writes under concurrent load:

```bash
# Run two processes simultaneously (simulates concurrent writers)
python main.py --demo --bots 50 &
python main.py --demo --bots 50 &
wait

python main.py --stats
# Expect: total_posts = 100 (no corruption)
```

---

## 5. Failure Injection Testing

### 5.1 Missing mapping file

```bash
mv bot_user_mapping.json bot_user_mapping.json.bak
python main.py --demo --bots 3
# Expect: WARNING "bot_user_mapping.json not found", all uploads skipped
mv bot_user_mapping.json.bak bot_user_mapping.json
```

### 5.2 Corrupt posts.json

```bash
echo "CORRUPT DATA {{{" > posts.json
python main.py --demo --bots 3
# Expect: WARNING "Corrupt posts file quarantined", fresh posts.json created
ls posts.corrupt.*.json   # quarantined backup
```

### 5.3 Missing image file

Manually delete an image after generation:

```python
# In a test script
import asyncio, os
from tayog_client import TayogClient, BotUserMapping

async def test():
    mapping = BotUserMapping("bot_user_mapping.json")
    client = TayogClient(
        base_url="https://httpbin.org",
        secret_key="test",
        mapping=mapping,
    )
    async with client:
        result = await client.upload_post(
            bot_id="bot_001",
            content="Test post",
            image_path="/nonexistent/image.png",
        )
    print(result)
    # Expect: upload proceeds as text-only (warning logged)

asyncio.run(test())
```

### 5.4 Keyboard interrupt

```bash
python main.py --demo --bots 100 --batch 5
# Press Ctrl+C mid-run
# Expect: "Interrupted by user. Partial results saved."
python main.py --stats
# Expect: partial count in total_posts
```

---

## 6. Unit Tests

### Setup

```bash
pip install pytest pytest-asyncio aioresponses
```

### Test file: `tests/test_storage.py`

```python
import json, pytest
from pathlib import Path
from storage import PostStorage, Post

def test_save_and_load(tmp_path):
    store = PostStorage(str(tmp_path / "posts.json"))
    post = Post(
        bot_id="bot_001", name="Alice",
        content="Hello world", image_path="img.png",
        timestamp="2025-01-01T00:00:00+00:00",
    )
    store.save_post(post)
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].content == "Hello world"

def test_corrupt_recovery(tmp_path):
    path = tmp_path / "posts.json"
    path.write_text("INVALID JSON", encoding="utf-8")
    store = PostStorage(str(path))
    # Should recover without raising
    posts = store.load_all()
    assert posts == []
    # A quarantine file should exist
    quarantine = list(tmp_path.glob("posts.corrupt.*.json"))
    assert len(quarantine) == 1

def test_atomic_write_thread_safety(tmp_path):
    import threading
    store = PostStorage(str(tmp_path / "posts.json"))
    errors = []

    def writer(i):
        try:
            post = Post(
                bot_id=f"bot_{i:03d}", name=f"Bot {i}",
                content="concurrent", image_path="x.png",
                timestamp=f"2025-01-01T00:00:0{i % 10}+00:00",
            )
            store.save_post(post)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    assert store.count() == 20
```

### Test file: `tests/test_config.py`

```python
import os, pytest
from config import Config

def test_missing_keys_produce_warnings():
    c = Config()
    c.GEMINI_API_KEY = ""
    c.HF_API_TOKEN = ""
    c.ENABLE_TAYOG_UPLOAD = True
    c.TAYOG_SECRET_KEY = ""
    warnings = c.validate()
    assert any("GEMINI_API_KEY" in w for w in warnings)
    assert any("TAYOG_SECRET_KEY" in w for w in warnings)

def test_placeholder_url_warning():
    c = Config()
    c.ENABLE_TAYOG_UPLOAD = True
    c.TAYOG_BASE_URL = "https://yourapp.com"
    c.TAYOG_SECRET_KEY = "real_key"
    warnings = c.validate()
    assert any("TAYOG_BASE_URL" in w for w in warnings)
```

### Test file: `tests/test_tayog_client.py`

```python
import asyncio, json, pytest
from unittest.mock import MagicMock, AsyncMock
from tayog_client import (
    TayogClient, BotUserMapping, UploadAnalytics,
    _validate_payload, extract_image_metadata,
)

def test_validate_payload_missing_user():
    errors = _validate_payload("", "content", "secret", [])
    assert any("userId" in e for e in errors)

def test_validate_payload_empty_content():
    errors = _validate_payload("user123", "   ", "secret", [])
    assert any("content" in e for e in errors)

def test_validate_payload_valid():
    errors = _validate_payload("user123", "Hello world", "secret", [])
    assert errors == []

def test_analytics_success_rate():
    from tayog_client import UploadResult
    a = UploadAnalytics()
    r = UploadResult(bot_id="bot_001", tayog_upload_status="success", latency_ms=500)
    a.record(r)
    assert a.success_rate == 100.0

def test_analytics_mixed():
    from tayog_client import UploadResult
    a = UploadAnalytics()
    for status in ["success", "success", "failed", "skipped"]:
        r = UploadResult(bot_id="bot_001", tayog_upload_status=status)
        a.record(r)
    assert a.total_attempted == 4
    assert a.total_success == 2
    assert a.success_rate == 50.0
```

### Running all tests

```bash
pytest tests/ -v --tb=short
```

---

## 7. Checklist Before Production Deployment

- [ ] `.env` file created with all real values
- [ ] `GEMINI_API_KEY` set and tested
- [ ] `HF_API_TOKEN` set and tested
- [ ] `TAYOG_BASE_URL` points to real instance
- [ ] `TAYOG_SECRET_KEY` matches backend value
- [ ] `bot_user_mapping.json` populated with real Tayog user IDs
- [ ] All placeholder values removed from mapping file
- [ ] Dry-run completed: `python main.py --dry-run --bots 10`
- [ ] Single-bot live upload verified: `python main.py --bots 1`
- [ ] `tayog_upload.log` shows `"status": "success"` entries
- [ ] Post visible at `TAYOG_BASE_URL/posts/<postId>`
- [ ] `images/` directory exists and is writable
- [ ] Log rotation configured for `simulator.log` and `tayog_upload.log`

