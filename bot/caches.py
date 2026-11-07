"""In-memory caches, dedup sets and DEX Validated runtime telemetry."""

import threading
import time
from collections import OrderedDict

# Cache to enforce rate-limiting for Dexscreener orders checks
# Key: (chainId, tokenAddress), Value: timestamp (float)
orders_check_cache = {}

# Live operational telemetry for the DEX Validated dashboard.
dex_validated_runtime_lock = threading.RLock()
dex_validated_active_ws_feeds = set()
dex_validated_runtime = {
    'mode': 'websocket',
    'ws_connected': 0,
    'last_checked_at': None,
    'next_check_at': None,
    'last_scan_seconds': None,
    'last_new_events': 0,
    'last_forwarded': 0,
    'last_detection_seconds': None,
    'total_checks': 0,
    'total_events': 0,
    'total_forwarded': 0,
    'last_error': None,
}

# Cache to avoid duplicate network requests for token symbol/name
token_metadata_cache = {}

# Cache to avoid duplicate network requests for token USD price
# Key: mint, Value: (price_usd, cache_timestamp)
token_price_cache = {}
CACHE_MAX_ENTRIES = 5_000

def bounded_cache_set(cache, key, value):
    cache[key] = value
    while len(cache) > CACHE_MAX_ENTRIES:
        cache.pop(next(iter(cache)), None)

# Helius can deliver the same webhook more than once (and can do so concurrently).
# Keep a small, time-bounded process-local set so duplicate deliveries do not fan
# out into duplicate API scans and asyncio tasks.
WEBHOOK_SIGNATURE_TTL_SECONDS = 15 * 60
WEBHOOK_SIGNATURE_CACHE_MAX = 10_000
processed_webhook_signatures = OrderedDict()
processed_webhook_signatures_lock = threading.Lock()

def claim_webhook_signature(signature):
    """Return True once per webhook signature within the deduplication window."""
    if not signature:
        return False

    now = time.monotonic()
    with processed_webhook_signatures_lock:
        cutoff = now - WEBHOOK_SIGNATURE_TTL_SECONDS
        while processed_webhook_signatures:
            _, seen_at = next(iter(processed_webhook_signatures.items()))
            if seen_at >= cutoff:
                break
            processed_webhook_signatures.popitem(last=False)

        if signature in processed_webhook_signatures:
            return False

        processed_webhook_signatures[signature] = now
        while len(processed_webhook_signatures) > WEBHOOK_SIGNATURE_CACHE_MAX:
            processed_webhook_signatures.popitem(last=False)
        return True
