"""Workflow rule matching against historical tokens (Outcomes Lab)."""

import re
import json
from datetime import datetime, timezone
import time

from bot.config import debug_log
from bot.db.connection import db_lock, get_db, supabase_client
from bot.services.token_indicators import (
    indicator_preset_passes,
    get_token_indicator_value,
    parse_age_minutes,
    parse_creation_age_minutes,
    parse_date_to_timestamp,
    parse_mc_usd,
    parse_migration_age_minutes,
    synthesize_token_indicators,
)

def token_matches_workflow_rules(token, rules):
    """Check whether a candidate token dict satisfies all rules of a workflow."""
    if not rules:
        return True

    name = str(token.get('name') or token.get('token_name') or '').strip()
    mc = float(parse_mc_usd(token.get('market_cap')) or parse_mc_usd(token.get('entry_mc')) or 0.0)
    age_m = parse_creation_age_minutes(token.get('age'), token.get('formatted_message'))
    if age_m is None:
        age_m = parse_age_minutes(token.get('age'))
    mig_age_m = parse_migration_age_minutes(token.get('migration_age'), token.get('formatted_message'))
    liq = float(token.get('liquidity_usd') or 0.0)

    ca_str = str(token.get('ca') or token.get('token_address') or '').strip().lower()
    plat_str = f"{token.get('platform', '')} {token.get('dex_url', '')} {token.get('formatted_message', '')} {'pumpfun' if ca_str.endswith('pump') else ''}".lower()
    plat = plat_str

    migrated_status = str(token.get('migration_status') or '').strip().lower()
    raw_msg_lower = str(token.get('formatted_message') or '').lower()
    if migrated_status in ('not migrated', 'unmigrated', 'no', 'false', '0') or 'migrated: no' in raw_msg_lower or 'still on pump.fun' in raw_msg_lower:
        is_migrated = False
    elif migrated_status in ('migrated', 'yes', 'true', '1') or 'raydium' in plat_str or 'migrated: yes' in raw_msg_lower:
        is_migrated = True
    elif ca_str.endswith('pump'):
        is_migrated = False
    else:
        is_migrated = ('migrated' in migrated_status and 'not' not in migrated_status and 'un' not in migrated_status)

    sig_type = str(token.get('signal_type') or token.get('category') or token.get('reason') or '').lower()
    cat = 'boost' if 'boost' in sig_type else ('cto' if ('takeover' in sig_type or 'cto' in sig_type) else ('paid' if 'paid' in sig_type else 'dex'))

    for r in rules:
        rtype = r.get('rule_type')
        tmin = float(r['time_min']) if r.get('time_min') not in (None, '') else None
        tmax = float(r['time_max']) if r.get('time_max') not in (None, '') else None
        stext = r.get('search_text')
        rtext = r.get('replace_text')

        if rtype == 'market_cap':
            if tmin is not None and mc < tmin:
                return False
            if tmax is not None and mc > tmax:
                return False

        elif rtype == 'token_age':
            age_kind = (stext or 'creation').lower().strip()
            eval_age = mig_age_m if age_kind == 'migration' else age_m
            if eval_age is None:
                if tmin is not None and tmin > 0:
                    return False
            else:
                if tmin is not None and eval_age < tmin:
                    return False
                if tmax is not None and eval_age > tmax:
                    return False

        elif rtype == 'liquidity':
            if tmin is not None and liq < tmin:
                return False
            if tmax is not None and liq > tmax:
                return False

        elif rtype == 'volume':
            v_tf = (stext or '24h').lower().replace('hr', 'h')
            vol = float(token.get(f'volume_{v_tf}') or token.get('volume_24h') or 0.0)
            if tmin is not None and vol < tmin:
                return False
            if tmax is not None and vol > tmax:
                return False

        elif rtype == 'performance':
            p_tf = (stext or '5m').lower().replace('hr', 'h')
            perf = float(token.get(f'perf_{p_tf}') or token.get('perf_5m') or 0.0)
            if tmin is not None and perf < tmin:
                return False
            if tmax is not None and perf > tmax:
                return False

        elif rtype == 'migrated':
            req = str(stext or 'yes').lower().strip()
            if req in ('yes', 'migrated', 'true', '1') and not is_migrated:
                return False
            if req in ('no', 'unmigrated', 'false', '0') and is_migrated:
                return False

        elif rtype == 'platform':
            req_plat = str(stext or '').lower().strip()
            if req_plat and req_plat not in ('all', 'any'):
                clean_plat = re.sub(r'[^a-z0-9]', '', plat)
                clean_req = re.sub(r'[^a-z0-9]', '', req_plat)
                if clean_req not in clean_plat:
                    return False

        elif rtype == 'exclude_platform':
            ex = str(stext or '').lower().strip()
            if ex and ex not in ('all', 'any'):
                clean_plat = re.sub(r'[^a-z0-9]', '', plat)
                clean_ex = re.sub(r'[^a-z0-9]', '', ex)
                if clean_ex in clean_plat:
                    return False

        elif rtype == 'category':
            req_cat = str(stext or '').lower().strip()
            if req_cat and req_cat not in ('all', 'any'):
                cat_corpus = f"{cat} {token.get('signal_type', '')} {token.get('migration_status', '')} {token.get('reason', '')} {token.get('formatted_message', '')} {token.get('tracked_address', '')} {token.get('source_system', '')}".lower()
                if req_cat in ('dex_paid', 'paid'):
                    if 'paid' not in cat_corpus and 'dex' not in cat_corpus:
                        return False
                elif req_cat == 'cto':
                    if 'cto' not in cat_corpus and 'takeover' not in cat_corpus:
                        return False
                elif req_cat == 'boost':
                    if 'boost' not in cat_corpus:
                        return False
                elif req_cat == 'profile':
                    if 'profile' not in cat_corpus:
                        return False
                elif req_cat == 'ad':
                    if 'ad' not in cat_corpus and 'campaign' not in cat_corpus:
                        return False
                elif req_cat not in cat_corpus:
                    return False

        elif rtype == 'token_name':
            pattern = (stext or '').strip()
            mode = (rtext or 'include').strip().lower()
            structured_filter = None
            if pattern.startswith('token_name_filter:'):
                try:
                    structured_filter = json.loads(pattern[len('token_name_filter:'):])
                except Exception:
                    structured_filter = {'exact': [], 'contains': []}

            if structured_filter is not None:
                norm_name = name.casefold().strip()
                exacts = [str(x).strip('"').casefold() for x in structured_filter.get('exact', []) if str(x).strip('"')]
                contains = [(str(x).strip('"').casefold(), str(x).startswith('"') and str(x).endswith('"')) for x in structured_filter.get('contains', []) if str(x).strip('"')]
                c_match = any(lit in norm_name if q else bool(re.search(rf'(?<!\w){re.escape(lit)}(?!\w)', norm_name, re.IGNORECASE)) for lit, q in contains)
                is_match = (norm_name in exacts) or c_match
            else:
                terms = [re.escape(t.strip()) for t in re.split(r'[,|]+', pattern) if t.strip()]
                if terms:
                    combined_regex = r'(?:' + '|'.join(terms) + r')'
                    is_match = bool(re.search(combined_regex, name, re.IGNORECASE))
                else:
                    is_match = True

            if mode == 'exclude' and is_match and name:
                return False
            if mode != 'exclude' and not is_match:
                return False

        elif rtype == 'filter':
            # Word / Regex message filter
            mode = (rtext or 'include').strip().lower()
            corpus = f"{name} {token.get('symbol', '')} {token.get('ca', '')} {token.get('formatted_message', '')}".lower()
            if stext and str(stext).strip():
                terms = [t.strip() for t in re.split(r'[,|]+', str(stext).strip()) if t.strip()]
                if terms:
                    escaped_terms = [re.escape(t) for t in terms]
                    try:
                        m = bool(re.search(r'(?:' + '|'.join(escaped_terms) + r')', corpus, re.IGNORECASE))
                    except re.error:
                        m = any(t.lower() in corpus for t in terms)
                else:
                    m = True
                if mode == 'exclude' and m:
                    return False
                if mode != 'exclude' and not m:
                    return False

        elif rtype == 'extract_ca':
            req_ca = str(stext or 'yes').lower().strip()
            ca_val = str(token.get('ca') or token.get('token_address') or '').strip()
            if req_ca in ('yes', 'true', '1') and not ca_val:
                return False

        elif rtype == 'signal_type':
            req_sig = str(stext or 'both').lower().strip()
            src_sys = str(token.get('source_system') or 'validated').lower().strip()
            if req_sig in ('dex_paid', 'paid') and src_sys != 'paid':
                return False
            if req_sig == 'validated' and src_sys == 'paid':
                return False

        elif rtype == 'dex_payment':
            if token.get('source_system') == 'paid':
                amt = float(token.get('amount') or 0.0)
                usd_amt = (amt * 180.0) if (amt > 0 and amt < 25.0) else amt
                if tmin is not None and usd_amt < tmin:
                    return False
                if tmax is not None and usd_amt > tmax:
                    return False
                if stext and str(stext).strip():
                    corpus = f"{token.get('signal_type', '')} {token.get('reason', '')} {token.get('tracked_address', '')} {token.get('formatted_message', '')}".lower()
                    stext_clean = str(stext).lower().strip()
                    if stext_clean not in corpus:
                        s_norm = re.sub(r'[\s_]+', '', stext_clean)
                        c_norm = re.sub(r'[\s_]+', '', corpus)
                        if s_norm not in c_norm:
                            return False

        elif rtype == 'indicator':
            # Akoueflow indicator filter
            indicator_key = (stext or 'net_buy_pressure').strip().lower()
            val = get_token_indicator_value(token, indicator_key)
            if val is None:
                if tmin is not None or tmax is not None:
                    return False
            else:
                if tmin is not None and val < tmin:
                    return False
                if tmax is not None and val > tmax:
                    return False

        elif rtype == 'indicator_preset':
            if not indicator_preset_passes(stext, token):
                return False

    return True


_candidate_tokens_cache = {}
_CANDIDATE_CACHE_TTL = 45  # seconds cache for instant Outcome Lab filter responses

def get_historical_candidate_tokens(system='validated', timeframe='day', date_from=None, date_to=None):
    """Fetch candidate historical signals from memory SQLite index or Supabase fallback."""
    from_ts = parse_date_to_timestamp(date_from, is_end_of_day=False) if date_from else None
    to_ts = parse_date_to_timestamp(date_to, is_end_of_day=True) if date_to else None

    cache_key = (str(system).lower().strip(), str(timeframe).lower().strip(), str(from_ts), str(to_ts))
    cached = _candidate_tokens_cache.get(cache_key)
    if cached and (time.time() - cached[0] < _CANDIDATE_CACHE_TTL):
        return [dict(t) for t in cached[1]]

    now_ts = int(time.time())
    if timeframe == 'custom' or from_ts is not None or to_ts is not None:
        tf_seconds = None
    else:
        tf_seconds = {
            'day': 86400,
            'week': 7 * 86400,
            'month': 30 * 86400,
            'all': None
        }.get(timeframe, 86400)

    candidate_tokens = []
    ts_dv_expr = "(CASE WHEN COALESCE(fetched_at, 0) > 1000000000000 THEN fetched_at / 1000 WHEN COALESCE(published_at, 0) > 1000000000000 THEN published_at / 1000 WHEN COALESCE(payment_timestamp, 0) > 1000000000000 THEN payment_timestamp / 1000 ELSE CAST(COALESCE(strftime('%s', created_at), 0) AS INTEGER) END)"
    ts_wp_expr = "(CASE WHEN COALESCE(timestamp, 0) > 1000000000000 THEN timestamp / 1000 WHEN COALESCE(timestamp, 0) > 0 THEN timestamp ELSE CAST(COALESCE(strftime('%s', created_at), 0) AS INTEGER) END)"

    with db_lock:
        conn = get_db()
        cursor = conn.cursor()

        if system in ('validated', 'all'):
            where_dv = [
                "name IS NOT NULL AND TRIM(name) != ''",
                "UPPER(TRIM(name)) NOT IN ('SOL','USDT','USDC','WSOL','WRAPPED SOL','UNKNOWN')",
                "market_cap IS NOT NULL AND TRIM(market_cap) != '' AND UPPER(TRIM(market_cap)) != 'UNKNOWN'",
                "peak_mult_1h IS NOT NULL"
            ]
            params_dv = []
            if from_ts is not None:
                where_dv.append(f"{ts_dv_expr} >= ?")
                params_dv.append(from_ts)
            elif tf_seconds:
                where_dv.append(f"{ts_dv_expr} >= ?")
                params_dv.append(now_ts - tf_seconds)

            if to_ts is not None:
                where_dv.append(f"{ts_dv_expr} <= ?")
                params_dv.append(to_ts)
            dv_query = f"""
                SELECT id, name, ca, platform, market_cap, age, perf_5m, perf_1h, perf_6h, perf_24h,
                       liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h, migration_status,
                       signal_type, formatted_message, max_mc_1h, peak_mult_1h, min_mc_1h, min_dd_1h_pct,
                       pre_peak_low_mc_1h, pre_peak_dd_1h_pct, path_1h, max_mc_4h, peak_mult_4h, min_mc_4h,
                       min_dd_4h_pct, pre_peak_low_mc_4h, pre_peak_dd_4h_pct, path_4h, {ts_dv_expr} AS event_ts,
                       metrics_data
                FROM dex_validated
                WHERE {' AND '.join(where_dv)}
                ORDER BY id DESC
            """
            try:
                for row in cursor.execute(dv_query, params_dv).fetchall():
                    d = dict(row) if isinstance(row, dict) else {
                        'id': row[0], 'name': row[1], 'ca': row[2], 'platform': row[3], 'market_cap': row[4],
                        'age': row[5], 'perf_5m': row[6], 'perf_1h': row[7], 'perf_6h': row[8], 'perf_24h': row[9],
                        'liquidity_usd': row[10], 'volume_5m': row[11], 'volume_1h': row[12], 'volume_6h': row[13],
                        'volume_24h': row[14], 'migration_status': row[15], 'signal_type': row[16],
                        'formatted_message': row[17], 'max_mc_1h': row[18], 'peak_mult_1h': row[19],
                        'min_mc_1h': row[20], 'min_dd_1h_pct': row[21], 'pre_peak_low_mc_1h': row[22],
                        'pre_peak_dd_1h_pct': row[23], 'path_1h': row[24], 'max_mc_4h': row[25],
                        'peak_mult_4h': row[26], 'min_mc_4h': row[27], 'min_dd_4h_pct': row[28],
                        'pre_peak_low_mc_4h': row[29], 'pre_peak_dd_4h_pct': row[30], 'path_4h': row[31],
                        'event_ts': row[32], 'metrics_data': row[33] if len(row) > 33 else None,
                        'source_system': 'validated'
                    }
                    candidate_tokens.append(d)
            except Exception as e:
                debug_log(f"[WF PERF] Error querying dex_validated: {e}")

        if system in ('paid', 'all'):
            where_wp = [
                "token_name IS NOT NULL AND TRIM(token_name) != ''",
                "UPPER(TRIM(token_name)) NOT IN ('SOL','USDT','USDC','WSOL','WRAPPED SOL','UNKNOWN')",
                "market_cap IS NOT NULL AND TRIM(market_cap) != '' AND UPPER(TRIM(market_cap)) != 'UNKNOWN'",
                "peak_mult_1h IS NOT NULL"
            ]
            params_wp = []
            if from_ts is not None:
                where_wp.append(f"{ts_wp_expr} >= ?")
                params_wp.append(from_ts)
            elif tf_seconds:
                where_wp.append(f"{ts_wp_expr} >= ?")
                params_wp.append(now_ts - tf_seconds)

            if to_ts is not None:
                where_wp.append(f"{ts_wp_expr} <= ?")
                params_wp.append(to_ts)
            wp_query = f"""
                SELECT id, token_name AS name, ca, platform, market_cap, age, perf_5m, perf_1h, perf_6h, perf_24h,
                       liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h, migration_status,
                       COALESCE(reason, 'paid') AS signal_type, formatted_message, max_mc_1h, peak_mult_1h, min_mc_1h, min_dd_1h_pct,
                       pre_peak_low_mc_1h, pre_peak_dd_1h_pct, path_1h, max_mc_4h, peak_mult_4h, min_mc_4h,
                       min_dd_4h_pct, pre_peak_low_mc_4h, pre_peak_dd_4h_pct, path_4h, {ts_wp_expr} AS event_ts,
                       amount, reason, dex_url, tracked_address, metrics_data
                FROM wallet_payments
                WHERE {' AND '.join(where_wp)}
                ORDER BY id DESC
            """
            try:
                for row in cursor.execute(wp_query, params_wp).fetchall():
                    d = dict(row) if isinstance(row, dict) else {
                        'id': row[0], 'name': row[1], 'ca': row[2], 'platform': row[3], 'market_cap': row[4],
                        'age': row[5], 'perf_5m': row[6], 'perf_1h': row[7], 'perf_6h': row[8], 'perf_24h': row[9],
                        'liquidity_usd': row[10], 'volume_5m': row[11], 'volume_1h': row[12], 'volume_6h': row[13],
                        'volume_24h': row[14], 'migration_status': row[15], 'signal_type': row[16],
                        'formatted_message': row[17], 'max_mc_1h': row[18], 'peak_mult_1h': row[19],
                        'min_mc_1h': row[20], 'min_dd_1h_pct': row[21], 'pre_peak_low_mc_1h': row[22],
                        'pre_peak_dd_1h_pct': row[23], 'path_1h': row[24], 'max_mc_4h': row[25],
                        'peak_mult_4h': row[26], 'min_mc_4h': row[27], 'min_dd_4h_pct': row[28],
                        'pre_peak_low_mc_4h': row[29], 'pre_peak_dd_4h_pct': row[30], 'path_4h': row[31],
                        'event_ts': row[32], 'amount': row[33], 'reason': row[34], 'dex_url': row[35],
                        'tracked_address': row[36], 'metrics_data': row[37] if len(row) > 37 else None,
                        'source_system': 'paid'
                    }
                    if d.get('liquidity_usd') is None or d.get('volume_24h') is None:
                        msg = str(d.get('formatted_message') or '')
                        if d.get('liquidity_usd') is None:
                            m_liq = re.search(r'Liquidity:\s*\$?([\d,.]+)([KMB]?)', msg, re.I)
                            if m_liq:
                                val = float(m_liq.group(1).replace(',', '')) * {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(m_liq.group(2).upper(), 1)
                                d['liquidity_usd'] = val
                        if d.get('volume_24h') is None:
                            m_vol = re.search(r'24h Volume:\s*\$?([\d,.]+)([KMB]?)', msg, re.I)
                            if m_vol:
                                val = float(m_vol.group(1).replace(',', '')) * {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(m_vol.group(2).upper(), 1)
                                d['volume_24h'] = val
                    candidate_tokens.append(d)
            except Exception as e:
                debug_log(f"[WF PERF] Error querying wallet_payments: {e}")

    # Fallback to Supabase if in-memory index was empty
    if not candidate_tokens and supabase_client:
        try:
            if system in ('validated', 'all'):
                q_sb = supabase_client.table('dex_validated').select(
                    'id,name,ca,platform,market_cap,age,perf_5m,perf_1h,perf_6h,perf_24h,liquidity_usd,volume_5m,volume_1h,volume_6h,volume_24h,migration_status,signal_type,formatted_message,max_mc_1h,peak_mult_1h,min_mc_1h,min_dd_1h_pct,pre_peak_low_mc_1h,pre_peak_dd_1h_pct,path_1h,max_mc_4h,peak_mult_4h,min_mc_4h,min_dd_4h_pct,pre_peak_low_mc_4h,pre_peak_dd_4h_pct,path_4h,fetched_at,published_at,payment_timestamp,created_at,metrics_data'
                ).not_.is_('peak_mult_1h', 'null').limit(1000)
                if from_ts is not None:
                    q_sb = q_sb.gte('fetched_at', from_ts * 1000)
                elif tf_seconds:
                    q_sb = q_sb.gte('fetched_at', (now_ts - tf_seconds) * 1000)
                if to_ts is not None:
                    q_sb = q_sb.lte('fetched_at', to_ts * 1000)
                for item in (q_sb.execute().data or []):
                    item['source_system'] = 'validated'
                    ts = item.get('fetched_at') or item.get('published_at') or item.get('payment_timestamp') or 0
                    if ts > 1000000000000:
                        ev_ts = int(ts / 1000)
                    elif ts > 0:
                        ev_ts = int(ts)
                    elif item.get('created_at'):
                        try:
                            dt = datetime.fromisoformat(str(item['created_at']).replace('Z', '+00:00'))
                            ev_ts = int(dt.timestamp())
                        except Exception:
                            ev_ts = None
                    else:
                        ev_ts = None
                    item['event_ts'] = ev_ts
                    candidate_tokens.append(item)

            if system in ('paid', 'all'):
                q_sb_wp = supabase_client.table('wallet_payments').select(
                    'id,token_name,ca,platform,market_cap,age,perf_5m,perf_1h,perf_6h,perf_24h,liquidity_usd,volume_5m,volume_1h,volume_6h,volume_24h,migration_status,formatted_message,max_mc_1h,peak_mult_1h,min_mc_1h,min_dd_1h_pct,pre_peak_low_mc_1h,pre_peak_dd_1h_pct,path_1h,max_mc_4h,peak_mult_4h,min_mc_4h,min_dd_4h_pct,pre_peak_low_mc_4h,pre_peak_dd_4h_pct,path_4h,timestamp,created_at,amount,reason,dex_url,tracked_address,metrics_data'
                ).not_.is_('peak_mult_1h', 'null').limit(1000)
                if from_ts is not None:
                    q_sb_wp = q_sb_wp.gte('timestamp', from_ts)
                elif tf_seconds:
                    q_sb_wp = q_sb_wp.gte('timestamp', now_ts - tf_seconds)
                if to_ts is not None:
                    q_sb_wp = q_sb_wp.lte('timestamp', to_ts)
                for item in (q_sb_wp.execute().data or []):
                    item['name'] = item.get('token_name')
                    item['signal_type'] = item.get('reason') or 'paid'
                    item['source_system'] = 'paid'
                    ts = item.get('timestamp') or 0
                    if ts > 1000000000000:
                        ev_ts = int(ts / 1000)
                    elif ts > 0:
                        ev_ts = int(ts)
                    elif item.get('created_at'):
                        try:
                            dt = datetime.fromisoformat(str(item['created_at']).replace('Z', '+00:00'))
                            ev_ts = int(dt.timestamp())
                        except Exception:
                            ev_ts = None
                    else:
                        ev_ts = None
                    item['event_ts'] = ev_ts
                    if item.get('liquidity_usd') is None or item.get('volume_24h') is None:
                        msg = str(item.get('formatted_message') or '')
                        if item.get('liquidity_usd') is None:
                            m_liq = re.search(r'Liquidity:\s*\$?([\d,.]+)([KMB]?)', msg, re.I)
                            if m_liq:
                                val = float(m_liq.group(1).replace(',', '')) * {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(m_liq.group(2).upper(), 1)
                                item['liquidity_usd'] = val
                        if item.get('volume_24h') is None:
                            m_vol = re.search(r'24h Volume:\s*\$?([\d,.]+)([KMB]?)', msg, re.I)
                            if m_vol:
                                val = float(m_vol.group(1).replace(',', '')) * {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(m_vol.group(2).upper(), 1)
                                item['volume_24h'] = val
                    candidate_tokens.append(item)
        except Exception as sb_err:
            debug_log(f"[WF PERF] Supabase fallback error: {sb_err}")

    if candidate_tokens:
        for t in candidate_tokens:
            if not t.get('event_ts'):
                ts = t.get('timestamp') or t.get('fetched_at') or t.get('published_at') or t.get('payment_timestamp') or 0
                if ts > 1000000000000:
                    t['event_ts'] = int(ts / 1000)
                elif ts > 0:
                    t['event_ts'] = int(ts)
                elif t.get('created_at'):
                    try:
                        dt = datetime.fromisoformat(str(t['created_at']).replace('Z', '+00:00'))
                        t['event_ts'] = int(dt.timestamp())
                    except Exception:
                        pass
                if not t.get('event_ts') and t.get('formatted_message'):
                    m_ord = re.search(r'Order Placed:\s*([\d-]+ [\d:]+)', str(t['formatted_message']))
                    if m_ord:
                        try:
                            dt = datetime.strptime(m_ord.group(1), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                            t['event_ts'] = int(dt.timestamp())
                        except Exception:
                            pass
            m = t.get('metrics_data')
            if isinstance(m, str) and m.strip():
                try:
                    m = json.loads(m)
                    if isinstance(m, str) and m.strip():
                        m = json.loads(m)
                except Exception:
                    m = None

            is_valid_real_metrics = (
                isinstance(m, dict)
                and bool(m.get('indicators'))
                and m.get('top10_concentration_pct') not in (25.0, 25)
                and (m.get('indicators') or {}).get('holder_concentration') not in (25.0, 25)
            )

            if is_valid_real_metrics:
                t['metrics_data'] = m
                t['indicators'] = m['indicators']
            else:
                syn = synthesize_token_indicators(t)
                if syn and isinstance(syn, dict):
                    t['metrics_data'] = syn
                    t['indicators'] = syn.get('indicators')
        if candidate_tokens and (from_ts is not None or to_ts is not None):
            candidate_tokens = [
                t for t in candidate_tokens
                if (from_ts is None or (t.get('event_ts') is not None and t.get('event_ts') >= from_ts))
                and (to_ts is None or (t.get('event_ts') is not None and t.get('event_ts') <= to_ts))
            ]
        _candidate_tokens_cache[cache_key] = (time.time(), candidate_tokens)

    return candidate_tokens
