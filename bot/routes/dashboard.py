"""Dashboard overview API."""

import json
from flask import Flask, render_template, request, jsonify

from bot.caches import (
    dex_validated_active_ws_feeds,
    dex_validated_runtime,
    dex_validated_runtime_lock,
)
from bot.db.connection import db_lock, get_db, get_settings, supabase_client
from bot.engine.rules import get_workflows
from bot.services.token_indicators import parse_mc_usd, synthesize_token_indicators
from bot.services.tokens import get_token_price_usd
from bot.web import app

@app.route('/api/dashboard/overview', methods=['GET'])
def get_dashboard_overview():
    """Unified analytics and live feed overview for the main Dashboard page."""
    try:
        settings = get_settings()
        workflows = get_workflows()
        active_wf_count = sum(1 for w in workflows if w.get('is_active'))
        total_wf_count = len(workflows)
        sol_price = get_token_price_usd('SOL') or 150.0

        with dex_validated_runtime_lock:
            ws_connected = len(dex_validated_active_ws_feeds)
            dv_runtime = dict(dex_validated_runtime)

        with db_lock:
            conn = get_db()
            conn.create_function('parse_mc_usd', 1, parse_mc_usd)
            cursor = conn.cursor()

            dv_where = (
                "WHERE name IS NOT NULL AND TRIM(name) != '' "
                "AND UPPER(TRIM(name)) NOT IN ('SOL', 'USDT', 'USDC', 'WSOL', 'WRAPPED SOL', 'UNKNOWN') "
                "AND ca IS NOT NULL AND TRIM(ca) != '' "
                "AND market_cap IS NOT NULL AND TRIM(market_cap) != '' AND UPPER(TRIM(market_cap)) != 'UNKNOWN' "
                "AND age IS NOT NULL AND TRIM(age) != '' AND UPPER(TRIM(age)) != 'UNKNOWN'"
            )
            dv_stats_row = cursor.execute(f"""
                SELECT
                    COUNT(*),
                    COUNT(DISTINCT ca),
                    COUNT(CASE WHEN LOWER(status) = 'forwarded' THEN 1 END),
                    COUNT(CASE WHEN LOWER(status) = 'detected' THEN 1 END),
                    COUNT(CASE WHEN LOWER(status) = 'dropped' THEN 1 END),
                    COUNT(CASE WHEN LOWER(signal_type) = 'cto' OR LOWER(migration_status) LIKE '%takeover%' THEN 1 END),
                    COUNT(CASE WHEN LOWER(signal_type) = 'boost' OR LOWER(migration_status) LIKE '%boost%' THEN 1 END),
                    COUNT(CASE WHEN LOWER(migration_status) LIKE '%profile%' THEN 1 END),
                    COUNT(CASE WHEN LOWER(migration_status) LIKE '%ad%' THEN 1 END),
                    COUNT(CASE WHEN LOWER(COALESCE(formatted_message, '')) LIKE '%migrated: yes%' OR LOWER(COALESCE(formatted_message, '')) LIKE '%raydium%' THEN 1 END),
                    COALESCE(SUM(COALESCE(volume_24h, 0)), 0),
                    COALESCE(AVG(NULLIF(liquidity_usd, 0)), 0),
                    COALESCE(AVG(NULLIF(parse_mc_usd(market_cap), 0)), 0)
                FROM dex_validated
                {dv_where}
            """).fetchone()

            dv_total = int(dv_stats_row[0] or 0) if dv_stats_row else 0
            dv_migrated = int(dv_stats_row[9] or 0) if dv_stats_row else 0
            dv_summary = {
                'total': dv_total,
                'unique_tokens': int(dv_stats_row[1] or 0) if dv_stats_row else 0,
                'forwarded': int(dv_stats_row[2] or 0) if dv_stats_row else 0,
                'detected': int(dv_stats_row[3] or 0) if dv_stats_row else 0,
                'dropped': int(dv_stats_row[4] or 0) if dv_stats_row else 0,
                'cto_count': int(dv_stats_row[5] or 0) if dv_stats_row else 0,
                'boost_count': int(dv_stats_row[6] or 0) if dv_stats_row else 0,
                'profile_count': int(dv_stats_row[7] or 0) if dv_stats_row else 0,
                'ad_count': int(dv_stats_row[8] or 0) if dv_stats_row else 0,
                'migrated_count': dv_migrated,
                'unmigrated_count': max(0, dv_total - dv_migrated),
                'total_volume_24h': round(float(dv_stats_row[10] or 0.0), 2) if dv_stats_row else 0.0,
                'avg_liquidity_usd': round(float(dv_stats_row[11] or 0.0), 2) if dv_stats_row else 0.0,
                'avg_mc_usd': round(float(dv_stats_row[12] or 0.0), 2) if dv_stats_row else 0.0,
            }

            dv_recent_rows = cursor.execute(f"""
                SELECT id, ca, name, platform, market_cap, age,
                       perf_5m, perf_1h, perf_6h, perf_24h,
                       liquidity_usd, volume_24h, status, dex_url,
                       migration_status, signal_type, payment_timestamp,
                       fetched_at, published_at, created_at, reason, formatted_message
                FROM dex_validated
                {dv_where}
                ORDER BY id DESC
                LIMIT 8
            """).fetchall()

            dv_recent = []
            for r in dv_recent_rows:
                rd = dict(r) if isinstance(r, dict) else {}
                mig_lower = str(rd.get('migration_status') or '').lower()
                sig_lower = str(rd.get('signal_type') or '').lower()
                if 'takeover' in mig_lower or sig_lower == 'cto':
                    cat_disp = 'CTO'
                elif 'boost' in mig_lower or sig_lower == 'boost':
                    cat_disp = 'BOOST'
                elif 'ad' in mig_lower:
                    cat_disp = 'DEX AD'
                else:
                    cat_disp = 'DEX PROFILE'
                raw_ms = rd.get('fetched_at') or rd.get('published_at') or 0
                ts_sec = int(raw_ms / 1000) if isinstance(raw_ms, (int, float)) and raw_ms > 1_000_000_000_000 else None
                dv_recent.append({
                    'id': rd.get('id'),
                    'ca': rd.get('ca'),
                    'token_name': rd.get('name') or 'Unknown',
                    'platform': rd.get('platform') or 'Solana (SOL)',
                    'category_display': cat_disp,
                    'market_cap': rd.get('market_cap') or '-',
                    'liquidity_usd': float(rd.get('liquidity_usd') or 0.0),
                    'volume_24h': float(rd.get('volume_24h') or 0.0),
                    'perf_5m': float(rd.get('perf_5m') or 0.0),
                    'perf_1h': float(rd.get('perf_1h') or 0.0),
                    'perf_24h': float(rd.get('perf_24h') or 0.0),
                    'age': rd.get('age') or '-',
                    'status': rd.get('status') or 'detected',
                    'reason': rd.get('reason') or '',
                    'dex_url': rd.get('dex_url') or f"https://dexscreener.com/solana/{rd.get('ca')}",
                    'timestamp': ts_sec,
                    'created_at': rd.get('created_at'),
                    'formatted_message': rd.get('formatted_message') or '',
                })

            dp_where = (
                "WHERE token_name IS NOT NULL AND TRIM(token_name) != '' "
                "AND UPPER(TRIM(token_name)) NOT IN ('SOL', 'USDT', 'USDC', 'WSOL', 'WRAPPED SOL', 'UNKNOWN') "
                "AND ca IS NOT NULL AND TRIM(ca) != '' "
                "AND market_cap IS NOT NULL AND TRIM(market_cap) != '' AND UPPER(TRIM(market_cap)) != 'UNKNOWN'"
            )
            dp_stats_row = cursor.execute(f"""
                SELECT
                    COUNT(*),
                    COUNT(DISTINCT ca),
                    COUNT(CASE WHEN LOWER(status) = 'forwarded' THEN 1 END),
                    COUNT(CASE WHEN LOWER(status) = 'dropped' THEN 1 END),
                    COALESCE(SUM(CASE WHEN UPPER(COALESCE(mint, 'SOL')) = 'SOL' THEN COALESCE(amount, 0) * ? ELSE COALESCE(amount, 0) END), 0),
                    COALESCE(SUM(COALESCE(volume_24h, 0)), 0),
                    COALESCE(AVG(NULLIF(liquidity_usd, 0)), 0),
                    COUNT(CASE WHEN (migration_age IS NOT NULL AND TRIM(migration_age) NOT IN ('', '0', '-')) OR LOWER(COALESCE(formatted_message, '')) LIKE '%migrated: yes%' THEN 1 END)
                FROM wallet_payments
                {dp_where}
            """, (sol_price,)).fetchone()

            dp_total = int(dp_stats_row[0] or 0) if dp_stats_row else 0
            dp_migrated = int(dp_stats_row[7] or 0) if dp_stats_row else 0
            dp_summary = {
                'total': dp_total,
                'unique_tokens': int(dp_stats_row[1] or 0) if dp_stats_row else 0,
                'forwarded': int(dp_stats_row[2] or 0) if dp_stats_row else 0,
                'dropped': int(dp_stats_row[3] or 0) if dp_stats_row else 0,
                'total_paid_usd': round(float(dp_stats_row[4] or 0.0), 2) if dp_stats_row else 0.0,
                'total_volume_24h': round(float(dp_stats_row[5] or 0.0), 2) if dp_stats_row else 0.0,
                'avg_liquidity_usd': round(float(dp_stats_row[6] or 0.0), 2) if dp_stats_row else 0.0,
                'migrated_count': dp_migrated,
                'unmigrated_count': max(0, dp_total - dp_migrated),
            }

            dp_recent_rows = cursor.execute(f"""
                SELECT id, ca, token_name, platform, market_cap, age, migration_age,
                       amount, mint, sender_address, timestamp,
                       perf_5m, perf_1h, perf_24h, liquidity_usd, volume_24h,
                       status, reason, dex_url, formatted_message, metrics_data
                FROM wallet_payments
                {dp_where}
                ORDER BY timestamp DESC, id DESC
                LIMIT 8
            """).fetchall()

            dp_recent = []
            for r in dp_recent_rows:
                rd = dict(r) if isinstance(r, dict) else {}
                amt = float(rd.get('amount') or 0.0)
                mint_str = str(rd.get('mint') or 'SOL').upper()
                amt_usd = round(amt * sol_price, 2) if mint_str == 'SOL' else round(amt, 2)
                raw_m = rd.get('metrics_data')
                m_obj = None
                if isinstance(raw_m, str) and raw_m.strip():
                    try:
                        m_obj = json.loads(raw_m)
                        if isinstance(m_obj, str) and m_obj.strip():
                            m_obj = json.loads(m_obj)
                    except Exception:
                        pass
                elif isinstance(raw_m, dict):
                    m_obj = raw_m
                if not isinstance(m_obj, dict) or not m_obj.get('indicators'):
                    m_obj = synthesize_token_indicators(rd)
                dp_recent.append({
                    'id': rd.get('id'),
                    'ca': rd.get('ca'),
                    'token_name': rd.get('token_name') or 'Unknown',
                    'platform': rd.get('platform') or 'Solana (SOL)',
                    'amount_usd': amt_usd,
                    'sender_address': rd.get('sender_address') or '',
                    'market_cap': rd.get('market_cap') or '-',
                    'liquidity_usd': float(rd.get('liquidity_usd') or 0.0),
                    'volume_24h': float(rd.get('volume_24h') or 0.0),
                    'perf_5m': float(rd.get('perf_5m') or 0.0),
                    'perf_1h': float(rd.get('perf_1h') or 0.0),
                    'perf_24h': float(rd.get('perf_24h') or 0.0),
                    'age': rd.get('age') or '-',
                    'status': rd.get('status') or 'detected',
                    'reason': rd.get('reason') or '',
                    'dex_url': rd.get('dex_url') or f"https://dexscreener.com/solana/{rd.get('ca')}",
                    'timestamp': rd.get('timestamp'),
                    'formatted_message': rd.get('formatted_message') or '',
                    'metrics_data': m_obj,
                    'indicators': m_obj.get('indicators') if isinstance(m_obj, dict) else None
                })

            # Top Movers across both pipelines (sorted by 1h / 5m performance & volume)
            top_movers_map = {}
            for item in dv_recent + dp_recent:
                ca_key = item.get('ca')
                if not ca_key:
                    continue
                score = float(item.get('perf_1h') or 0.0) * 1.2 + float(item.get('perf_5m') or 0.0)
                if ca_key not in top_movers_map or score > top_movers_map[ca_key]['_score']:
                    entry = dict(item)
                    entry['source'] = item.get('category_display') or 'DEX PAID'
                    entry['_score'] = score
                    top_movers_map[ca_key] = entry

            # Also pull top gainers from dex_validated and wallet_payments tables
            dv_top_rows = cursor.execute(f"""
                SELECT ca, name, market_cap, liquidity_usd, volume_24h, perf_5m, perf_1h, perf_24h,
                       dex_url, migration_status, signal_type, formatted_message
                FROM dex_validated
                {dv_where}
                ORDER BY COALESCE(perf_1h, 0) DESC, COALESCE(volume_24h, 0) DESC
                LIMIT 10
            """).fetchall()
            for r in dv_top_rows:
                rd = dict(r) if isinstance(r, dict) else {}
                ca_key = rd.get('ca')
                if not ca_key:
                    continue
                mig_lower = str(rd.get('migration_status') or '').lower()
                sig_lower = str(rd.get('signal_type') or '').lower()
                src = 'CTO' if ('takeover' in mig_lower or sig_lower == 'cto') else ('BOOST' if ('boost' in mig_lower or sig_lower == 'boost') else ('DEX AD' if 'ad' in mig_lower else 'DEX PROFILE'))
                score = float(rd.get('perf_1h') or 0.0) * 1.2 + float(rd.get('perf_5m') or 0.0)
                if ca_key not in top_movers_map or score > top_movers_map[ca_key]['_score']:
                    top_movers_map[ca_key] = {
                        'ca': ca_key,
                        'token_name': rd.get('name') or 'Unknown',
                        'market_cap': rd.get('market_cap') or '-',
                        'liquidity_usd': float(rd.get('liquidity_usd') or 0.0),
                        'volume_24h': float(rd.get('volume_24h') or 0.0),
                        'perf_5m': float(rd.get('perf_5m') or 0.0),
                        'perf_1h': float(rd.get('perf_1h') or 0.0),
                        'perf_24h': float(rd.get('perf_24h') or 0.0),
                        'dex_url': rd.get('dex_url') or f"https://dexscreener.com/solana/{ca_key}",
                        'source': src,
                        'formatted_message': rd.get('formatted_message') or '',
                        '_score': score,
                    }

            top_movers = sorted(top_movers_map.values(), key=lambda x: (x.get('perf_1h') or 0.0, x.get('volume_24h') or 0.0), reverse=True)[:6]
            for tm in top_movers:
                tm.pop('_score', None)

        return jsonify({
            'success': True,
            'system': {
                'ws_connected': ws_connected,
                'dex_validated_auto': str(settings.get('dex_validated_auto_scan', 'false')).lower() == 'true',
                'dex_paid_auto': str(settings.get('cto_auto_scan', 'false')).lower() == 'true',
                'wallet_poll_enabled': str(settings.get('wallet_poll_enabled', 'false')).lower() == 'true',
                'helius_configured': bool(settings.get('helius_api_key')),
                'supabase_connected': supabase_client is not None,
                'active_workflows': active_wf_count,
                'total_workflows': total_wf_count,
                'last_checked_at': dv_runtime.get('last_checked_at'),
                'sol_price': round(float(sol_price), 2),
            },
            'dex_validated': dv_summary,
            'dex_paid': dp_summary,
            'dv_recent': dv_recent,
            'dp_recent': dp_recent,
            'top_movers': top_movers,
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500
