"""Environment configuration, debug logging and app version."""

import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# Detailed per-message and per-rule logs are useful locally, but extremely noisy
# in production. Enable them explicitly with DEBUG_LOGGING=true.
DEBUG_LOGGING = os.environ.get("DEBUG_LOGGING", "false").strip().lower() in ("1", "true", "yes", "on")

def debug_log(message):
    if DEBUG_LOGGING:
        print(message, flush=True)


# Release label format: <major>.<YYYY>.<MM>.<DD>
APP_BUILD_ID = "02"

def get_app_version():
    """Return application version dynamically formatted with build ID and today's date."""
    today_str = datetime.now().strftime('%Y.%m.%d')
    return f"{APP_BUILD_ID}.{today_str}"

APP_VERSION = get_app_version()

PERSISTENT_DATA_DIR = os.environ.get("PERSISTENT_DATA_DIR")
if not PERSISTENT_DATA_DIR and os.path.exists("/data") and os.access("/data", os.W_OK):
    PERSISTENT_DATA_DIR = "/data"

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or "").strip()
