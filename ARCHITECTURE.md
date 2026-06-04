# Architecture — AI Social Bot Simulator

## Current Architecture (v2.1)

```
┌──────────────────────────────────────────────────────────────────┐
│  main.py  — CLI entry point, terminal UI, run orchestration       │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  config.py  — Singleton Config dataclass                          │
│  All settings loaded from environment variables / .env            │
│  Startup validation and warning emission                          │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │   bots.py        │
                    │  BotProfile      │
                    │  generate_all_   │
                    │  bots(n)         │
                    └────────┬─────────┘
                             │  List[BotProfile]
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  scheduler.py  — BotScheduler                                     │
│                                                                    │
│  asyncio event loop                                               │
│  asyncio.Semaphore (MAX_CONCURRENT_REQUESTS)                      │
│  Divides bots into batches → runs _run_batch() concurrently       │
│                                                                    │
│  Shared aiohttp.ClientSession (owned here, passed to both         │
│  ContentGenerator and TayogClient for connection reuse)           │
└──────────┬───────────────────────────────────┬───────────────────┘
           │                                   │
           ▼                                   ▼
┌──────────────────────┐           ┌───────────────────────────────┐
│  generator.py         │           │  tayog_client.py               │
│  ContentGenerator     │           │  TayogClient                   │
│                       │           │                                │
│  generate_post(bot)   │           │  upload_post(bot_id,           │
│  ├─ Gemini REST API   │           │              content,          │
│  │  gemini-2.0-flash  │           │              image_path)        │
│  └─ mock fallback     │           │                                │
│                       │           │  ├─ BotUserMapping             │
│  generate_image(post) │           │  │  (bot_id → tayogUserId)     │
│  ├─ HF FLUX.1-schnell │           │  │                             │
│  ├─ HF SDXL fallback  │           │  ├─ _validate_payload()        │
│  └─ mock placeholder  │           │  │                             │
│                       │           │  ├─ asyncio.Semaphore          │
│  PIL post-processing  │           │  │  (MAX_CONCURRENT_UPLOADS)   │
│  (crop, sharpen,      │           │  │                             │
│   enhance)            │           │  ├─ _do_upload()               │
│                       │           │  │  aiohttp FormData           │
│  extract_image_       │           │  │  multipart POST             │
│  metadata()           │           │  │                             │
└──────────┬────────────┘           │  ├─ Exponential backoff retry  │
           │                        │  │  (1s → 2s → 4s → 8s → 16s) │
           │ Post object            │  │                             │
           └──────────────┬─────────│  ├─ UploadAnalytics            │
                          │         │  │                             │
                          │         │  └─ UploadLogger               │
                          │         │     tayog_upload.log           │
                          │         └───────────┬───────────────────┘
                          │                     │
                          ▼                     ▼
┌──────────────────────────────────────────────────────────────────┐
│  storage.py  — PostStorage                                        │
│                                                                    │
│  Thread-safe JSON persistence (threading.Lock)                    │
│  Atomic writes (write-to-tmp → rename)                            │
│  Corruption quarantine (bad files renamed, fresh file created)    │
│  Double-checked locking singleton (get_storage())                 │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
                     posts.json  (local)
```

## Target Architecture (v2.1+)

```
Bot Fleet
  │
  ▼
Scheduler (asyncio)
  │
  ├─► ContentGenerator ──► Gemini + HuggingFace
  │
  └─► TayogClient
        │
        ▼
   POST /api/posts/new/v1
        │
        ├─► Cloudflare R2  (media storage)
        └─► MongoDB         (posts, media, hashtags, mentions)

                    ║ (optional dual-write)
                    ▼
              posts.json  (local backup)
```

## Component Responsibilities

| Component | Responsibility |
|---|---|
| `Config` | Single source of truth for all runtime parameters; validates at startup |
| `BotProfile` | Immutable persona definition (identity, skills, topics) |
| `ContentGenerator` | AI content pipeline; owns no state beyond the shared HTTP session |
| `BotScheduler` | Divides work, manages async concurrency, owns the HTTP session lifetime |
| `TayogClient` | API integration; owns only upload logic and analytics, never the session |
| `BotUserMapping` | Maps bot identity → Tayog user identity; loaded once at startup |
| `PostStorage` | Durable persistence; thread-safe; not involved in upload logic |
| `UploadLogger` | Write-only structured log; never blocks the upload path |
| `UploadAnalytics` | In-memory aggregate stats; no I/O |

## Data Flow

```
1. main.py parses CLI args, applies overrides to cfg
2. generate_all_bots(n) creates n BotProfile objects
3. BotScheduler.run() enters asyncio.run()
4. Scheduler creates shared aiohttp.ClientSession
5. Scheduler creates TayogClient (receives session, does not own it)
6. Scheduler creates ContentGenerator (receives session, does not own it)
7. For each batch:
   a. asyncio.gather() dispatches _process_bot() tasks concurrently
   b. Each task:
      i.  ContentGenerator.generate_post(bot) → Gemini API → text
      ii. ContentGenerator.generate_image(text, bot) → HF API → PNG
      iii. extract_image_metadata(path) → ImageMetadata
      iv. TayogClient.upload_post(bot_id, text, path) → Tayog API
      v.  PostStorage.save_post(post) → posts.json (if enabled)
      vi. callback(post, idx, total) → terminal output
8. After all batches: TayogClient.analytics.report() logged
9. Scheduler closes TayogClient, then closes shared session
```

## Upload Flow

```
upload_post(bot_id, content, image_path)
  │
  ├─ Acquire asyncio.Semaphore  (rate limit)
  │
  ├─ BotUserMapping.get_user_id(bot_id)
  │   └─ None → UploadResult(status="skipped")
  │
  ├─ _validate_payload(user_id, content, secret, images)
  │   └─ errors → UploadResult(status="failed")
  │
  ├─ DRY_RUN_MODE check
  │   └─ True → UploadResult(status="skipped", error="dry_run_mode")
  │
  └─ Retry loop (max_retries=4, backoff 1→2→4→8→16s, cap 30s):
      │
      ├─ _do_upload(user_id, content, image_path)
      │   ├─ aiohttp.FormData:
      │   │   ├─ files        = image bytes
      │   │   ├─ fileMetadata = {"type":"image","sortOrder":1}
      │   │   └─ postData     = {"x_secrect":...,"content":...,"userId":...}
      │   └─ session.post(endpoint, data=form)
      │
      ├─ 201 → success, extract postId from response
      ├─ 5xx / 429 → retry with backoff
      ├─ Timeout → retry with backoff
      └─ 4xx (not 429) → fail immediately
```

## Failure Handling

| Failure | Behaviour |
|---|---|
| Gemini API down | Falls back to `_mock_generate_post()` |
| HuggingFace primary down | Falls back to SDXL endpoint |
| Both HF endpoints down | Falls back to 1×1 PNG placeholder |
| Tayog HTTP 5xx | Retry with exponential backoff (up to `TAYOG_MAX_RETRIES`) |
| Tayog HTTP 401/403 | Fail immediately, log error (auth misconfiguration) |
| Tayog HTTP 404 | Fail immediately, log error (invalid userId) |
| Timeout | Retry with exponential backoff |
| Missing userId mapping | Skip upload, log warning, continue |
| posts.json corrupt | Quarantine to `posts.corrupt.<ts>.json`, reset to `[]` |
| Bot-level exception | Log error, return None, continue other bots |

## Recovery Logic

- **Partial runs**: posts already saved to `posts.json` survive a crash. Re-run picks up from zero (re-generates) unless `--clear` is omitted — existing posts accumulate.
- **Corrupt storage**: on `JSONDecodeError` at read, the corrupt file is renamed and a fresh `[]` file replaces it. Data is not lost (quarantined), but will not be counted in stats.
- **Failed uploads**: stored in `posts.json` with `tayog_upload_status="failed"`. Future runs do not retry them automatically; a replay script reading `posts.json` and re-calling `TayogClient.upload_post()` can be written if needed.

## Scalability Guidance

| Bot count | Storage recommendation | Scheduler recommendation |
|---|---|---|
| < 1 000 | `posts.json` (current) | Single process |
| 1 000 – 10 000 | SQLite (`posts.db`) | Single process, tune concurrency |
| > 10 000 | MongoDB / PostgreSQL | Multiple processes / workers + task queue (Celery, RQ) |

