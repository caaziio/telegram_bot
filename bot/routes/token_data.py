"""Token data / token payments API."""

import re
import json
import time
from flask import Flask, render_template, request, jsonify

from bot.caches import token_metadata_cache, token_price_cache
from bot.db.connection import db_lock, get_db
from bot.engine.rules import token_name_pattern_for_sql
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
    synthesize_token_indicators,
)
from bot.services.tokens import get_token_price_usd
from bot.web import app

@app.route('/api/wallet/token_payments', methods=['GET'])
def get_token_wallet_data():
    try:
        page = max(1, int(request.args.get('page', 1)))
        limit = min(max(10, int(request.args.get('limit', 25))), 50000)
        offset = (page - 1) * limit

        search = (request.args.get('search') or '').strip()
        status = (request.args.get('status') or 'all').strip().lower()
        platform = (request.args.get('platform') or 'all').strip().lower()
        exclude_platform = (request.args.get('exclude_platform') or 'none').strip().lower()
        timeframe = (request.args.get('timeframe') or 'all').strip().lower()
        date_from = request.args.get('date_from') or request.args.get('from_date')
        date_to = request.args.get('date_to') or request.args.get('to_date')
        min_amount = request.args.get('min_amount')
        max_amount = request.args.get('max_amount')
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
        workflow_id = request.args.get('workflow_id')
        enriched_only = request.args.get('enriched_only', 'false').lower() == 'true'
        sort_by = (request.args.get('sort_by') or 'id').lower()
        sort_order = 'ASC' if (request.args.get('sort_order') or 'desc').upper() == 'ASC' else 'DESC'

        sol_price = 0.0
        try:
            sol_mint = "So11111111111111111111111111111111111111112"
            if sol_mint in token_price_cache and token_price_cache[sol_mint][0] > 0:
                sol_price = float(token_price_cache[sol_mint][0])
            else:
                sol_price = float(get_token_price_usd("SOL") or 0.0)
        except Exception:
            pass
        if not sol_price or sol_price <= 0:
            sol_price = 145.0

        ts_wp_expr = "(CASE WHEN COALESCE(timestamp, 0) > 1000000000000 THEN timestamp / 1000 WHEN COALESCE(timestamp, 0) > 0 THEN timestamp ELSE CAST(COALESCE(strftime('%s', created_at), 0) AS INTEGER) END)"
        usd_calc_sql = f"(CASE WHEN mint IN ('SOL', 'So11111111111111111111111111111111111111112') AND amount < 20 THEN amount * {sol_price} ELSE amount END)"
        valid_sorts = {
            'id': 'id',
            'timestamp': ts_wp_expr,
            'amount': usd_calc_sql,
            'token_name': 'LOWER(COALESCE(token_name, ""))',
            'ca': 'LOWER(COALESCE(ca, ""))',
            'sender_address': 'LOWER(COALESCE(sender_address, ""))',
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
            'migration_age': 'parse_migration_age_minutes(migration_age, formatted_message)',
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

        where_clauses = []
        params = []

        if search:
            s_param = f"%{search}%"
            where_clauses.append("(token_name LIKE ? OR ca LIKE ? OR sender_address LIKE ? OR tracked_address LIKE ? OR reason LIKE ?)")
            params.extend([s_param, s_param, s_param, s_param, s_param])

        if status != 'all' and status:
            where_clauses.append("LOWER(status) = ?")
            params.append(status)

        if platform != 'all' and platform:
            where_clauses.append("LOWER(platform) LIKE ?")
            params.append(f"%{platform}%")

        if exclude_platform != 'none' and exclude_platform:
            where_clauses.append("LOWER(platform) NOT LIKE ?")
            params.append(f"%{exclude_platform}%")

        # The table is an enriched-token dataset, never a raw payment ledger.
        where_clauses.append("""(
            token_name IS NOT NULL AND TRIM(token_name) != ''
            AND UPPER(TRIM(token_name)) NOT IN ('SOL', 'USDT', 'USDC', 'WSOL', 'WRAPPED SOL', 'UNKNOWN')
            AND ca IS NOT NULL AND TRIM(ca) != ''
            AND market_cap IS NOT NULL AND TRIM(market_cap) != '' AND UPPER(TRIM(market_cap)) != 'UNKNOWN'
            AND age IS NOT NULL AND TRIM(age) != '' AND UPPER(TRIM(age)) != 'UNKNOWN'
            AND dex_url IS NOT NULL AND TRIM(dex_url) != ''
        )""")

        from_ts = parse_date_to_timestamp(date_from, is_end_of_day=False) if date_from else None
        to_ts = parse_date_to_timestamp(date_to, is_end_of_day=True) if date_to else None

        if from_ts is not None:
            where_clauses.append(f"{ts_wp_expr} >= ?")
            params.append(from_ts)
        elif timeframe and timeframe != 'all' and timeframe != 'custom':
            now_ts = int(time.time())
            tf_seconds = {
                '1h': 3600,
                '6h': 21600,
                '24h': 86400,
                '7d': 604800,
                '30d': 2592000
            }.get(timeframe)
            if tf_seconds:
                where_clauses.append(f"{ts_wp_expr} >= ?")
                params.append(now_ts - tf_seconds)

        if to_ts is not None:
            where_clauses.append(f"{ts_wp_expr} <= ?")
            params.append(to_ts)

        usd_amount_clause = "(CASE WHEN mint IN ('SOL', 'So11111111111111111111111111111111111111112') AND amount < 20 THEN amount * ? ELSE amount END)"
        if min_amount:
            try:
                where_clauses.append(f"{usd_amount_clause} >= ?")
                params.extend([sol_price, float(min_amount)])
            except ValueError:
                pass

        if max_amount:
            try:
                where_clauses.append(f"{usd_amount_clause} <= ?")
                params.extend([sol_price, float(max_amount)])
            except ValueError:
                pass

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

        volume_col = f"volume_{volume_tf}" if volume_tf in ['5m', '1h', '6h', '24h'] else 'volume_24h'
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

        # Performance filter
        perf_col_clean = f"perf_{perf_tf}" if perf_tf in ['5m', '1h', '6h', '24h'] else 'perf_5m'
        if min_perf:
            try:
                where_clauses.append(f"{perf_col_clean} >= ?")
                params.append(float(min_perf))
            except ValueError:
                pass

        if max_perf:
            try:
                where_clauses.append(f"{perf_col_clean} <= ?")
                params.append(float(max_perf))
            except ValueError:
                pass

        # Creation Age filter (in minutes)
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

        # Migration Age filter (in minutes)
        if min_migration_age:
            try:
                where_clauses.append("parse_migration_age_minutes(migration_age, formatted_message) >= ?")
                params.append(float(min_migration_age))
            except ValueError:
                pass

        if max_migration_age:
            try:
                where_clauses.append("parse_migration_age_minutes(migration_age, formatted_message) <= ?")
                params.append(float(max_migration_age))
            except ValueError:
                pass

        # Migrated filter
        if migrated == 'yes':
            where_clauses.append("(LOWER(migration_status) LIKE '%migrated%' OR LOWER(platform) LIKE '%raydium%' OR formatted_message LIKE '%Migrated: Yes%')")
        elif migrated == 'no':
            where_clauses.append("((LOWER(migration_status) LIKE '%not migrated%' OR formatted_message LIKE '%Migrated: No%') OR (LOWER(migration_status) NOT LIKE '%migrated%' AND LOWER(platform) NOT LIKE '%raydium%' AND (formatted_message NOT LIKE '%Migrated: Yes%' OR formatted_message IS NULL)))")

        # Token Name pattern / regex filter
        if token_name_pattern and token_name_pattern.strip() not in ('', '.*'):
            pat = token_name_pattern_for_sql(token_name_pattern)
            if token_name_mode == 'exclude':
                where_clauses.append("NOT (token_name REGEXP ?)")
            else:
                where_clauses.append("(token_name REGEXP ?)")
            params.append(pat)

        # Word regex filter (drops tokens when any searchable text matches)
        if word_filter:
            where_clauses.append("NOT ((COALESCE(token_name, '') || '\n' || COALESCE(reason, '') || '\n' || COALESCE(formatted_message, '')) REGEXP ?)")
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

            # Count total matching records
            count_query = f"SELECT COUNT(*) FROM wallet_payments{where_str}"
            total_count = cursor.execute(count_query, params).fetchone()[0]

            # Overall Stats (Volume in USD)
            usd_sum_clause = "COALESCE(SUM(CASE WHEN mint IN ('SOL', 'So11111111111111111111111111111111111111112') AND amount < 20 THEN amount * ? ELSE amount END), 0)"
            stats_query = f"""
                SELECT
                    COUNT(*),
                    COUNT(CASE WHEN LOWER(status) = 'forwarded' THEN 1 END),
                    {usd_sum_clause},
                    COUNT(DISTINCT ca)
                FROM wallet_payments{where_str}
            """
            stats_params = [sol_price] + list(params)
            stats_row = cursor.execute(stats_query, stats_params).fetchone()
            stats = {
                'total': stats_row[0] if stats_row else 0,
                'forwarded': stats_row[1] if stats_row else 0,
                'total_volume': round(stats_row[2], 2) if stats_row and stats_row[2] else 0.0,
                'unique_tokens': stats_row[3] if stats_row else 0,
                'sol_price': sol_price
            }

            # Fetch Paginated Records
            data_query = f"""
                SELECT id, tracked_address, sender_address, amount, mint, signature, timestamp,
                       token_name, ca, platform, market_cap, age, perf_5m, perf_1h, perf_6h, perf_24h,
                       liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                       dex_url, migration_status, status, reason, formatted_message, created_at, migration_age,
                       max_mc_1h, peak_mult_1h, min_mc_1h, min_dd_1h_pct, pre_peak_low_mc_1h, pre_peak_dd_1h_pct, path_1h,
                       max_mc_4h, peak_mult_4h, min_mc_4h, min_dd_4h_pct, pre_peak_low_mc_4h, pre_peak_dd_4h_pct, path_4h,
                       perf_checked_stage, metrics_data
                FROM wallet_payments
                {where_str}
                ORDER BY {order_col} {sort_order} NULLS LAST
                LIMIT ? OFFSET ?
            """
            fetch_params = list(params) + [limit, offset]
            rows = cursor.execute(data_query, fetch_params).fetchall()

            rows_list = [dict(r) if isinstance(r, dict) else {
                "id": r[0],
                "tracked_address": r[1],
                "sender_address": r[2],
                "amount": r[3],
                "mint": r[4],
                "signature": r[5],
                "timestamp": r[6],
                "token_name": r[7],
                "ca": r[8],
                "platform": r[9],
                "market_cap": r[10],
                "age": r[11],
                "perf_5m": r[12],
                "perf_1h": r[13],
                "perf_6h": r[14],
                "perf_24h": r[15],
                "liquidity_usd": r[16],
                "volume_5m": r[17],
                "volume_1h": r[18],
                "volume_6h": r[19],
                "volume_24h": r[20],
                "dex_url": r[21],
                "migration_status": r[22],
                "status": r[23],
                "reason": r[24],
                "formatted_message": r[25],
                "created_at": r[26],
                "migration_age": r[27] if len(r) > 27 else None,
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
            } for r in rows]

        USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

        results = []
        for r in rows_list:
            item_dict = dict(r)
            mint = item_dict.get('mint') or 'SOL'
            try:
                amount = float(item_dict.get('amount') or 0.0)
            except (ValueError, TypeError):
                amount = 0.0

            raw_msg = item_dict.get('formatted_message') or ''
            match_msg = re.search(r'DEX Payment:\s*\$?([\d,]+(?:\.\d+)?)\s*(?:USD)?', raw_msg, re.IGNORECASE)
            if match_msg:
                try:
                    amount_usd = float(match_msg.group(1).replace(',', ''))
                except ValueError:
                    amount_usd = amount if amount >= 20 else (amount * sol_price if mint in ('SOL', 'So11111111111111111111111111111111111111112') else amount)
            elif mint in (USDC_MINT, USDT_MINT, 'USDC', 'USDT') or amount >= 20:
                amount_usd = round(amount, 2)
            elif mint in ('SOL', 'So11111111111111111111111111111111111111112'):
                amount_usd = round(amount * sol_price, 2)
            else:
                cached = token_price_cache.get(mint)
                if cached and cached[0] > 0:
                    amount_usd = round(amount * cached[0], 2)
                else:
                    amount_usd = round(amount, 2)

            item_dict['amount_usd'] = round(amount_usd, 2)
            item_dict['currency'] = 'USD' if (mint in (USDC_MINT, USDT_MINT, 'USDC', 'USDT') or amount >= 20) else ('SOL' if mint in ('SOL', 'So11111111111111111111111111111111111111112') else 'TOKEN')

            c_age = extract_creation_age_str(item_dict.get('age'), item_dict.get('formatted_message')) or item_dict.get('age') or '-'
            m_age = extract_migration_age_str(item_dict.get('migration_age'), item_dict.get('formatted_message')) or item_dict.get('migration_age') or '-'
            item_dict['creation_age'] = c_age
            item_dict['migration_age'] = m_age
            item_dict['age'] = c_age

            # Fallback extraction from formatted_message if metrics/fields are null in DB
            if raw_msg:
                if item_dict.get('perf_5m') is None:
                    m5 = re.search(r'5m:\s*([+-]?\d+(?:\.\d+)?)%', raw_msg)
                    if m5:
                        try: item_dict['perf_5m'] = float(m5.group(1))
                        except ValueError: pass
                if item_dict.get('perf_1h') is None:
                    h1 = re.search(r'1h:\s*([+-]?\d+(?:\.\d+)?)%', raw_msg)
                    if h1:
                        try: item_dict['perf_1h'] = float(h1.group(1))
                        except ValueError: pass
                if item_dict.get('perf_6h') is None:
                    h6 = re.search(r'6h:\s*([+-]?\d+(?:\.\d+)?)%', raw_msg)
                    if h6:
                        try: item_dict['perf_6h'] = float(h6.group(1))
                        except ValueError: pass
                if item_dict.get('perf_24h') is None:
                    h24 = re.search(r'24h:\s*([+-]?\d+(?:\.\d+)?)%', raw_msg)
                    if h24:
                        try: item_dict['perf_24h'] = float(h24.group(1))
                        except ValueError: pass
                if not item_dict.get('ca'):
                    ca_match = re.search(r'CA:\s*([1-9A-HJ-NP-Za-km-z]{32,44})', raw_msg)
                    if ca_match:
                        item_dict['ca'] = ca_match.group(1)
                if (not item_dict.get('market_cap') or item_dict.get('market_cap') == '-') and raw_msg:
                    mc_match = re.search(r'Market Cap:\s*(\$[\d,]+(?:\.\d+)?[KMB]?)', raw_msg, re.IGNORECASE)
                    if mc_match:
                        item_dict['market_cap'] = mc_match.group(1)
                if (not item_dict.get('token_name') or item_dict.get('token_name') == 'SOL') and raw_msg:
                    name_match = re.search(r'PROJECT:\s*(.+?)\s*🚀', raw_msg, re.IGNORECASE)
                    if name_match:
                        item_dict['token_name'] = name_match.group(1).strip()

            m_data = item_dict.get('metrics_data')
            if isinstance(m_data, str) and m_data.strip():
                try:
                    m_data = json.loads(m_data)
                    if isinstance(m_data, str) and m_data.strip():
                        m_data = json.loads(m_data)
                except Exception:
                    m_data = None
            if not isinstance(m_data, dict) or not m_data.get('indicators'):
                m_data = synthesize_token_indicators(item_dict)

            item_dict['metrics_data'] = m_data
            item_dict['indicators'] = m_data.get('indicators') if isinstance(m_data, dict) else None

            if not item_dict.get('token_name'):
                item_dict['token_name'] = "SOL" if mint == "SOL" else (token_metadata_cache.get(mint) or (mint[:6] + '...' if len(mint) > 10 else mint))
            results.append(item_dict)

        total_pages = max(1, (total_count + limit - 1) // limit)
        return jsonify({
            "success": True,
            "payments": results,
            "total": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "stats": stats
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
