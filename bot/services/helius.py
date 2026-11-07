"""Helius multi-key pool and automatic failover."""

import os
import threading
import time
import requests

from bot.db.connection import get_settings

# =====================================================================
# HELIUS MULTI-KEY POOL & AUTOMATIC FAILOVER MANAGER
# =====================================================================
helius_keys_lock = threading.Lock()
helius_key_cooldowns = {}  # key -> cooldown_until_timestamp

def get_all_helius_keys():
    """Return all configured Helius API keys in priority order (Key 1 -> Key 2 -> Key 3)."""
    settings = get_settings()
    keys = []
    for k in ('helius_api_key', 'helius_api_key_2', 'helius_api_key_3'):
        val = str(settings.get(k) or os.environ.get(k.upper()) or '').strip()
        if val and val not in keys:
            keys.append(val)
    return keys

def get_current_helius_key():
    """Get the first active (non-cooled-down) Helius API key, or least recently cooled down."""
    keys = get_all_helius_keys()
    if not keys:
        return None
    now = time.time()
    with helius_keys_lock:
        for k in keys:
            if now >= helius_key_cooldowns.get(k, 0):
                return k
        # If all in cooldown, return the one whose cooldown expires soonest
        return min(keys, key=lambda k: helius_key_cooldowns.get(k, 0))

def mark_helius_key_rate_limited(key, cooldown_seconds=60):
    """Mark a key as rate-limited or quota-exhausted so requests switch to the next key."""
    if not key:
        return
    with helius_keys_lock:
        helius_key_cooldowns[key] = time.time() + cooldown_seconds
        keys = get_all_helius_keys()
        next_key = None
        for k in keys:
            if k != key and time.time() >= helius_key_cooldowns.get(k, 0):
                next_key = k
                break
        short_k = f"{key[:6]}...{key[-4:]}" if len(key) >= 10 else key
        short_next = f"{next_key[:6]}...{next_key[-4:]}" if next_key and len(next_key) >= 10 else (next_key or "None")
        print(f"[HELIUS ROTATION] Key {short_k} hit rate limit / quota (HTTP 429/402/403). Cooldown {cooldown_seconds}s. Next active key: {short_next}", flush=True)

def helius_request_with_failover(method, endpoint_template, timeout=8, **kwargs):
    """
    Executes a Helius request (GET or POST) with automatic key failover.
    endpoint_template must contain '{key}' where the API key belongs.
    e.g. 'https://api.helius.xyz/v0/addresses/{addr}/transactions?api-key={key}&limit=100'
    or 'https://mainnet.helius-rpc.com/?api-key={key}'
    """
    keys = get_all_helius_keys()
    if not keys:
        return None

    # Try keys ordered by cooldown expiry (active first, then soonest expiring)
    now = time.time()
    with helius_keys_lock:
        sorted_keys = sorted(keys, key=lambda k: helius_key_cooldowns.get(k, 0))

    last_response = None
    for k in sorted_keys:
        url = endpoint_template.format(key=k)
        try:
            if method.upper() == 'GET':
                r = requests.get(url, timeout=timeout, **kwargs)
            else:
                r = requests.post(url, timeout=timeout, **kwargs)

            # Rate limit (429), payment required / quota (402), or forbidden (403)
            if r.status_code in (429, 402, 403) or (r.status_code == 400 and 'rate limit' in r.text.lower()):
                mark_helius_key_rate_limited(k, cooldown_seconds=60)
                last_response = r
                continue

            return r
        except Exception as e:
            last_response = None
            continue

    return last_response
