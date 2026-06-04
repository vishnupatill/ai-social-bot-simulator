# Changelog — AI Social Bot Simulator

All notable changes to this project are documented in this file.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2.1.0] — Security & Production-Readiness Hardening

### Security Fixes
- **BREAKING**: Removed all hardcoded API keys from `config.py`
  - `GEMINI_API_KEY` no longer has a default value (was `AIzaSy...`)
  - `HF_API_TOKEN` no longer has a default value (was `hf_udN...`)
  - Keys must now be set via environment variables or `.env` file
- Added startup credential validation via `Config.validate()` and `Config.emit_startup_warnings()`
- Startup warnings emitted at `WARNING` level for missing `GEMINI_API_KEY`, `HF_API_TOKEN`, `TAYOG_SECRET_KEY`

### Bug Fixes
- **storage.py**: Fixed `get_storage()` singleton race condition with double-checked locking
- **storage.py**: Fixed missing `image_*` metadata fields in `Post` dataclass (were dropped on `from_dict()`)
- **tayog_client.py**: Fixed `UploadResult.user_id` field not being populated (was always `"N/A"` in upload log)
- **tayog_client.py**: Fixed retry backoff not being capped (could theoretically grow unbounded); now capped at 30 s
- **tayog_client.py**: Fixed SSL disabled by default (`ssl=False` in `TCPConnector`); SSL is now enabled
- **tayog_client.py**: Added `aiohttp.ClientConnectorError` as a handled exception (was falling through to generic `Exception`)
- **scheduler.py**: Fixed `TayogClient` receiving and closing a session it does not own; session lifecycle is now exclusively owned by the scheduler
- **scheduler.py**: Fixed potential `AttributeError` if `http_session` was `None` when `_HAS_AIOHTTP=False`

### New Features
- **config.py**: `Config.validate()` method returns list of configuration warnings
- **config.py**: `Config.emit_startup_warnings()` logs all config issues at startup
- **config.py**: All numeric config values now support environment variable override
- **config.py**: `python-dotenv` integration — `.env` is loaded automatically if installed
- **storage.py**: Corruption quarantine — bad `posts.json` files are renamed and a fresh file is created
- **storage.py**: `Post` dataclass gains `image_width`, `image_height`, `image_mime_type`, `image_file_size` fields
- **storage.py**: `stats()` now returns `tayog_pending` and `tayog_skipped` counts
- **tayog_client.py**: `UploadAnalytics.http_status_counts` tracks HTTP response code distribution
- **tayog_client.py**: `UploadAnalytics.report()` includes HTTP status breakdown and per-batch counts
- **tayog_client.py**: `_validate_payload()` runs before every HTTP call (validates files exist, userId present, etc.)
- **tayog_client.py**: `BotUserMapping.validate_bots()` returns coverage report for a list of bot IDs
- **tayog_client.py**: `BotUserMapping.mapped_count` property
- **tayog_client.py**: Placeholder detection — mapping entries with `TAYOG_USER_ID_HERE` are filtered and warned about
- **tayog_client.py**: `UploadLogger` now writes structured JSON lines (was pipe-delimited text)
- **tayog_client.py**: `UploadResult.user_id` is now populated for accurate log correlation
- **scheduler.py**: Image metadata extraction via `extract_image_metadata()` after each image is generated
- **scheduler.py**: Image metadata stored in `Post` object before saving/uploading
- **main.py**: `_on_post()` callback now prints Tayog upload status (✓/✗) in colour
- **main.py**: `_print_tayog_stats()` includes success rate calculation

### Documentation
- Added `README.md` — full project documentation
- Added `ARCHITECTURE.md` — system architecture, data flow, failure handling
- Added `CHANGELOG.md` — this file
- Added `TESTING.md` — testing guide
- Added `.env.example` — all environment variables with descriptions
- Updated `requirements.txt` — all dependencies pinned to stable versions

---

## [2.0.0] — Tayog Integration

### Added
- `tayog_client.py` — async Tayog API client
  - Multipart form-data upload matching `POST /api/posts/new/v1`
  - `BotUserMapping` — loads `bot_user_mapping.json`
  - `TayogClient` — exponential backoff retry, semaphore throttle
  - `UploadAnalytics` — session-level aggregate statistics
  - `UploadLogger` — writes to `tayog_upload.log`
  - `extract_image_metadata()` — PIL-based image info extraction
  - `ImageMetadata` dataclass
  - `UploadResult` dataclass
- `bot_user_mapping.json` — template mapping file with placeholder entries

### Changed
- `config.py` — added Tayog configuration block:
  - `TAYOG_BASE_URL`, `TAYOG_SECRET_KEY`, `TAYOG_BOT_MAPPING_FILE`
  - `ENABLE_TAYOG_UPLOAD`, `ENABLE_LOCAL_STORAGE`, `DRY_RUN_MODE`
  - `MAX_CONCURRENT_UPLOADS`, `TAYOG_MAX_RETRIES`, `TAYOG_UPLOAD_LOG`
  - `ENABLE_MENTIONS`
- `storage.py` — `Post` dataclass extended with:
  - `tayog_post_id: Optional[str]`
  - `tayog_upload_status: str` (pending | success | failed | skipped)
  - `tayog_upload_timestamp: Optional[str]`
  - `PostStorage.update_post_tayog_result()` added
  - `PostStorage.stats()` extended with Tayog counts
- `scheduler.py` — integrated `TayogClient` into the post pipeline:
  - Instantiates client when `ENABLE_TAYOG_UPLOAD=True`
  - Calls `upload_post()` after each content generation
  - Tracks `uploads_per_batch` for analytics
- `generator.py` — added:
  - `_pick_hashtags()` — topic-aware hashtag selection, no duplicates
  - `_pick_mentions()` — random mention selection from `MENTION_POOL`
  - `HASHTAG_POOLS` — domain-mapped hashtag sets
  - `MENTION_POOL` — configurable list of Tayog @usernames
- `main.py` — added CLI flags:
  - `--tayog-stats` — show upload analytics from stored posts
  - `--dry-run` — validate payloads without uploading
  - `--no-tayog` — disable Tayog upload for a single run
- `MIGRATION.md` — step-by-step Tayog integration guide

---

## [1.0.0] — Original Simulator

### Added
- `main.py` — CLI entry point with `--bots`, `--batch`, `--delay`, `--demo`, `--clear`, `--stats`
- `config.py` — central configuration dataclass
- `bots.py` — bot persona generation (names, specialisations, skills, about sections)
- `generator.py` — Gemini text generation + HuggingFace image generation
  - FLUX.1-schnell primary, SDXL fallback
  - PIL post-processing (crop, sharpen, contrast)
  - Deterministic image prompt generation seeded by bot identity
  - Domain-aware scene pools (AI, cybersecurity, healthcare, climate, etc.)
  - Demo/mock mode for offline testing
- `scheduler.py` — async batch scheduler with semaphore throttle
- `storage.py` — thread-safe JSON persistence with atomic writes

### Breaking Changes (from v2.0.0)
- None — v1.0.0 posts.json files load without errors in v2.x due to `Post.from_dict()` tolerance

---

## Known Limitations

- `posts.json` is not suitable as a primary store above ~50 000 posts (full read-write on every save)
- Image generation is slow (~5–15 s per image); concurrent throughput is bounded by HuggingFace rate limits
- `MENTION_POOL` in `generator.py` must be manually populated with real Tayog usernames
- No built-in scheduler cron; run via system cron or a task queue
- `tayog_upload.log` is not rotated; use `logrotate` in production

