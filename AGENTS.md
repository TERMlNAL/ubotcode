# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

This is a Russian-language Telegram AI chatbot ("ubot") built with Python 3, aiogram 3, OpenAI API, PostgreSQL, and Redis. It provides GPT chat, DALL-E image generation, and voice transcription with a paid subscription system (Robokassa).

### Key entry points

- `bot.py` — production bot (async PostgreSQL via SQLAlchemy + asyncpg, Redis FSM storage)
- `g.py` — legacy/test bot (SQLite, in-memory FSM storage, `TESTING_MODE=True`)

### Required services

| Service | How to start | Notes |
|---------|-------------|-------|
| PostgreSQL | `sudo pg_ctlcluster 16 main start` | DB: `ubotdb`, user: `ubotuser`, password in `bot.py` |
| Redis | `sudo redis-server --daemonize yes` | Default localhost:6379 |

### Running the bot

```bash
source /workspace/venv/bin/activate
cd /workspace
python3 bot.py   # production (requires PostgreSQL + Redis)
python3 g.py     # legacy/test (SQLite, no Redis needed)
```

Both bots require a valid `.env` file with `API_TOKEN` (Telegram), `OPENAI_API_KEY`, `PAYMENT_PROVIDER_TOKEN`, `SUPPORT_BOT_USERNAME`, and Robokassa credentials. Without a valid Telegram token, the bot will initialize DB/Redis successfully but fail at `start_polling` with `TelegramUnauthorizedError`.

### Alembic migrations

```bash
source /workspace/venv/bin/activate
cd /workspace
alembic current   # check migration status
alembic upgrade head  # apply migrations
```

The `alembic.ini` SQLAlchemy URL points to the same PostgreSQL database as `bot.py`.

### Non-obvious gotchas

- The `.env` file is gitignored. You must create it before running either bot.
- `webhook.py` (FastAPI payment callback handler) is also gitignored and not present in the repo. The payment webhook service cannot be started from the repo alone.
- `bot.py` calls `exit(1)` immediately if any required env var is missing — all nine variables must be set.
- System dependency: `ffmpeg` is required by `pydub` for voice message OGG-to-WAV conversion.
- No linter or test framework is configured in this repo. Syntax checking can be done via `python3 -m py_compile bot.py`.
- `openai==0.27.0` is a legacy version (pre-1.0); it uses `openai.ChatCompletion.create()` not the newer client API.
