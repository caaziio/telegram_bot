"""DEX Validated feed scanning, websocket workers and auto-scanner loop."""

import asyncio
import json
from datetime import datetime, timezone
import time
import requests
import websockets
from collections import OrderedDict

from bot import state
from bot.caches import (
    bounded_cache_set,
    dex_validated_active_ws_feeds,
    dex_validated_runtime,
    dex_validated_runtime_lock,
    orders_check_cache,
)
from bot.config import debug_log
from bot.db.connection import get_db, get_settings
from bot.services.cto_processor import process_single_cto_item

DEX_VALIDATED_FEED_SPECS = (
    ('cto', 'communityTakeover', 'wss://api.dexscreener.com/community-takeovers/latest/v1', 'https://api.dexscreener.com/community-takeovers/latest/v1'),
    ('boost', 'tokenBoost', 'wss://api.dexscreener.com/token-boosts/latest/v1', 'https://api.dexscreener.com/token-boosts/latest/v1'),
    ('dex', 'tokenProfile', 'wss://api.dexscreener.com/token-profiles/latest/v1', 'https://api.dexscreener.com/token-profiles/latest/v1'),
    ('dex', 'tokenAd', 'wss://api.dexscreener.com/ads/latest/v1', 'https://api.dexscreener.com/ads/latest/v1'),
)


def get_enabled_dex_validated_categories(settings):
    return {
        category for category, setting_key in (
            ('boost', 'dex_validated_include_boosts'),
            ('cto', 'dex_validated_include_cto'),
            ('dex', 'dex_validated_include_dex'),
        )
        if str(settings.get(setting_key, 'true')).lower() == 'true'
    }


def extract_dex_validated_events(payload, category, order_type, fetched_at_ms=None):
    """Normalize raw Dexscreener WebSocket/HTTP feed payloads into Solana event items (newest first)."""
    if fetched_at_ms is None:
        fetched_at_ms = int(time.time() * 1000)

    if isinstance(payload, dict):
        # DEX Screener's {"limit": 90, "data": [...]} WebSocket envelopes append newest items at the end.
        data = payload.get('data')
        raw_items = list(reversed(data)) if isinstance(data, list) else []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    events = []
    for event in raw_items:
        if not isinstance(event, dict):
            continue
        addr = event.get('tokenAddress')
        chain = str(event.get('chainId') or '').lower()
        if not addr or chain != 'solana':
            continue

        # Date-backed feeds use their publish time. Boosts have no timestamp,
        # so totalAmount changes identify a new published boost for the same token.
        marker = event.get('claimDate') or event.get('date')
        if marker:
            try:
                payment_timestamp = int(datetime.fromisoformat(
                    str(marker).replace('Z', '+00:00')
                ).timestamp() * 1000)
            except (TypeError, ValueError):
                payment_timestamp = 0
        total_boosts = 0
        boost_amount = 0
        if category == 'boost':
            try:
                total_boosts = int(float(event.get('totalAmount') or event.get('amount') or 0))
                boost_amount = int(float(event.get('amount') or 0))
            except (ValueError, TypeError):
                total_boosts = 0
                boost_amount = 0
            payment_timestamp = total_boosts
        else:
            payment_timestamp = 0

        events.append({
            'tokenAddress': addr,
            'chainId': chain,
            'order_type': order_type,
            'order_status': 'approved',
            'payment_timestamp': payment_timestamp,
            'published_at': payment_timestamp if marker else None,
            'fetched_at': fetched_at_ms,
            'event_category': category,
            'storage_table': 'dex_validated',
            'persist_db': True,
            'total_boosts': total_boosts,
            'boost_amount': boost_amount,
        })
    return events


async def fetch_dex_validated_feed_snapshot(category, order_type, ws_url, http_url):
    """One-shot WebSocket snapshot fetch for manual/test scans, with HTTP fallback."""
    fetched_at_ms = int(time.time() * 1000)
    try:
        async with websockets.connect(
            ws_url,
            open_timeout=8,
            close_timeout=2,
            max_size=4 * 1024 * 1024,
        ) as ws:
            raw_msg = await asyncio.wait_for(ws.recv(), timeout=8)
            fetched_at_ms = int(time.time() * 1000)
            payload = json.loads(raw_msg)
            return extract_dex_validated_events(payload, category, order_type, fetched_at_ms)
    except Exception as ws_err:
        debug_log(f"[DEX VALIDATED] One-shot WS snapshot fallback to HTTP for {order_type}: {ws_err}")
        response = await asyncio.to_thread(requests.get, http_url, timeout=8)
        fetched_at_ms = int(time.time() * 1000)
        if response.status_code != 200:
            return []
        return extract_dex_validated_events(response.json(), category, order_type, fetched_at_ms)


async def perform_cto_scan(target_channel, workflow_id=None, test_mode=False, validated_only=False):
    scan_started = time.monotonic()
    try:
        settings = get_settings()
        if validated_only:
            # DEX Validated is intentionally isolated from the SOL-payment scanner.
            # Only Dexscreener-confirmed orders are eligible for this system.
            fetch_approved = True
            fetch_pending = False
            enabled_categories = get_enabled_dex_validated_categories(settings)
        else:
            fetch_approved = settings.get('cto_fetch_approved', 'true').lower() == 'true'
            fetch_pending = settings.get('cto_fetch_pending', 'true').lower() == 'true'
            enabled_categories = {'boost', 'cto', 'dex'}

        # Fallback for old databases
        if not validated_only and 'cto_fetch_approved' not in settings and 'cto_fetch_pending' not in settings:
            scan_mode = settings.get('cto_scan_mode', 'both')
            fetch_approved = scan_mode in ('approved', 'both')
            fetch_pending = scan_mode in ('pending', 'both')

        tokens_to_process = []

        # DEX Validated reacts to tokens appearing in Dexscreener's published
        # feeds. The SOL-payment scanner keeps the order-status lookup path.
        try:
            def feed_items(response):
                if response.status_code != 200:
                    return []
                payload = response.json()
                return payload if isinstance(payload, list) else []

            if validated_only:
                active_specs = [
                    spec for spec in DEX_VALIDATED_FEED_SPECS
                    if spec[0] in enabled_categories
                ]
                feed_event_lists = await asyncio.gather(*(
                    fetch_dex_validated_feed_snapshot(category, order_type, ws_url, http_url)
                    for category, order_type, ws_url, http_url in active_specs
                ))
                # Interleave across CTO, Boost, Profile, and Ads so one large feed
                # does not starve the others within the 40-item manual batch cap.
                max_len = max((len(lst) for lst in feed_event_lists), default=0)
                for idx in range(max_len):
                    for lst in feed_event_lists:
                        if idx < len(lst):
                            tokens_to_process.append(lst[idx])
            else:
                profiles_res = await asyncio.to_thread(
                    requests.get, 'https://api.dexscreener.com/token-profiles/latest/v1', timeout=8
                )
                boosts_res = await asyncio.to_thread(
                    requests.get, 'https://api.dexscreener.com/token-boosts/latest/v1', timeout=8
                )
                profiles = feed_items(profiles_res)
                boosts = feed_items(boosts_res)

                candidates = {}
                for source, entries in (('profile', profiles), ('boost', boosts)):
                    for entry in entries:
                        addr = entry.get('tokenAddress')
                        chain = str(entry.get('chainId') or '').lower()
                        if addr and chain == 'solana':
                            candidates.setdefault(addr, {'chain': chain, 'sources': set()})['sources'].add(source)

                now = time.time()
                for addr, candidate in candidates.items():
                    chain = candidate['chain']
                    cache_key = (chain, addr)
                    if not test_mode and now - orders_check_cache.get(cache_key, 0) < 120:
                        continue
                    bounded_cache_set(orders_check_cache, cache_key, now)
                    orders_res = await asyncio.to_thread(
                        requests.get, f'https://api.dexscreener.com/orders/v1/{chain}/{addr}', timeout=8
                    )
                    if orders_res.status_code != 200:
                        continue
                    orders_payload = orders_res.json()
                    orders_list = orders_payload.get('orders', []) if isinstance(orders_payload, dict) else orders_payload
                    for order in orders_list if isinstance(orders_list, list) else []:
                        status = str(order.get('status') or '').lower()
                        if not ((status == 'approved' and fetch_approved) or (status == 'processing' and fetch_pending)):
                            continue
                        raw_type = order.get('type', '')
                        normalized_type = str(raw_type).lower()
                        category = 'cto' if 'takeover' in normalized_type else ('boost' if 'boost' in normalized_type else 'dex')
                        tokens_to_process.append({
                            'tokenAddress': addr,
                            'chainId': chain,
                            'order_type': raw_type,
                            'order_status': status,
                            'payment_timestamp': order.get('paymentTimestamp'),
                            'event_category': category,
                        })
        except Exception as e:
            print(f"Error fetching Dexscreener published feeds/orders: {e}", flush=True)

        results = []

        # Limit processing to avoid timeouts
        seen_in_batch = set()
        for item in tokens_to_process[:40]:
            ca = item.get("tokenAddress")
            order_type = item.get("order_type")
            order_status = item.get("order_status")
            payment_timestamp = item.get("payment_timestamp")
            if not ca:
                continue

            batch_key = (ca, order_type, payment_timestamp, order_status)
            if batch_key in seen_in_batch:
                continue
            seen_in_batch.add(batch_key)

            token_info = await process_single_cto_item(item, target_channel, workflow_id, test_mode)
            if token_info:
                results.append(token_info)

        # Flushed realtime summary for production console logging
        scanned_count = len(results)
        passed_count = sum(1 for r in results if r.get("status") == "passed")
        duplicate_count = sum(1 for r in results if r.get("status") == "duplicate")
        dropped_count = sum(1 for r in results if r.get("status") == "dropped")
        debug_log(f"[CTO SCANNER] Done. Scanned: {scanned_count} | Forwarded: {passed_count} | Duplicates: {duplicate_count} | Dropped: {dropped_count}")

        if validated_only:
            fresh_results = [r for r in results if r.get('status') != 'duplicate']
            latencies = [
                float(r['detection_seconds']) for r in fresh_results
                if isinstance(r.get('detection_seconds'), (int, float))
            ]
            with dex_validated_runtime_lock:
                dex_validated_runtime['last_checked_at'] = datetime.now(timezone.utc).isoformat()
                dex_validated_runtime['last_scan_seconds'] = round(time.monotonic() - scan_started, 2)
                dex_validated_runtime['last_new_events'] = len(fresh_results)
                dex_validated_runtime['last_forwarded'] = passed_count
                dex_validated_runtime['last_detection_seconds'] = round(min(latencies), 2) if latencies else None
                dex_validated_runtime['total_checks'] += 1
                dex_validated_runtime['total_events'] += len(fresh_results)
                dex_validated_runtime['total_forwarded'] += passed_count
                dex_validated_runtime['last_error'] = None

        db_results = []
        try:
            allowed_statuses = []
            if fetch_approved:
                allowed_statuses.append('approved')
            if fetch_pending:
                allowed_statuses.append('processing')

            if allowed_statuses:
                placeholders = ','.join('?' for _ in allowed_statuses)
                history_table = 'dex_validated' if validated_only else 'cto_signals'
                formatted_message_column = 'formatted_message' if validated_only else "'' AS formatted_message"
                query = f'''
                    SELECT ca, name, platform, market_cap, age,
                           perf_5m, perf_1h, perf_6h, perf_24h,
                           status, dex_url, migration_status, signal_type,
                           payment_timestamp, order_status, reason, {formatted_message_column},
                           liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h
                    FROM {history_table}
                    WHERE order_status IN ({placeholders})
                    ORDER BY id DESC LIMIT 50
                '''
                conn = get_db()
                cursor = conn.cursor()
                db_signals = cursor.execute(query, allowed_statuses).fetchall()
            else:
                db_signals = []
            for row in db_signals:
                stored_type = str(row[12] or '').lower()
                stored_category = (
                    'boost' if 'boost' in stored_type else
                    'cto' if ('takeover' in stored_type or stored_type == 'cto') else
                    'dex'
                )
                if validated_only and stored_category not in enabled_categories:
                    continue
                db_results.append({
                    "ca": row[0],
                    "name": row[1],
                    "platform": row[2],
                    "market_cap": row[3],
                    "age": row[4],
                    "perf_5m": row[5],
                    "perf_1h": row[6],
                    "perf_6h": row[7],
                    "perf_24h": row[8],
                    "status": "passed" if row[9] == "forwarded" else "dropped",
                    "reason": row[15] if row[15] else ("Passed and Sent!" if row[9] == "forwarded" else "Dropped by Filter Rules"),
                    "dex_url": row[10],
                    "migration_status": row[11],
                    "signal_type": stored_category if validated_only else row[12],
                    "payment_timestamp": row[13],
                    "order_status": row[14],
                    "formatted_message": row[16] or "",
                    "liquidity_usd": row[17] or 0,
                    "volume_5m": row[18] or 0,
                    "volume_1h": row[19] or 0,
                    "volume_6h": row[20] or 0,
                    "volume_24h": row[21] or 0
                })
        except Exception as e:
            print(f"Error loading recent signals from DB: {e}", flush=True)

        # Merge new scan results and database results
        merged_results = list(results)
        merged_keys = {(r["ca"], r["signal_type"], r.get("payment_timestamp"), r.get("order_status")) for r in results}

        for r in db_results:
            key = (r["ca"], r["signal_type"], r.get("payment_timestamp"), r.get("order_status"))
            if key not in merged_keys:
                merged_results.append(r)
                merged_keys.add(key)

        return merged_results

    except Exception as e:
        import traceback
        print(f"Auto scan error: {traceback.format_exc()}")
        if validated_only:
            with dex_validated_runtime_lock:
                dex_validated_runtime['last_checked_at'] = datetime.now(timezone.utc).isoformat()
                dex_validated_runtime['last_scan_seconds'] = round(time.monotonic() - scan_started, 2)
                dex_validated_runtime['last_error'] = str(e)
        return []


async def _dex_validated_ws_worker(category, order_type, ws_url):
    """Persistent WebSocket listener for a single DEX Screener feed (CTO, Boost, Profile, or Ad).
    Always collects and enriches incoming events into dex_validated; forwards to Telegram when auto_scan is active.
    """
    seen_keys = OrderedDict()

    def mark_seen(key):
        seen_keys[key] = True
        while len(seen_keys) > 5000:
            seen_keys.popitem(last=False)

    while True:
        try:
            debug_log(f"[DEX VALIDATED WS] Connecting to {order_type} ({ws_url})...")
            async with websockets.connect(
                ws_url,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=2,
                max_size=4 * 1024 * 1024,
            ) as ws:
                with dex_validated_runtime_lock:
                    dex_validated_active_ws_feeds.add(order_type)
                    dex_validated_runtime['ws_connected'] = len(dex_validated_active_ws_feeds)
                    dex_validated_runtime['next_check_at'] = None
                    dex_validated_runtime['last_error'] = None
                debug_log(f"[DEX VALIDATED WS] Connected to {order_type} stream.")

                is_initial_snapshot = True
                while True:
                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        continue

                    frame_started = time.monotonic()
                    fetched_at_ms = int(time.time() * 1000)
                    try:
                        payload = json.loads(raw_msg)
                    except Exception as parse_err:
                        debug_log(f"[DEX VALIDATED WS] JSON parse error on {order_type}: {parse_err}")
                        continue

                    events = extract_dex_validated_events(payload, category, order_type, fetched_at_ms)
                    is_initial_frame = is_initial_snapshot
                    if is_initial_snapshot:
                        is_initial_snapshot = False
                    candidate_items = events

                    new_items = []
                    for item in candidate_items:
                        ev_key = (
                            item.get('tokenAddress'),
                            category,
                            item.get('payment_timestamp'),
                            item.get('order_status'),
                        )
                        if ev_key in seen_keys:
                            continue
                        new_items.append((ev_key, item))

                    settings = get_settings()
                    is_auto = str(settings.get('dex_validated_auto_scan', 'false')).lower() == 'true'
                    enabled_categories = get_enabled_dex_validated_categories(settings)
                    test_mode = str(settings.get('dex_validated_test_mode', 'false')).lower() == 'true'
                    tg_ready = bool(state.tg_client and await state.tg_client.is_user_authorized())
                    should_forward = is_auto and (not test_mode) and (not is_initial_frame) and (category in enabled_categories) and tg_ready
                    effective_test_mode = not should_forward

                    target = settings.get('dex_validated_target_channel', '')
                    workflow_id = settings.get('dex_validated_workflow_id', 'active_all')

                    results = []
                    for ev_key, item in new_items:
                        item['persist_db'] = True
                        token_info = await process_single_cto_item(item, target, workflow_id, effective_test_mode)
                        if token_info:
                            results.append(token_info)
                            if token_info.get('status') != 'error':
                                mark_seen(ev_key)
                            if token_info.get('status') != 'duplicate':
                                with dex_validated_runtime_lock:
                                    dex_validated_runtime['last_checked_at'] = datetime.now(timezone.utc).isoformat()
                        else:
                            mark_seen(ev_key)

                    passed_count = sum(1 for r in results if r.get('status') == 'passed' and should_forward)
                    fresh_results = [r for r in results if r.get('status') != 'duplicate']
                    latencies = [
                        float(r['detection_seconds']) for r in fresh_results
                        if isinstance(r.get('detection_seconds'), (int, float))
                    ]
                    if fresh_results:
                        debug_log(
                            f"[DEX VALIDATED WS] {order_type}: Enriched={len(fresh_results)} | "
                            f"Forwarded={passed_count} | Elapsed={time.monotonic() - frame_started:.2f}s"
                        )

                    with dex_validated_runtime_lock:
                        dex_validated_runtime['last_checked_at'] = datetime.now(timezone.utc).isoformat()
                        dex_validated_runtime['last_scan_seconds'] = round(time.monotonic() - frame_started, 2)
                        dex_validated_runtime['total_checks'] += 1
                        dex_validated_runtime['last_error'] = None
                        if fresh_results:
                            dex_validated_runtime['last_new_events'] = len(fresh_results)
                            dex_validated_runtime['last_forwarded'] = passed_count
                            dex_validated_runtime['total_events'] += len(fresh_results)
                            dex_validated_runtime['total_forwarded'] += passed_count
                            if latencies:
                                dex_validated_runtime['last_detection_seconds'] = round(min(latencies), 2)

        except Exception as e:
            print(f"[DEX VALIDATED WS] Error on {order_type} stream: {e}", flush=True)
            with dex_validated_runtime_lock:
                dex_validated_active_ws_feeds.discard(order_type)
                dex_validated_runtime['ws_connected'] = len(dex_validated_active_ws_feeds)
                dex_validated_runtime['last_error'] = f"{order_type}: {e}"
            await asyncio.sleep(2.0)
        finally:
            with dex_validated_runtime_lock:
                dex_validated_active_ws_feeds.discard(order_type)
                dex_validated_runtime['ws_connected'] = len(dex_validated_active_ws_feeds)


async def cto_auto_scanner_loop():
    """Background WebSocket supervisor for the independent DEX Validated system."""
    await asyncio.gather(*(
        _dex_validated_ws_worker(category, order_type, ws_url)
        for category, order_type, ws_url, _ in DEX_VALIDATED_FEED_SPECS
    ))
