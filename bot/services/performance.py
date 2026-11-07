"""Post-signal 1h/4h OHLCV peak & trajectory engine."""

import asyncio
import threading
import json
from datetime import datetime, timezone
import time
import requests
from collections import OrderedDict

from bot.config import debug_log
from bot.db.connection import db_lock, get_db, get_settings, supabase_client
from bot.services.dex_enrichment import fetch_token_rugcheck_sync
from bot.services.token_indicators import parse_mc_usd, synthesize_token_indicators

# ==========================================
# Post-Signal 1h & 4h OHLCV Peak & Trajectory Engine
# ==========================================
perf_scanner_lock = threading.RLock()
perf_scanner_runtime = {
    'running': False,
    'last_run_at': None,
    'next_run_at': None,
    'last_batch_updated': 0,
    'total_updated': 0,
    'last_error': None,
    'backfill_wallet_payments': False,
    'scan_interval_minutes': 15.0,
    'wake_requested': False,
}
_ohlcv_pool_cache = OrderedDict()


def _sync_perf_scanner_settings():
    try:
        s = get_settings()
        if 'perf_scan_interval_minutes' in s:
            try:
                perf_scanner_runtime['scan_interval_minutes'] = float(s['perf_scan_interval_minutes'])
            except Exception:
                pass
        if 'perf_backfill_wp_enabled' in s:
            perf_scanner_runtime['backfill_wallet_payments'] = str(s['perf_backfill_wp_enabled']).lower() in ('true', '1', 'yes')
    except Exception as e:
        debug_log(f"[PERF SCANNER] Could not sync settings: {e}")


def _cache_ohlcv_get(key):
    val = _ohlcv_pool_cache.get(key)
    if val is not None:
        _ohlcv_pool_cache.move_to_end(key)
    return val


def _cache_ohlcv_set(key, val):
    _ohlcv_pool_cache[key] = val
    _ohlcv_pool_cache.move_to_end(key)
    while len(_ohlcv_pool_cache) > 600:
        _ohlcv_pool_cache.popitem(last=False)


def _fetch_dexscreener_pools_batch_sync(ca_list):
    """Fetch primary Solana pool addresses and live MC/price for up to 30 CAs in one HTTP request."""
    result_map = {}
    if not ca_list:
        return result_map
    for idx in range(0, len(ca_list), 30):
        chunk = [c for c in ca_list[idx:idx + 30] if c]
        if not chunk:
            continue
        url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(chunk)}"
        try:
            res = requests.get(url, timeout=12)
            if res.status_code != 200:
                continue
            payload = res.json()
            pairs = payload.get('pairs') if isinstance(payload, dict) else []
            if not isinstance(pairs, list):
                continue
            by_ca = {}
            for p in pairs:
                if not isinstance(p, dict):
                    continue
                if str(p.get('chainId') or '').lower() != 'solana':
                    continue
                base_addr = (p.get('baseToken') or {}).get('address')
                if not base_addr:
                    continue
                by_ca.setdefault(base_addr, []).append(p)

            for ca, ca_pairs in by_ca.items():
                ca_pairs.sort(
                    key=lambda x: (
                        float((x.get('liquidity') or {}).get('usd') or 0.0),
                        float((x.get('volume') or {}).get('h24') or 0.0),
                    ),
                    reverse=True,
                )
                top = ca_pairs[0]
                mc_now = float(top.get('marketCap') or top.get('fdv') or 0.0)
                price_now = float(top.get('priceUsd') or 0.0)
                supply = (mc_now / price_now) if (mc_now > 0 and price_now > 0) else 1_000_000_000.0
                pools = []
                for p in ca_pairs[:3]:
                    pa = p.get('pairAddress')
                    if pa and pa not in pools:
                        pools.append(pa)
                result_map[ca] = {
                    'pools': pools,
                    'mc_now': mc_now,
                    'price_now': price_now,
                    'supply': supply,
                    'liquidity_usd': float((top.get('liquidity') or {}).get('usd') or 0.0),
                    'volume_24h': float((top.get('volume') or {}).get('h24') or 0.0),
                    'volume_5m': float((top.get('volume') or {}).get('m5') or 0.0),
                    'volume_1h': float((top.get('volume') or {}).get('h1') or 0.0),
                    'volume_6h': float((top.get('volume') or {}).get('h6') or 0.0),
                    'perf_5m': float((top.get('priceChange') or {}).get('m5') or 0.0),
                    'perf_1h': float((top.get('priceChange') or {}).get('h1') or 0.0),
                    'perf_6h': float((top.get('priceChange') or {}).get('h6') or 0.0),
                    'perf_24h': float((top.get('priceChange') or {}).get('h24') or 0.0),
                }
        except Exception as e:
            debug_log(f"[PERF SCANNER] DexScreener batch lookup error: {e}")
    return result_map


# ---------------------------------------------------------------------------
# GeckoTerminal access with a shared, adaptive rate limiter.
# The free API allows roughly 30 calls/minute per IP; exceeding it returns 429 for
# a while. All GeckoTerminal calls go through _gecko_get so the scanner thread and
# the manual "Scan now" thread never exceed the budget together.
# ---------------------------------------------------------------------------
GECKO_HEADERS = {'Accept': 'application/json;version=20230302'}
GECKO_RATE_LIMITED = 'RATE_LIMITED'  # sentinel: data unavailable right now, retry later


class _GeckoRateLimiter:
    MIN_INTERVAL = 2.5    # seconds between calls when healthy (~24/min)
    MAX_INTERVAL = 12.0   # slowest pace after repeated 429s
    COOLDOWN = 45.0       # pause after a 429 before trying again

    def __init__(self):
        self._lock = threading.Lock()
        self._interval = self.MIN_INTERVAL
        self._next_at = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_at)
            self._next_at = slot + self._interval
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def on_success(self):
        with self._lock:
            self._interval = max(self.MIN_INTERVAL, self._interval * 0.9)

    def on_rate_limited(self):
        with self._lock:
            self._interval = min(self.MAX_INTERVAL, self._interval * 1.5)
            self._next_at = max(self._next_at, time.monotonic() + self.COOLDOWN)
        print(f"[PERF SCANNER] GeckoTerminal rate limit hit - pausing {int(self.COOLDOWN)}s, pace now 1 call / {self._interval:.1f}s", flush=True)


_gecko_limiter = _GeckoRateLimiter()


def _gecko_get(url):
    """Rate-limited GET. Returns the Response, or GECKO_RATE_LIMITED if GeckoTerminal
    is throttling us / unreachable (the caller should retry the row later)."""
    for _attempt in range(2):
        _gecko_limiter.wait()
        try:
            res = requests.get(url, headers=GECKO_HEADERS, timeout=12)
        except Exception as e:
            debug_log(f"[PERF SCANNER] GeckoTerminal request error: {e}")
            return GECKO_RATE_LIMITED
        if res.status_code == 429:
            _gecko_limiter.on_rate_limited()
            continue
        _gecko_limiter.on_success()
        return res
    return GECKO_RATE_LIMITED


def _fetch_geckoterminal_pool_direct_sync(ca):
    """Fallback: primary pool + meta from GeckoTerminal. Returns meta dict, None if the
    token has no pools, or GECKO_RATE_LIMITED if it could not be checked right now."""
    if not ca:
        return None
    cache_key = f"pool_{ca}"
    cached = _cache_ohlcv_get(cache_key)
    if cached is not None:
        return cached if cached != 'NONE' else None

    res = _gecko_get(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{ca}/pools")
    if res is GECKO_RATE_LIMITED:
        return GECKO_RATE_LIMITED
    try:
        if res.status_code == 404:
            _cache_ohlcv_set(cache_key, 'NONE')
            return None
        if res.status_code != 200:
            return GECKO_RATE_LIMITED
        data = res.json().get('data') or []
        if not data:
            _cache_ohlcv_set(cache_key, 'NONE')
            return None

        pools = []
        for p in data[:3]:
            pid = str(p.get('id') or '').replace('solana_', '')
            if pid and pid not in pools:
                pools.append(pid)

        top = data[0].get('attributes') or {}
        price_now = float(top.get('base_token_price_usd') or 0.0)
        fdv_usd = float(top.get('fdv_usd') or 0.0)
        reserve_usd = float(top.get('reserve_in_usd') or 0.0)
        mc_now = fdv_usd if fdv_usd > 0 else (reserve_usd * 2.0 if reserve_usd > 0 else 0.0)
        supply = (mc_now / price_now) if (mc_now > 0 and price_now > 0) else 1_000_000_000.0

        meta = {'pools': pools, 'mc_now': mc_now, 'price_now': price_now, 'supply': supply}
        _cache_ohlcv_set(cache_key, meta)
        return meta
    except Exception as e:
        debug_log(f"[PERF SCANNER] GeckoTerminal direct pool lookup error for {ca}: {e}")
        return GECKO_RATE_LIMITED


async def _fetch_geckoterminal_ohlcv(pool_address, t0, now_ts):
    """Chronological OHLCV candles covering [t0, t0 + 4h] (1m, with 5m/15m/1h fallback).

    Returns the candle list, [] if GeckoTerminal confirms there is no history, or
    GECKO_RATE_LIMITED if it could not be fetched right now (never cached).
    """
    if not pool_address:
        return []
    before_ts = min(int(now_ts) + 120, int(t0) + 14400 + 300)
    # limit=300 covers 5 hours of 1m candles, so signals on the same pool within a 1-hour bucket share the response
    bucket_key = ('1m_v2', pool_address, before_ts // 3600)
    cached = _cache_ohlcv_get(bucket_key)
    if cached is not None:
        return cached

    endpoints = [
        f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/minute?aggregate=1&before_timestamp={before_ts}&limit=300",
        f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/minute?aggregate=5&before_timestamp={before_ts + 600}&limit=100",
        f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/minute?aggregate=15&before_timestamp={before_ts + 900}&limit=100",
        f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/hour?aggregate=1&before_timestamp={before_ts + 3600}&limit=100"
    ]

    t0_min = (int(t0) // 60) * 60
    for url in endpoints:
        res = await asyncio.to_thread(_gecko_get, url)
        if res is GECKO_RATE_LIMITED:
            return GECKO_RATE_LIMITED
        try:
            if res.status_code == 404:
                break
            if res.status_code != 200:
                return GECKO_RATE_LIMITED
            payload = res.json()
            raw_list = ((payload.get('data') or {}).get('attributes') or {}).get('ohlcv_list') or []
            if not raw_list:
                continue
            # Sort ascending by timestamp: [ts, open, high, low, close, volume]
            candles = sorted(
                [c for c in raw_list if isinstance(c, list) and len(c) >= 5],
                key=lambda c: int(c[0] or 0)
            )
            in_range = [c for c in candles if (t0_min - 3600) <= int(c[0]) <= (t0 + 14400 + 3600)]
            if in_range:
                _cache_ohlcv_set(bucket_key, candles)
                return candles
        except Exception as e:
            debug_log(f"[PERF SCANNER] GeckoTerminal OHLCV error for pool {pool_address}: {e}")
            return GECKO_RATE_LIMITED

    # GeckoTerminal answered but has no candles for this window: genuinely no history.
    _cache_ohlcv_set(bucket_key, [])
    return []


def compute_window_peak_and_trajectory(entry_mc, t0, window_sec, candles, supply, mc_now=0.0, age_sec=0):
    """Compute Max MC, Peak Multiplier, Min MC, Pre-Peak Drawdown, and Path (Pump->Dump vs Dip->Pump) for [t0, t0+window_sec]."""
    if not entry_mc or entry_mc <= 0:
        return None

    t0_int = int(t0)
    t0_min = (t0_int // 60) * 60  # Exact 1-minute bucket when the signal was sent (e.g. 02:32:00 for 02:32:25)
    t0_5m = (t0_int // 300) * 300  # Fallback 5-minute bucket if 1m candles were unavailable
    win_end = t0_int + window_sec

    # Primary: start at the exact 1-minute candle of the signal [t0_min, win_end]
    win_candles = [c for c in (candles or []) if t0_min <= int(c[0]) <= win_end]
    # If the first returned candle starts > 60s after t0_min (meaning 5m/15m fallback candles were returned),
    # include the enclosing 5-minute bucket (e.g. 02:30 for a 02:32 signal) so the first minutes are never skipped
    if candles and (not win_candles or int(win_candles[0][0]) > t0_min + 60):
        fallback_5m = [c for c in candles if t0_5m <= int(c[0]) <= win_end]
        if fallback_5m:
            win_candles = fallback_5m
    if not win_candles:
        win_candles = [c for c in (candles or []) if (t0_int - 900) <= int(c[0]) <= (win_end + 300)]
    if not win_candles and candles:
        win_candles = [c for c in candles if (t0_int - 3600) <= int(c[0]) <= (win_end + 3600)]

    scaled = []
    if win_candles:
        first_open = float(win_candles[0][1] or 0.0)
        eff_supply = float(supply or 0.0)
        if first_open > 0:
            implied_entry = first_open * eff_supply
            # If effective supply doesn't align with entry_mc (e.g. burned supply or dead pool), calibrate to entry_mc
            if eff_supply <= 0 or implied_entry <= 0 or (implied_entry / entry_mc > 4.5) or (implied_entry / entry_mc < 0.22):
                eff_supply = entry_mc / first_open

        for idx, c in enumerate(win_candles):
            c_ts = int(c[0])
            o_mc = float(c[1] or 0.0) * eff_supply
            h_mc = float(c[2] or 0.0) * eff_supply
            l_mc = float(c[3] or 0.0) * eff_supply
            cl_mc = float(c[4] or 0.0) * eff_supply
            # If the first candle started before t0 (e.g. 02:32:00 vs 02:32:45 or 5m fallback 02:30 vs 02:32),
            # prevent a pre-signal dump from registering as a fake post-signal high
            if idx == 0 and c_ts < t0_int and o_mc > entry_mc * 1.20 and cl_mc <= entry_mc * 1.15:
                h_mc = max(entry_mc, cl_mc)
            if h_mc > 0 and l_mc > 0:
                scaled.append({
                    'ts': c_ts,
                    'open_mc': o_mc,
                    'high_mc': h_mc,
                    'low_mc': l_mc,
                    'close_mc': cl_mc,
                })

    if not scaled:
        # Fallback when pool has no OHLCV candles (e.g. rugged/dead pool or unindexed curve)
        fallback_mc = float(mc_now or 0.0)
        max_mc = round(max(entry_mc, fallback_mc), 2) if fallback_mc > entry_mc else round(fallback_mc if fallback_mc > 0 else entry_mc, 2)
        peak_mult = round(max_mc / entry_mc, 2)
        min_mc = round(min(entry_mc, fallback_mc), 2)
        min_dd_pct = round(min(0.0, ((min_mc - entry_mc) / entry_mc) * 100.0), 2)
        pre_low = round(entry_mc if fallback_mc >= entry_mc else fallback_mc, 2)
        pre_dd_pct = round(min(0.0, ((pre_low - entry_mc) / entry_mc) * 100.0), 2)
        path = 'DIRECT_PUMP' if peak_mult >= 1.15 else ('STRAIGHT_DUMP' if min_dd_pct <= -15.0 else 'FLAT')
        return {
            'max_mc': max_mc,
            'peak_mult': peak_mult,
            'min_mc': min_mc,
            'min_dd_pct': min_dd_pct,
            'pre_peak_low_mc': pre_low,
            'pre_peak_dd_pct': pre_dd_pct,
            'path': path,
        }

    k_peak = max(range(len(scaled)), key=lambda i: scaled[i]['high_mc'])
    max_mc = round(scaled[k_peak]['high_mc'], 2)
    peak_mult = round(max_mc / entry_mc, 2)

    min_mc = round(min(c['low_mc'] for c in scaled), 2)
    min_dd_pct = round(min(0.0, ((min_mc - entry_mc) / entry_mc) * 100.0), 2)

    # Lowest MC strictly before (or on the dip of) the peak candle
    pre_candidates = [entry_mc] + [c['low_mc'] for c in scaled[:k_peak]]
    peak_candle = scaled[k_peak]
    if peak_candle['close_mc'] >= peak_candle['open_mc']:
        pre_candidates.append(peak_candle['low_mc'])
    else:
        pre_candidates.append(peak_candle['open_mc'])

    pre_peak_low_mc = round(min(pre_candidates), 2)
    pre_peak_dd_pct = round(min(0.0, ((pre_peak_low_mc - entry_mc) / entry_mc) * 100.0), 2)

    post_peak_low_mc = min(c['low_mc'] for c in scaled[k_peak:])

    if peak_mult < 1.12:
        path = 'STRAIGHT_DUMP' if min_dd_pct <= -15.0 else 'FLAT'
    else:
        if pre_peak_dd_pct <= -15.0:
            path = 'DIP_THEN_PUMP'
        elif post_peak_low_mc <= max_mc * 0.55 or post_peak_low_mc <= entry_mc * 0.85:
            path = 'PUMP_THEN_DUMP'
        else:
            path = 'DIRECT_PUMP'

    return {
        'max_mc': max_mc,
        'peak_mult': peak_mult,
        'min_mc': min_mc,
        'min_dd_pct': min_dd_pct,
        'pre_peak_low_mc': pre_peak_low_mc,
        'pre_peak_dd_pct': pre_peak_dd_pct,
        'path': path,
    }


def _persist_row_perf_metrics(table_name, row_info, metrics_payload):
    """Write computed 1h/4h peak & drawdown metrics directly to Supabase first, then mirror to in-memory index."""
    row_id = row_info.get('id')
    now_ts = int(time.time())
    ev_ts = int(row_info.get('event_ts') or 0)

    # Safety guard: prevent 4h metrics from EVER being written to a token younger than 4 hours (14,400s)
    if any(k in metrics_payload for k in ('max_mc_4h', 'peak_mult_4h')):
        if ev_ts > 0 and (now_ts - ev_ts) < 14400:
            for k in ['max_mc_4h', 'peak_mult_4h', 'min_mc_4h', 'min_dd_4h_pct', 'pre_peak_low_mc_4h', 'pre_peak_dd_4h_pct', 'path_4h']:
                metrics_payload.pop(k, None)
            if metrics_payload.get('perf_checked_stage', 0) > 1:
                metrics_payload['perf_checked_stage'] = 1 if ('peak_mult_1h' in metrics_payload or row_info.get('peak_mult_1h') is not None) else 0

    if not metrics_payload:
        return

    if supabase_client:
        try:
            # IMPORTANT: never match Supabase rows by the local ``id``. Local in-memory ids
            # are assigned independently of Supabase ids, so an id match writes one
            # signal's results onto an unrelated token. Only natural keys are used.
            if table_name == 'wallet_payments':
                sig = row_info.get('signature')
                ca_val = row_info.get('ca')
                raw_ts = row_info.get('timestamp')
                if sig:
                    supabase_client.table('wallet_payments').update(metrics_payload).eq('signature', sig).execute()
                elif ca_val and raw_ts:
                    supabase_client.table('wallet_payments').update(metrics_payload).eq('ca', ca_val).eq('timestamp', raw_ts).execute()
                else:
                    debug_log(f"[PERF SCANNER] Skipped Supabase update for wallet_payments row without signature/timestamp (CA: {ca_val})")
            elif table_name == 'dex_validated':
                # Update only the exact signal: ca + signal_type + order_status + payment_timestamp
                # (the table's unique key). No CA-only fallback - that would copy this
                # signal's results onto every other signal of the same token.
                ca_val = row_info.get('ca')
                if ca_val and row_info.get('signal_type') is not None and row_info.get('order_status') is not None:
                    q = (supabase_client.table('dex_validated').update(metrics_payload)
                         .eq('ca', ca_val)
                         .eq('signal_type', row_info.get('signal_type'))
                         .eq('order_status', row_info.get('order_status')))
                    if row_info.get('payment_timestamp') is not None:
                        q = q.eq('payment_timestamp', row_info.get('payment_timestamp'))
                    else:
                        q = q.is_('payment_timestamp', 'null')
                    q.execute()
                else:
                    debug_log(f"[PERF SCANNER] Skipped Supabase update for dex_validated row without full key (CA: {ca_val})")
        except Exception as e:
            debug_log(f"[PERF SCANNER] Supabase direct update notice on {table_name} (CA: {row_info.get('ca')}): {e}")

    set_cols = ", ".join(f"{k}=?" for k in metrics_payload.keys())
    try:
        with db_lock:
            conn = get_db()
            cur = conn.cursor()
            if table_name == 'dex_validated' and row_info.get('ca'):
                ca_val = row_info.get('ca')
                sig_type = row_info.get('signal_type')
                ord_status = row_info.get('order_status')
                pay_ts = row_info.get('payment_timestamp')
                vals = list(metrics_payload.values())
                if sig_type is not None and ord_status is not None and pay_ts is not None:
                    cur.execute(f"UPDATE {table_name} SET {set_cols} WHERE ca=? AND signal_type=? AND order_status=? AND payment_timestamp=?", vals + [ca_val, sig_type, ord_status, pay_ts])
                elif row_id:
                    # Local ids are consistent within the local index, so this is safe locally.
                    cur.execute(f"UPDATE {table_name} SET {set_cols} WHERE id=?", vals + [row_id])
            elif table_name == 'wallet_payments' and row_info.get('signature'):
                vals = list(metrics_payload.values()) + [row_info['signature']]
                cur.execute(f"UPDATE {table_name} SET {set_cols} WHERE signature=?", vals)
            elif row_id:
                vals = list(metrics_payload.values()) + [row_id]
                cur.execute(f"UPDATE {table_name} SET {set_cols} WHERE id=?", vals)
            conn.commit()
    except Exception as e_sq:
        debug_log(f"[PERF SCANNER] Local SQLite update notice on {table_name}: {e_sq}")


async def scan_post_signal_performance_batch(max_rows_per_table=60):
    """Scan due rows in dex_validated and wallet_payments using DexScreener + GeckoTerminal OHLCV candles."""
    with perf_scanner_lock:
        if perf_scanner_runtime['running']:
            return {'updated': 0, 'status': 'already_running'}
        perf_scanner_runtime['running'] = True
        perf_scanner_runtime['last_error'] = None

    updated_count = 0
    skipped_rate_limited = 0
    try:
        now_ts = int(time.time())
        due_items = []

        dv_ts_expr = (
            "(CASE "
            "WHEN COALESCE(fetched_at, 0) > 1000000000000 THEN fetched_at / 1000 "
            "WHEN COALESCE(fetched_at, 0) > 1000000000 THEN fetched_at "
            "WHEN COALESCE(published_at, 0) > 1000000000000 THEN published_at / 1000 "
            "WHEN COALESCE(published_at, 0) > 1000000000 THEN published_at "
            "WHEN COALESCE(payment_timestamp, 0) > 1000000000000 THEN payment_timestamp / 1000 "
            "WHEN COALESCE(payment_timestamp, 0) > 1000000000 THEN payment_timestamp "
            "ELSE CAST(COALESCE(strftime('%s', created_at), 0) AS INTEGER) END)"
        )
        wp_ts_expr = (
            "(CASE "
            "WHEN COALESCE(timestamp, 0) > 1000000000000 THEN timestamp / 1000 "
            "WHEN COALESCE(timestamp, 0) > 0 THEN timestamp "
            "ELSE CAST(COALESCE(strftime('%s', created_at), 0) AS INTEGER) END)"
        )

        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            dv_list = []
            wp_list = []

            try:
                dv_rows = cursor.execute(f"""
                    SELECT id, ca, market_cap, signal_type, payment_timestamp, order_status,
                           {dv_ts_expr} AS event_ts,
                           COALESCE(perf_checked_stage, 0) AS stage,
                           max_mc_1h, peak_mult_1h, min_mc_1h, min_dd_1h_pct, pre_peak_low_mc_1h, pre_peak_dd_1h_pct, path_1h,
                           max_mc_4h, peak_mult_4h, min_mc_4h, min_dd_4h_pct, pre_peak_low_mc_4h, pre_peak_dd_4h_pct, path_4h
                    FROM dex_validated
                    WHERE ca IS NOT NULL AND TRIM(ca) != ''
                      AND market_cap IS NOT NULL AND TRIM(market_cap) != '' AND UPPER(TRIM(market_cap)) != 'UNKNOWN'
                      AND (
                          (COALESCE(perf_checked_stage, 0) = 0 AND ({now_ts} - {dv_ts_expr}) >= 3600)
                          OR (COALESCE(perf_checked_stage, 0) = 1 AND ({now_ts} - {dv_ts_expr}) >= 14400)
                          OR (COALESCE(perf_checked_stage, 0) = 2 AND (min_mc_1h IS NULL OR COALESCE(min_mc_1h, 0) = 0))
                          OR (max_mc_1h IS NULL AND ({now_ts} - {dv_ts_expr}) >= 3600)
                          OR (max_mc_4h IS NULL AND ({now_ts} - {dv_ts_expr}) >= 14400)
                      )
                    ORDER BY id DESC
                    LIMIT ?
                """, (max_rows_per_table,)).fetchall()

                for r in dv_rows:
                    rd = dict(r) if isinstance(r, dict) else {
                        'id': r[0], 'ca': r[1], 'market_cap': r[2], 'signal_type': r[3],
                        'payment_timestamp': r[4], 'order_status': r[5], 'event_ts': r[6], 'stage': r[7],
                        'max_mc_1h': r[8], 'peak_mult_1h': r[9], 'min_mc_1h': r[10], 'min_dd_1h_pct': r[11],
                        'pre_peak_low_mc_1h': r[12], 'pre_peak_dd_1h_pct': r[13], 'path_1h': r[14],
                        'max_mc_4h': r[15] if len(r) > 15 else None,
                        'peak_mult_4h': r[16] if len(r) > 16 else None,
                        'min_mc_4h': r[17] if len(r) > 17 else None,
                        'min_dd_4h_pct': r[18] if len(r) > 18 else None,
                        'pre_peak_low_mc_4h': r[19] if len(r) > 19 else None,
                        'pre_peak_dd_4h_pct': r[20] if len(r) > 20 else None,
                        'path_4h': r[21] if len(r) > 21 else None,
                    }
                    rd['table'] = 'dex_validated'
                    dv_list.append(rd)
            except Exception as e_dv:
                debug_log(f"[PERF] SQLite dex_validated query: {e_dv}")

            try:
                wp_rows = cursor.execute(f"""
                    SELECT id, ca, market_cap, signature,
                           {wp_ts_expr} AS event_ts,
                           COALESCE(perf_checked_stage, 0) AS stage,
                           max_mc_1h, peak_mult_1h, min_mc_1h, min_dd_1h_pct, pre_peak_low_mc_1h, pre_peak_dd_1h_pct, path_1h,
                           max_mc_4h, peak_mult_4h, min_mc_4h, min_dd_4h_pct, pre_peak_low_mc_4h, pre_peak_dd_4h_pct, path_4h,
                           timestamp
                    FROM wallet_payments
                    WHERE ca IS NOT NULL AND TRIM(ca) != ''
                      AND token_name IS NOT NULL AND TRIM(token_name) != ''
                      AND UPPER(TRIM(token_name)) NOT IN ('SOL', 'USDT', 'USDC', 'WSOL', 'WRAPPED SOL', 'UNKNOWN')
                      AND market_cap IS NOT NULL AND TRIM(market_cap) != '' AND UPPER(TRIM(market_cap)) != 'UNKNOWN'
                      AND (
                          (COALESCE(perf_checked_stage, 0) = 0 AND ({now_ts} - {wp_ts_expr}) >= 3600)
                          OR (COALESCE(perf_checked_stage, 0) = 1 AND ({now_ts} - {wp_ts_expr}) >= 14400)
                          OR (COALESCE(perf_checked_stage, 0) = 2 AND (min_mc_1h IS NULL OR COALESCE(min_mc_1h, 0) = 0))
                          OR (max_mc_1h IS NULL AND ({now_ts} - {wp_ts_expr}) >= 3600)
                          OR (max_mc_4h IS NULL AND ({now_ts} - {wp_ts_expr}) >= 14400)
                      )
                    ORDER BY id DESC
                    LIMIT ?
                """, (max_rows_per_table,)).fetchall()

                for r in wp_rows:
                    rd = dict(r) if isinstance(r, dict) else {
                        'id': r[0], 'ca': r[1], 'market_cap': r[2], 'signature': r[3],
                        'event_ts': r[4], 'stage': r[5],
                        'max_mc_1h': r[6], 'peak_mult_1h': r[7], 'min_mc_1h': r[8], 'min_dd_1h_pct': r[9],
                        'pre_peak_low_mc_1h': r[10], 'pre_peak_dd_1h_pct': r[11], 'path_1h': r[12],
                        'max_mc_4h': r[13] if len(r) > 13 else None,
                        'peak_mult_4h': r[14] if len(r) > 14 else None,
                        'min_mc_4h': r[15] if len(r) > 15 else None,
                        'min_dd_4h_pct': r[16] if len(r) > 16 else None,
                        'pre_peak_low_mc_4h': r[17] if len(r) > 17 else None,
                        'pre_peak_dd_4h_pct': r[18] if len(r) > 18 else None,
                        'path_4h': r[19] if len(r) > 19 else None,
                        'timestamp': r[20] if len(r) > 20 else None,
                    }
                    rd['table'] = 'wallet_payments'
                    wp_list.append(rd)
            except Exception as e_wp:
                debug_log(f"[PERF] SQLite wallet_payments query: {e_wp}")

            # Include due rows from both dex_validated and wallet_payments (DEX Paid)
            if dv_list:
                due_items.extend(dv_list)
            if wp_list:
                due_items.extend(wp_list)

        # Check Supabase for un-enriched wallet_payments rows if local due items have capacity
        if len(due_items) < max_rows_per_table * 2 and supabase_client:
            try:
                one_hr_ago = now_ts - 3600
                res_sb = supabase_client.table('wallet_payments').select(
                    'id, ca, market_cap, signature, timestamp, created_at, perf_checked_stage, max_mc_1h, peak_mult_1h, min_mc_1h, min_dd_1h_pct, pre_peak_low_mc_1h, pre_peak_dd_1h_pct, path_1h, max_mc_4h, peak_mult_4h, min_mc_4h, min_dd_4h_pct, pre_peak_low_mc_4h, pre_peak_dd_4h_pct, path_4h, formatted_message, liquidity_usd, volume_24h, metrics_data'
                ).is_('peak_mult_1h', 'null').lte('timestamp', one_hr_ago).order('id', desc=True).limit(max_rows_per_table).execute()

                if not res_sb.data:
                    four_hrs_ago = now_ts - 14400
                    res_sb = supabase_client.table('wallet_payments').select(
                        'id, ca, market_cap, signature, timestamp, created_at, perf_checked_stage, max_mc_1h, peak_mult_1h, min_mc_1h, min_dd_1h_pct, pre_peak_low_mc_1h, pre_peak_dd_1h_pct, path_1h, max_mc_4h, peak_mult_4h, min_mc_4h, min_dd_4h_pct, pre_peak_low_mc_4h, pre_peak_dd_4h_pct, path_4h, formatted_message, liquidity_usd, volume_24h, metrics_data'
                    ).eq('perf_checked_stage', 1).is_('max_mc_4h', 'null').lte('timestamp', four_hrs_ago).order('id', desc=True).limit(max_rows_per_table).execute()

                for r in (res_sb.data or []):
                    ts = r.get('timestamp') or 0
                    if ts > 1000000000000:
                        ev_ts = ts / 1000
                    elif ts > 0:
                        ev_ts = ts
                    elif r.get('created_at'):
                        try:
                            dt = datetime.fromisoformat(str(r['created_at']).replace('Z', '+00:00'))
                            ev_ts = int(dt.timestamp())
                        except Exception:
                            ev_ts = 0
                    else:
                        ev_ts = 0
                    due_items.append({
                        'id': r['id'],
                        'ca': r.get('ca'),
                        'market_cap': r.get('market_cap'),
                        'signature': r.get('signature'),
                        'timestamp': r.get('timestamp'),
                        'event_ts': ev_ts,
                        'stage': r.get('perf_checked_stage', 0),
                        'max_mc_1h': r.get('max_mc_1h'),
                        'peak_mult_1h': r.get('peak_mult_1h'),
                        'min_mc_1h': r.get('min_mc_1h'),
                        'min_dd_1h_pct': r.get('min_dd_1h_pct'),
                        'pre_peak_low_mc_1h': r.get('pre_peak_low_mc_1h'),
                        'pre_peak_dd_1h_pct': r.get('pre_peak_dd_1h_pct'),
                        'path_1h': r.get('path_1h'),
                        'max_mc_4h': r.get('max_mc_4h'),
                        'peak_mult_4h': r.get('peak_mult_4h'),
                        'min_mc_4h': r.get('min_mc_4h'),
                        'min_dd_4h_pct': r.get('min_dd_4h_pct'),
                        'pre_peak_low_mc_4h': r.get('pre_peak_low_mc_4h'),
                        'pre_peak_dd_4h_pct': r.get('pre_peak_dd_4h_pct'),
                        'path_4h': r.get('path_4h'),
                        'liquidity_usd': r.get('liquidity_usd'),
                        'volume_24h': r.get('volume_24h'),
                        'metrics_data': r.get('metrics_data'),
                        'formatted_message': r.get('formatted_message'),
                        'table': 'wallet_payments'
                    })
            except Exception as sb_err:
                debug_log(f"[PERF] Supabase fallback due_items query error: {sb_err}")

        if not due_items:
            with perf_scanner_lock:
                perf_scanner_runtime['last_run_at'] = datetime.now(timezone.utc).isoformat()
                perf_scanner_runtime['last_batch_updated'] = 0
            return {'updated': 0, 'status': 'up_to_date'}

        unique_cas = list(dict.fromkeys(item['ca'] for item in due_items if item.get('ca')))
        pool_map = await asyncio.to_thread(_fetch_dexscreener_pools_batch_sync, unique_cas)

        for item in due_items:
            ca = item['ca']
            entry_mc = parse_mc_usd(item.get('market_cap'))
            t0 = int(item.get('event_ts') or 0)
            if entry_mc <= 0 or t0 <= 0:
                _persist_row_perf_metrics(item['table'], item, {'perf_checked_stage': 3})
                continue

            age_sec = now_ts - t0
            token_meta = pool_map.get(ca) or {'pools': [], 'mc_now': 0.0, 'price_now': 0.0, 'supply': 1_000_000_000.0}
            pools = token_meta.get('pools') or []
            supply = token_meta.get('supply') or 1_000_000_000.0
            mc_now = token_meta.get('mc_now') or 0.0

            # Fallback directly to CoinGecko/GeckoTerminal pool lookup if DexScreener returned no pool
            if not pools:
                gecko_meta = await asyncio.to_thread(_fetch_geckoterminal_pool_direct_sync, ca)
                if gecko_meta is GECKO_RATE_LIMITED:
                    skipped_rate_limited += 1
                    continue  # leave the row due; retry on a later pass
                if gecko_meta and gecko_meta.get('pools'):
                    token_meta = gecko_meta
                    pools = token_meta.get('pools') or []
                    supply = token_meta.get('supply') or supply
                    mc_now = token_meta.get('mc_now') or mc_now
                    pool_map[ca] = token_meta

            candles = []
            for pool_addr in pools[:1]:
                candles = await _fetch_geckoterminal_ohlcv(pool_addr, t0, now_ts)
                if candles:
                    break
            if candles is GECKO_RATE_LIMITED:
                # Price history unavailable right now (throttled/unreachable). Do NOT fill the
                # row with an estimate - leave it due so a later pass computes real values.
                skipped_rate_limited += 1
                continue

            payload = {}
            is_rescan = (item.get('stage', 0) == 2 and (item.get('min_mc_1h') is None or float(item.get('min_mc_1h') or 0.0) == 0.0))
            # Compute 1h metrics if not computed yet or if rescanning a zero-stage record
            if item.get('stage', 0) == 0 or item.get('max_mc_1h') is None or is_rescan:
                m1 = compute_window_peak_and_trajectory(entry_mc, t0, 3600, candles, supply, mc_now=mc_now, age_sec=age_sec)
                if m1:
                    payload.update({
                        'max_mc_1h': m1['max_mc'],
                        'peak_mult_1h': m1['peak_mult'],
                        'min_mc_1h': m1['min_mc'],
                        'min_dd_1h_pct': m1['min_dd_pct'],
                        'pre_peak_low_mc_1h': m1['pre_peak_low_mc'],
                        'pre_peak_dd_1h_pct': m1['pre_peak_dd_pct'],
                        'path_1h': m1['path'],
                    })
            else:
                m1 = {
                    'max_mc': float(item.get('max_mc_1h') or entry_mc),
                    'peak_mult': float(item.get('peak_mult_1h') or 1.0),
                    'min_mc': float(item.get('min_mc_1h') or entry_mc),
                    'min_dd_pct': float(item.get('min_dd_1h_pct') or 0.0),
                    'pre_peak_low_mc': float(item.get('pre_peak_low_mc_1h') or entry_mc),
                    'pre_peak_dd_pct': float(item.get('pre_peak_dd_1h_pct') or 0.0),
                    'path': item.get('path_1h') or 'FLAT',
                }

            # Compute 4h metrics if at least 4 hours old
            if age_sec >= 14400:
                if item.get('max_mc_4h') is None or is_rescan:
                    m4 = compute_window_peak_and_trajectory(entry_mc, t0, 14400, candles, supply, mc_now=mc_now, age_sec=age_sec)
                else:
                    m4 = {
                        'max_mc': float(item.get('max_mc_4h') or entry_mc),
                        'peak_mult': float(item.get('peak_mult_4h') or 1.0),
                        'min_mc': float(item.get('min_mc_4h') or entry_mc),
                        'min_dd_pct': float(item.get('min_dd_4h_pct') or 0.0),
                        'pre_peak_low_mc': float(item.get('pre_peak_low_mc_4h') or entry_mc),
                        'pre_peak_dd_pct': float(item.get('pre_peak_dd_4h_pct') or 0.0),
                        'path': item.get('path_4h') or 'FLAT',
                    }
                if m4 and m1:
                    # Guarantee mathematical consistency: 4h window includes 1h window
                    if m1['max_mc'] >= m4['max_mc']:
                        m4['max_mc'] = m1['max_mc']
                        m4['peak_mult'] = m1['peak_mult']
                        m4['pre_peak_low_mc'] = m1['pre_peak_low_mc']
                        m4['pre_peak_dd_pct'] = m1['pre_peak_dd_pct']
                    if m1['min_mc'] < m4['min_mc']:
                        m4['min_mc'] = m1['min_mc']
                        m4['min_dd_pct'] = m1['min_dd_pct']
                if m4:
                    payload.update({
                        'max_mc_4h': m4['max_mc'],
                        'peak_mult_4h': m4['peak_mult'],
                        'min_mc_4h': m4['min_mc'],
                        'min_dd_4h_pct': m4['min_dd_pct'],
                        'pre_peak_low_mc_4h': m4['pre_peak_low_mc'],
                        'pre_peak_dd_4h_pct': m4['pre_peak_dd_pct'],
                        'path_4h': m4['path'],
                        'perf_checked_stage': 3,
                    })
                else:
                    payload['perf_checked_stage'] = 3
            else:
                payload['perf_checked_stage'] = 1

            if item.get('table') == 'wallet_payments':
                if (item.get('liquidity_usd') is None or float(item.get('liquidity_usd') or 0) == 0) and token_meta.get('liquidity_usd'):
                    payload['liquidity_usd'] = token_meta['liquidity_usd']
                if (item.get('volume_24h') is None or float(item.get('volume_24h') or 0) == 0) and token_meta.get('volume_24h'):
                    payload['volume_24h'] = token_meta['volume_24h']

                m_existing = item.get('metrics_data')
                if isinstance(m_existing, str):
                    try:
                        m_existing = json.loads(m_existing)
                    except Exception:
                        m_existing = None
                has_dummy_top10 = isinstance(m_existing, dict) and m_existing.get('top10_concentration_pct') in (25.0, 25)

                if not item.get('metrics_data') or has_dummy_top10:
                    rc_res = fetch_token_rugcheck_sync(ca)
                    syn = synthesize_token_indicators({
                        'ca': ca,
                        'perf_5m': token_meta.get('perf_5m') or item.get('perf_5m') or 0.0,
                        'perf_1h': token_meta.get('perf_1h') or item.get('perf_1h') or 0.0,
                        'perf_6h': token_meta.get('perf_6h') or item.get('perf_6h') or 0.0,
                        'perf_24h': token_meta.get('perf_24h') or item.get('perf_24h') or 0.0,
                        'liquidity_usd': payload.get('liquidity_usd') or item.get('liquidity_usd') or 0.0,
                        'volume_5m': token_meta.get('volume_5m') or item.get('volume_5m') or 0.0,
                        'volume_1h': token_meta.get('volume_1h') or item.get('volume_1h') or 0.0,
                        'volume_6h': token_meta.get('volume_6h') or item.get('volume_6h') or 0.0,
                        'volume_24h': payload.get('volume_24h') or item.get('volume_24h') or 0.0,
                        'market_cap': item.get('market_cap'),
                        'formatted_message': item.get('formatted_message'),
                    })
                    if rc_res and rc_res.get('top10_concentration_pct') is not None:
                        if syn:
                            syn['top10_concentration_pct'] = rc_res['top10_concentration_pct']
                            if syn.get('indicators'):
                                syn['indicators']['holder_concentration'] = rc_res['top10_concentration_pct']
                    if syn:
                        payload['metrics_data'] = syn

            if payload:
                _persist_row_perf_metrics(item['table'], item, payload)
                updated_count += 1

        with perf_scanner_lock:
            perf_scanner_runtime['last_run_at'] = datetime.now(timezone.utc).isoformat()
            perf_scanner_runtime['last_batch_updated'] = updated_count
            perf_scanner_runtime['total_updated'] += updated_count

        print(f"[PERF SCANNER] Pass complete: {updated_count} rows filled, {skipped_rate_limited} left for retry (GeckoTerminal unavailable).", flush=True)
        return {'updated': updated_count, 'status': 'completed'}
    except Exception as e:
        import traceback
        print(f"[PERF SCANNER] Error in scan_post_signal_performance_batch: {traceback.format_exc()}", flush=True)
        with perf_scanner_lock:
            perf_scanner_runtime['last_error'] = str(e)
        return {'updated': updated_count, 'status': 'error', 'error': str(e)}
    finally:
        with perf_scanner_lock:
            perf_scanner_runtime['running'] = False


async def post_signal_perf_loop():
    """Background scanner for 1h & 4h OHLCV Peak & Trajectory metrics."""
    print("[PERF SCANNER] Started 1h/4h OHLCV Peak & Trajectory background scanner.", flush=True)
    _sync_perf_scanner_settings()
    await asyncio.sleep(5)
    while True:
        try:
            _sync_perf_scanner_settings()
            interval_min = float(perf_scanner_runtime.get('scan_interval_minutes', 15.0) or 15.0)
            backfill_on = bool(perf_scanner_runtime.get('backfill_wallet_payments', False))

            # If interval <= 0 and backfill is off, auto-scanning is paused
            if interval_min <= 0 and not backfill_on:
                with perf_scanner_lock:
                    perf_scanner_runtime['next_run_at'] = None
                for _ in range(6):
                    if perf_scanner_runtime.get('wake_requested', False):
                        with perf_scanner_lock:
                            perf_scanner_runtime['wake_requested'] = False
                        break
                    await asyncio.sleep(5)
                continue

            res = await scan_post_signal_performance_batch(max_rows_per_table=40)
            updated = int(res.get('updated') or 0)

            # If backfill is enabled and rows were updated, loop quickly (3s)
            if updated > 0 and backfill_on:
                wait_seconds = 3
            elif backfill_on and res.get('status') == 'up_to_date':
                print("[PERF SCANNER] All paid transactions fully backfilled! Turning off backfill mode.", flush=True)
                with perf_scanner_lock:
                    perf_scanner_runtime['backfill_wallet_payments'] = False
                wait_seconds = int(max(10, interval_min * 60)) if interval_min > 0 else 60
            else:
                wait_seconds = int(max(10, interval_min * 60)) if interval_min > 0 else 60

            with perf_scanner_lock:
                perf_scanner_runtime['next_run_at'] = datetime.fromtimestamp(time.time() + wait_seconds, tz=timezone.utc).isoformat()

            # Responsive wait loop
            elapsed = 0
            while elapsed < wait_seconds:
                if perf_scanner_runtime.get('wake_requested', False):
                    with perf_scanner_lock:
                        perf_scanner_runtime['wake_requested'] = False
                    break
                sleep_slice = min(5, wait_seconds - elapsed)
                await asyncio.sleep(sleep_slice)
                elapsed += sleep_slice
                _sync_perf_scanner_settings()
                if perf_scanner_runtime.get('backfill_wallet_payments', False) and updated > 0:
                    wait_seconds = 5
                else:
                    curr_min = float(perf_scanner_runtime.get('scan_interval_minutes', 15.0) or 15.0)
                    if curr_min > 0:
                        new_target = int(max(10, curr_min * 60))
                        if new_target < wait_seconds:
                            wait_seconds = new_target

        except Exception as e:
            print(f"[PERF SCANNER] Loop error: {e}", flush=True)
            await asyncio.sleep(30)
