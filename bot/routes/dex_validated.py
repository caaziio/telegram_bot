"""CTO scan, payment discovery and DEX Validated API."""

import asyncio
import re
import json
import time
import requests
from flask import Flask, render_template, request, jsonify

from bot import state
from bot.caches import (
    dex_validated_active_ws_feeds,
    dex_validated_runtime,
    dex_validated_runtime_lock,
)
from bot.config import debug_log
from bot.db.connection import db_lock, get_db, get_settings
from bot.engine.rules import token_name_pattern_for_sql
from bot.services.cto_processor import process_single_cto_item
from bot.services.dex_payments import discover_payment_wallet_logic
from bot.services.dex_scanner import perform_cto_scan
from bot.services.query_filters import (
    append_akoueflow_indicator_filters,
    append_post_signal_outcome_filters,
)
from bot.services.token_indicators import (
    sqlite_token_metric,
    extract_creation_age_str,
    extract_indicator_val,
    extract_migration_age_str,
    parse_age_minutes,
    parse_creation_age_minutes,
    parse_date_to_timestamp,
    parse_mc_usd,
    parse_migration_age_minutes,
    sqlite_regexp,
)
from bot.web import app

@app.route('/api/cto/discover_payment_address', methods=['POST'])
def discover_payment_address_route():
    try:
        settings = get_settings()
        api_key = settings.get('helius_api_key')
        if not api_key:
            return jsonify({"success": False, "error": "Helius API Key not configured."})

        # 1. Fetch active Solana tokens from DexScreener profiles & boosts to find fresh CTO orders
        fresh_candidates = []
        try:
            debug_log("[HELIUS DISCOVERY] Querying DexScreener profiles and boosts feeds for live tokens...")
            p_res = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=5)
            p_data = p_res.json() if p_res.status_code == 200 else []
            b_res = requests.get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=5)
            b_data = b_res.json() if b_res.status_code == 200 else []

            seen_cas = set()
            feed_tokens = []
            for item in p_data + b_data:
                ca = item.get("tokenAddress")
                chain = item.get("chainId")
                if ca and chain and chain.lower() == "solana" and ca not in seen_cas:
                    seen_cas.add(ca)
                    feed_tokens.append(ca)

            # Check orders for up to 15 latest tokens from the feed to find a recent payment
            for ca in feed_tokens[:15]:
                try:
                    orders_res = requests.get(f"https://api.dexscreener.com/orders/v1/solana/{ca}", timeout=5)
                    if orders_res.status_code == 200:
                        orders_data = orders_res.json()
                        for o in orders_data.get("orders", []):
                            if o.get("type") == "communityTakeover" and o.get("paymentTimestamp"):
                                fresh_candidates.append({
                                    "ca": ca,
                                    "payment_timestamp": int(o.get("paymentTimestamp"))
                                })
                except Exception as ex:
                    print(f"[HELIUS DISCOVERY] Error checking feed token orders for {ca}: {ex}")
        except Exception as ex:
            print(f"[HELIUS DISCOVERY] Error fetching DexScreener profiles/boosts: {ex}")

        # Sort fresh candidates by paymentTimestamp descending (newest first)
        fresh_candidates.sort(key=lambda x: x["payment_timestamp"], reverse=True)
        debug_log(f"[HELIUS DISCOVERY] Discovered {len(fresh_candidates)} candidate CTO orders in live feed.")

        # 2. Get fallback tokens from database
        db_candidates = []
        try:
            with db_lock:
                conn = get_db()
                cursor = conn.cursor()
                rows = cursor.execute('''
                    SELECT ca, payment_timestamp FROM cto_signals
                    WHERE payment_timestamp IS NOT NULL AND payment_timestamp > 0
                    ORDER BY id DESC LIMIT 10
                ''').fetchall()
            for r in rows:
                db_candidates.append({
                    "ca": r['ca'] if isinstance(r, dict) else r[0],
                    "payment_timestamp": r['payment_timestamp'] if isinstance(r, dict) else r[1]
                })
        except Exception as ex:
            print(f"[HELIUS DISCOVERY] Error fetching database candidates: {ex}")

        # 3. Combine list, prioritize live feed candidates (skipping duplicates)
        candidates_to_try = []
        seen_ca_try = set()
        for c in fresh_candidates:
            if c["ca"] not in seen_ca_try:
                seen_ca_try.add(c["ca"])
                candidates_to_try.append(c)

        for c in db_candidates:
            if c["ca"] not in seen_ca_try:
                seen_ca_try.add(c["ca"])
                candidates_to_try.append(c)

        if not candidates_to_try:
            return jsonify({"success": False, "error": "No candidate CTO orders found in live feed or database."})

        # Try finding the address from the candidates
        attempts = []
        for c in candidates_to_try:
            ca = c["ca"]
            payment_ts = c["payment_timestamp"]

            debug_log(f"[HELIUS DISCOVERY] Attempting to discover DexScreener payment address using token {ca} at timestamp {payment_ts}...")
            discovered_addr, reason = discover_payment_wallet_logic(ca, payment_ts, api_key)
            if discovered_addr:
                print(f"[HELIUS DISCOVERY] SUCCESS! Discovered payment address: {discovered_addr}")
                return jsonify({"success": True, "address": discovered_addr})
            else:
                attempts.append(f"Token {ca[:8]}: {reason}")

        error_msg = f"Could not identify payment address from candidates. Attempts logs: " + " | ".join(attempts)
        print(f"[HELIUS DISCOVERY] FAILURE: {error_msg}")
        return jsonify({"success": False, "error": error_msg})
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


@app.route('/api/cto/scan', methods=['POST'])
def scan_cto():
    data = request.json
    target_channel = data.get('target_channel')
    workflow_id = data.get('workflow_id')
    test_mode = data.get('test_mode', False)

    if not state.tg_client or not state.telethon_loop:
        return jsonify({"success": False, "error": "Telegram client not ready"})

    try:
        results = asyncio.run_coroutine_threadsafe(perform_cto_scan(target_channel, workflow_id, test_mode), state.telethon_loop).result(timeout=60)
        return jsonify({"success": True, "results": results})

    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})

@app.route('/api/dex-validated/scan', methods=['POST'])
def scan_dex_validated():
    """Run the Dexscreener-confirmed scanner independently of SOL payments."""
    data = request.json or {}
    target_channel = data.get('target_channel')
    workflow_id = data.get('workflow_id')
    test_mode = bool(data.get('test_mode', False))

    try:
        scan_coro = perform_cto_scan(target_channel, workflow_id, test_mode, validated_only=True)
        if state.tg_client and state.telethon_loop:
            results = asyncio.run_coroutine_threadsafe(scan_coro, state.telethon_loop).result(timeout=60)
        elif test_mode:
            results = asyncio.run(scan_coro)
        else:
            scan_coro.close()
            return jsonify({"success": False, "error": "Telegram client not ready"})
        return jsonify({"success": True, "results": results})
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/dex-validated/status', methods=['GET'])
def dex_validated_status():
    settings = get_settings()
    with dex_validated_runtime_lock:
        runtime = dict(dex_validated_runtime)
        runtime['ws_connected'] = len(dex_validated_active_ws_feeds)
    try:
        configured_interval = max(2, int(settings.get('dex_validated_scan_interval', 10) or 10))
    except (TypeError, ValueError):
        configured_interval = 10
    latest_id = 0
    total_records = 0
    try:
        with db_lock:
            conn = get_db()
            row = conn.cursor().execute("SELECT COALESCE(MAX(id), 0), COUNT(*) FROM dex_validated").fetchone()
            if row:
                latest_id = int(row[0] or 0)
                total_records = int(row[1] or 0)
    except Exception:
        pass
    runtime.update({
        'mode': 'websocket',
        'active': str(settings.get('dex_validated_auto_scan', 'false')).lower() == 'true',
        'interval': configured_interval,
        'latest_record_id': latest_id,
        'total_records': total_records,
    })
    return jsonify({'success': True, 'status': runtime})


@app.route('/api/dex-validated/records', methods=['GET'])
def get_dex_validated_records():
    """Return enriched DEX Validated records with full filtering, sorting, KPI stats, and pagination."""
    try:
        page = max(1, int(request.args.get('page', 1)))
        limit = min(max(10, int(request.args.get('limit', 100))), 50000)
        offset = (page - 1) * limit

        search = (request.args.get('search') or '').strip()
        category = (request.args.get('category') or 'all').strip().lower()
        status = (request.args.get('status') or 'all').strip().lower()
        platform = (request.args.get('platform') or 'all').strip().lower()
        exclude_platform = (request.args.get('exclude_platform') or 'none').strip().lower()
        timeframe = (request.args.get('timeframe') or 'all').strip().lower()
        date_from = request.args.get('date_from') or request.args.get('from_date')
        date_to = request.args.get('date_to') or request.args.get('to_date')
        min_mc = request.args.get('min_mc')
        max_mc = request.args.get('max_mc')
        min_liquidity = request.args.get('min_liquidity')
        max_liquidity = request.args.get('max_liquidity')
        volume_tf = (request.args.get('volume_tf') or '24h').strip().lower()
        min_volume = request.args.get('min_volume')
        max_volume = request.args.get('max_volume')
        perf_tf = (request.args.get('perf_tf') or '5m').strip().lower()
        min_perf = request.args.get('min_perf')
        max_perf = request.args.get('max_perf')
        min_age = request.args.get('min_age') or request.args.get('min_creation_age')
        max_age = request.args.get('max_age') or request.args.get('max_creation_age')
        min_migration_age = request.args.get('min_migration_age')
        max_migration_age = request.args.get('max_migration_age')
        migrated = (request.args.get('migrated') or 'all').strip().lower()
        token_name_pattern = (request.args.get('token_name_pattern') or '').strip()
        token_name_mode = (request.args.get('token_name_mode') or 'include').strip().lower()
        word_filter = (request.args.get('word_filter') or '').strip()
        sort_by = (request.args.get('sort_by') or 'id').lower()
        sort_order = 'ASC' if (request.args.get('sort_order') or 'desc').upper() == 'ASC' else 'DESC'

        ts_expr = (
            "(CASE "
            "WHEN COALESCE(fetched_at, 0) > 1000000000000 THEN fetched_at / 1000 "
            "WHEN COALESCE(published_at, 0) > 1000000000000 THEN published_at / 1000 "
            "WHEN COALESCE(payment_timestamp, 0) > 1000000000000 THEN payment_timestamp / 1000 "
            "ELSE CAST(COALESCE(strftime('%s', created_at), 0) AS INTEGER) END)"
        )

        valid_sorts = {
            'id': 'id',
            'timestamp': ts_expr,
            'token_name': 'LOWER(COALESCE(name, ""))',
            'name': 'LOWER(COALESCE(name, ""))',
            'ca': 'LOWER(COALESCE(ca, ""))',
            'category': 'LOWER(COALESCE(signal_type, migration_status, ""))',
            'signal_type': 'LOWER(COALESCE(signal_type, ""))',
            'perf_5m': 'perf_5m',
            'perf_1h': 'perf_1h',
            'perf_6h': 'perf_6h',
            'perf_24h': 'perf_24h',
            'status': 'LOWER(COALESCE(status, ""))',
            'reason': 'LOWER(COALESCE(reason, ""))',
            'market_cap': 'parse_mc_usd(market_cap)',
            'liquidity_usd': 'liquidity_usd',
            'volume_5m': 'volume_5m',
            'volume_1h': 'volume_1h',
            'volume_6h': 'volume_6h',
            'volume_24h': 'volume_24h',
            'age': 'parse_creation_age_minutes(age, formatted_message)',
            'creation_age': 'parse_creation_age_minutes(age, formatted_message)',
            'migration_age': 'parse_migration_age_minutes(NULL, formatted_message)',
            'peak_mult_1h': 'peak_mult_1h',
            'peak_mult_4h': 'peak_mult_4h',
            'max_mc_1h': 'max_mc_1h',
            'max_mc_4h': 'max_mc_4h',
            'pre_peak_dd_1h_pct': 'pre_peak_dd_1h_pct',
            'pre_peak_dd_4h_pct': 'pre_peak_dd_4h_pct',
            'min_dd_1h_pct': 'min_dd_1h_pct',
            'min_dd_4h_pct': 'min_dd_4h_pct',
            'net_buy_pressure': "get_indicator(metrics_data, 'net_buy_pressure')",
            'buy_dominance': "get_indicator(metrics_data, 'buy_dominance_pct')",
            'buy_vol_accel': "get_indicator(metrics_data, 'buy_vol_accel')",
            'buyer_seller_ratio': "get_indicator(metrics_data, 'buyer_seller_ratio')",
            'holder_concentration': "get_indicator(metrics_data, 'holder_concentration')",
            'round_trip_cost': "get_indicator(metrics_data, 'round_trip_cost')",
        }
        order_col = valid_sorts.get(sort_by, 'id')

        where_clauses = [
            "("
            "name IS NOT NULL AND TRIM(name) != '' "
            "AND UPPER(TRIM(name)) NOT IN ('SOL', 'USDT', 'USDC', 'WSOL', 'WRAPPED SOL', 'UNKNOWN') "
            "AND ca IS NOT NULL AND TRIM(ca) != '' "
            "AND market_cap IS NOT NULL AND TRIM(market_cap) != '' AND UPPER(TRIM(market_cap)) != 'UNKNOWN' "
            "AND age IS NOT NULL AND TRIM(age) != '' AND UPPER(TRIM(age)) != 'UNKNOWN'"
            ")"
        ]
        params = []

        if search:
            s_param = f"%{search}%"
            where_clauses.append("(name LIKE ? OR ca LIKE ? OR migration_status LIKE ? OR signal_type LIKE ? OR reason LIKE ?)")
            params.extend([s_param, s_param, s_param, s_param, s_param])

        if category and category != 'all':
            if category == 'cto':
                where_clauses.append("(LOWER(signal_type) = 'cto' OR LOWER(migration_status) LIKE '%takeover%')")
            elif category == 'boost':
                where_clauses.append("(LOWER(signal_type) = 'boost' OR LOWER(migration_status) LIKE '%boost%')")
            elif category == 'dex_profile':
                where_clauses.append("(LOWER(migration_status) LIKE '%profile%')")
            elif category == 'dex_ad':
                where_clauses.append("(LOWER(migration_status) LIKE '%ad%')")
            elif category == 'dex':
                where_clauses.append("(LOWER(signal_type) = 'dex' OR LOWER(migration_status) LIKE '%profile%' OR LOWER(migration_status) LIKE '%ad%')")

        if status != 'all' and status:
            where_clauses.append("LOWER(status) = ?")
            params.append(status)

        if platform != 'all' and platform:
            where_clauses.append("(LOWER(platform) LIKE ? OR LOWER(formatted_message) LIKE ?)")
            params.extend([f"%{platform}%", f"%{platform}%"])

        if exclude_platform != 'none' and exclude_platform:
            where_clauses.append("(LOWER(platform) NOT LIKE ? AND LOWER(COALESCE(formatted_message, '')) NOT LIKE ?)")
            params.extend([f"%{exclude_platform}%", f"%current dex: %{exclude_platform}%"])

        from_ts = parse_date_to_timestamp(date_from, is_end_of_day=False) if date_from else None
        to_ts = parse_date_to_timestamp(date_to, is_end_of_day=True) if date_to else None

        if from_ts is not None:
            where_clauses.append(f"{ts_expr} >= ?")
            params.append(from_ts)
        elif timeframe and timeframe != 'all' and timeframe != 'custom':
            now_ts = int(time.time())
            tf_seconds = {
                '1h': 3600,
                '6h': 21600,
                '24h': 86400,
                '7d': 604800,
                '30d': 2592000,
            }.get(timeframe)
            if tf_seconds:
                where_clauses.append(f"{ts_expr} >= ?")
                params.append(now_ts - tf_seconds)

        if to_ts is not None:
            where_clauses.append(f"{ts_expr} <= ?")
            params.append(to_ts)

        if min_mc:
            try:
                where_clauses.append("parse_mc_usd(market_cap) >= ?")
                params.append(float(min_mc))
            except ValueError:
                pass
        if max_mc:
            try:
                where_clauses.append("parse_mc_usd(market_cap) <= ?")
                params.append(float(max_mc))
            except ValueError:
                pass

        if min_liquidity:
            try:
                where_clauses.append("liquidity_usd >= ?")
                params.append(float(min_liquidity))
            except ValueError:
                pass
        if max_liquidity:
            try:
                where_clauses.append("liquidity_usd <= ?")
                params.append(float(max_liquidity))
            except ValueError:
                pass

        volume_col = f"volume_{volume_tf}" if volume_tf in ('5m', '1h', '6h', '24h') else 'volume_24h'
        if min_volume:
            try:
                where_clauses.append(f"{volume_col} >= ?")
                params.append(float(min_volume))
            except ValueError:
                pass
        if max_volume:
            try:
                where_clauses.append(f"{volume_col} <= ?")
                params.append(float(max_volume))
            except ValueError:
                pass

        perf_col = f"perf_{perf_tf}" if perf_tf in ('5m', '1h', '6h', '24h') else 'perf_5m'
        if min_perf:
            try:
                where_clauses.append(f"{perf_col} >= ?")
                params.append(float(min_perf))
            except ValueError:
                pass
        if max_perf:
            try:
                where_clauses.append(f"{perf_col} <= ?")
                params.append(float(max_perf))
            except ValueError:
                pass

        if min_age:
            try:
                where_clauses.append("parse_creation_age_minutes(age, formatted_message) >= ?")
                params.append(float(min_age))
            except ValueError:
                pass
        if max_age:
            try:
                where_clauses.append("parse_creation_age_minutes(age, formatted_message) <= ?")
                params.append(float(max_age))
            except ValueError:
                pass

        if min_migration_age:
            try:
                where_clauses.append("parse_migration_age_minutes(NULL, formatted_message) >= ?")
                params.append(float(min_migration_age))
            except ValueError:
                pass
        if max_migration_age:
            try:
                where_clauses.append("parse_migration_age_minutes(NULL, formatted_message) <= ?")
                params.append(float(max_migration_age))
            except ValueError:
                pass

        if migrated == 'yes':
            where_clauses.append("(formatted_message LIKE '%Migrated: Yes%' OR LOWER(platform) LIKE '%raydium%')")
        elif migrated == 'no':
            where_clauses.append("(formatted_message LIKE '%Migrated: No%' OR (COALESCE(formatted_message, '') NOT LIKE '%Migrated: Yes%' AND LOWER(COALESCE(platform, '')) NOT LIKE '%raydium%'))")

        if token_name_pattern and token_name_pattern.strip() not in ('', '.*'):
            pat = token_name_pattern_for_sql(token_name_pattern)
            if token_name_mode == 'exclude':
                where_clauses.append("NOT (name REGEXP ?)")
            else:
                where_clauses.append("(name REGEXP ?)")
            params.append(pat)

        if word_filter:
            where_clauses.append("NOT ((COALESCE(name, '') || '\n' || COALESCE(reason, '') || '\n' || COALESCE(formatted_message, '')) REGEXP ?)")
            params.append(word_filter)

        append_akoueflow_indicator_filters(request.args, where_clauses, params)
        append_post_signal_outcome_filters(request.args, where_clauses, params)

        where_str = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with db_lock:
            conn = get_db()
            conn.create_function('parse_mc_usd', 1, parse_mc_usd)
            conn.create_function('parse_age_minutes', 1, parse_age_minutes)
            conn.create_function('parse_creation_age_minutes', 2, parse_creation_age_minutes)
            conn.create_function('parse_migration_age_minutes', 2, parse_migration_age_minutes)
            conn.create_function('regexp', 2, sqlite_regexp)
            conn.create_function('get_indicator', 2, extract_indicator_val)
            conn.create_function('get_token_metric', 3, sqlite_token_metric)
            cursor = conn.cursor()

            count_query = f"SELECT COUNT(*) FROM dex_validated{where_str}"
            total_count = cursor.execute(count_query, params).fetchone()[0]

            stats_query = f"""
                SELECT
                    COUNT(*),
                    COUNT(CASE WHEN LOWER(status) = 'forwarded' THEN 1 END),
                    COALESCE(SUM(COALESCE(volume_24h, 0)), 0),
                    COUNT(DISTINCT ca),
                    COUNT(CASE WHEN LOWER(signal_type) = 'cto' OR LOWER(migration_status) LIKE '%takeover%' THEN 1 END),
                    COUNT(CASE WHEN LOWER(signal_type) = 'boost' OR LOWER(migration_status) LIKE '%boost%' THEN 1 END),
                    COUNT(CASE WHEN LOWER(signal_type) = 'dex' OR LOWER(migration_status) LIKE '%profile%' OR LOWER(migration_status) LIKE '%ad%' THEN 1 END)
                FROM dex_validated{where_str}
            """
            stats_row = cursor.execute(stats_query, params).fetchone()
            stats = {
                'total': stats_row[0] if stats_row else 0,
                'forwarded': stats_row[1] if stats_row else 0,
                'total_volume': round(float(stats_row[2] or 0.0), 2) if stats_row else 0.0,
                'unique_tokens': stats_row[3] if stats_row else 0,
                'cto_count': stats_row[4] if stats_row else 0,
                'boost_count': stats_row[5] if stats_row else 0,
                'dex_count': stats_row[6] if stats_row else 0,
            }

            data_query = f"""
                SELECT id, ca, name, platform, market_cap, age,
                       perf_5m, perf_1h, perf_6h, perf_24h,
                       liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                       status, dex_url, migration_status, signal_type,
                       payment_timestamp, order_status, published_at, fetched_at,
                       detection_seconds, reason, formatted_message, created_at,
                       {ts_expr} AS event_ts,
                       max_mc_1h, peak_mult_1h, min_mc_1h, min_dd_1h_pct, pre_peak_low_mc_1h, pre_peak_dd_1h_pct, path_1h,
                       max_mc_4h, peak_mult_4h, min_mc_4h, min_dd_4h_pct, pre_peak_low_mc_4h, pre_peak_dd_4h_pct, path_4h,
                       perf_checked_stage, metrics_data
                FROM dex_validated
                {where_str}
                ORDER BY {order_col} {sort_order} NULLS LAST
                LIMIT ? OFFSET ?
            """
            rows = cursor.execute(data_query, list(params) + [limit, offset]).fetchall()

        records = []
        for r in rows:
            row_dict = dict(r) if isinstance(r, dict) else {
                "id": r[0],
                "ca": r[1],
                "name": r[2],
                "platform": r[3],
                "market_cap": r[4],
                "age": r[5],
                "perf_5m": r[6],
                "perf_1h": r[7],
                "perf_6h": r[8],
                "perf_24h": r[9],
                "liquidity_usd": r[10],
                "volume_5m": r[11],
                "volume_1h": r[12],
                "volume_6h": r[13],
                "volume_24h": r[14],
                "status": r[15],
                "dex_url": r[16],
                "migration_status": r[17],
                "signal_type": r[18],
                "payment_timestamp": r[19],
                "order_status": r[20],
                "published_at": r[21],
                "fetched_at": r[22],
                "detection_seconds": r[23],
                "reason": r[24],
                "formatted_message": r[25],
                "created_at": r[26],
                "timestamp": r[27],
                "max_mc_1h": r[28] if len(r) > 28 else None,
                "peak_mult_1h": r[29] if len(r) > 29 else None,
                "min_mc_1h": r[30] if len(r) > 30 else None,
                "min_dd_1h_pct": r[31] if len(r) > 31 else None,
                "pre_peak_low_mc_1h": r[32] if len(r) > 32 else None,
                "pre_peak_dd_1h_pct": r[33] if len(r) > 33 else None,
                "path_1h": r[34] if len(r) > 34 else None,
                "max_mc_4h": r[35] if len(r) > 35 else None,
                "peak_mult_4h": r[36] if len(r) > 36 else None,
                "min_mc_4h": r[37] if len(r) > 37 else None,
                "min_dd_4h_pct": r[38] if len(r) > 38 else None,
                "pre_peak_low_mc_4h": r[39] if len(r) > 39 else None,
                "pre_peak_dd_4h_pct": r[40] if len(r) > 40 else None,
                "path_4h": r[41] if len(r) > 41 else None,
                "perf_checked_stage": r[42] if len(r) > 42 else 0,
                "metrics_data": r[43] if len(r) > 43 else None,
            }
            m_data = row_dict.get("metrics_data")
            if isinstance(m_data, str) and m_data.strip():
                try:
                    row_dict["metrics_data"] = json.loads(m_data)
                except Exception:
                    pass
            raw_msg = row_dict.get("formatted_message") or ""
            mig_status = str(row_dict.get("migration_status") or "")
            sig_type = str(row_dict.get("signal_type") or "").lower()
            mig_lower = mig_status.lower()
            if "takeover" in mig_lower or sig_type == "cto":
                cat_display = "CTO"
            elif "boost" in mig_lower or sig_type == "boost":
                cat_display = "BOOST"
            elif "token ad" in mig_lower or "ad (" in mig_lower:
                cat_display = "DEX AD"
            else:
                cat_display = "DEX PROFILE"

            pay_ts = row_dict.get("payment_timestamp")
            boost_amt = int(pay_ts) if (cat_display == "BOOST" and isinstance(pay_ts, (int, float)) and 0 < pay_ts < 1_000_000_000) else None

            c_age = extract_creation_age_str(row_dict.get("age"), raw_msg) or row_dict.get("age") or "-"
            m_age = extract_migration_age_str(None, raw_msg) or "-"
            cur_dex_match = re.search(r"Current DEX:\s*(.+)", raw_msg)
            current_dex = cur_dex_match.group(1).strip() if cur_dex_match else ""
            is_migrated = bool("migrated: yes" in raw_msg.lower() or "raydium" in current_dex.lower())

            event_ts = row_dict.get("timestamp") or row_dict.get("event_ts")
            if not event_ts:
                raw_ms = row_dict.get("fetched_at") or row_dict.get("published_at") or 0
                if isinstance(raw_ms, (int, float)) and raw_ms > 1_000_000_000_000:
                    event_ts = int(raw_ms / 1000)
            row_dict["timestamp"] = event_ts
            row_dict["token_name"] = row_dict.get("name") or "Unknown"
            row_dict["category_display"] = cat_display
            row_dict["boost_amount"] = boost_amt
            row_dict["creation_age"] = c_age
            row_dict["migration_age"] = m_age
            row_dict["age"] = c_age
            row_dict["current_dex"] = current_dex
            row_dict["is_migrated"] = is_migrated
            records.append(row_dict)

        total_pages = max(1, (total_count + limit - 1) // limit)
        with dex_validated_runtime_lock:
            ws_connected = len(dex_validated_active_ws_feeds)

        return jsonify({
            "success": True,
            "records": records,
            "stats": stats,
            "total": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "ws_connected": ws_connected,
        })
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/dex-validated/details', methods=['POST'])
def dex_validated_details():
    """Rebuild a complete Telegram preview for an older validated row."""
    data = request.json or {}
    ca = str(data.get('ca') or '').strip()
    category = str(data.get('signal_type') or 'dex').strip().lower()
    if not ca:
        return jsonify({'success': False, 'error': 'Contract address is required'}), 400

    order_type = {
        'boost': 'tokenBoost',
        'cto': 'communityTakeover',
        'dex': 'tokenProfile',
        'dex_profile': 'tokenProfile',
        'dex_ad': 'tokenAd',
    }.get(category, 'tokenProfile')
    item = {
        'tokenAddress': ca,
        'chainId': 'solana',
        'order_type': order_type,
        'order_status': str(data.get('order_status') or 'approved'),
        'payment_timestamp': data.get('payment_timestamp') or 0,
        'event_category': category if category in ('boost', 'cto') else 'dex',
        'storage_table': 'dex_validated',
    }
    settings = get_settings()
    workflow_id = settings.get('dex_validated_workflow_id', 'active_all')
    try:
        preview_coro = process_single_cto_item(item, '', workflow_id, True)
        if state.telethon_loop and state.telethon_loop.is_running():
            result = asyncio.run_coroutine_threadsafe(preview_coro, state.telethon_loop).result(timeout=30)
        else:
            result = asyncio.run(preview_coro)
        if not result:
            return jsonify({'success': False, 'error': 'Unable to rebuild signal details'}), 404
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
