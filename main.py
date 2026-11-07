"""Entry point for the Crypto Bot Copier.

All application code lives in the ``bot`` package:

    bot/config.py            environment, debug logging, app version
    bot/state.py             shared runtime state (Telegram client, event loop, ...)
    bot/web.py               Flask app instance
    bot/caches.py            in-memory caches and DEX Validated telemetry
    bot/async_bridge.py      run coroutines on the Telethon loop from Flask threads
    bot/runtime.py           Telegram client lifecycle + message handlers (main())
    bot/db/                  SQLite connection/schema, Supabase sync, startup
    bot/engine/rules.py      workflow rule processing engine
    bot/services/            token data, Helius, DEX enrichment, CTO processing,
                             wallet tracking, scanners, performance, outcomes
    bot/routes/              Flask API/page routes grouped by feature
"""

import asyncio

# Importing the route modules registers their endpoints on the Flask app.
from bot.routes import (  # noqa: F401
    pages,
    dashboard,
    workflows,
    settings,
    tester,
    telegram,
    wallet,
    token_data,
    dex_validated,
    helius,
    performance,
    outcomes,
)
from bot.web import app  # noqa: F401  (exposed for WSGI servers, e.g. gunicorn main:app)
from bot.runtime import main


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping bot gracefully...")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
