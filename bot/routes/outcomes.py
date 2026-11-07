"""Outcomes Lab API."""

import re
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify

from bot.config import debug_log
from bot.db.connection import supabase_client
from bot.services.outcomes import get_historical_candidate_tokens, token_matches_workflow_rules
from bot.services.token_indicators import get_token_indicator_value, parse_mc_usd
from bot.web import app

@app.route('/api/outcomes/workflows', methods=['GET'])
def get_outcomes_workflows():
    """Return all workflows with their rules for the Outcomes Strategy Lab preset loader."""
    try:
        wf_rows = []
        rule_rows = []
        if supabase_client:
            try:
                wf_rows = supabase_client.table('workflows').select('id,name,is_active,source_channel,target_channel').order('name').execute().data or []
                rule_rows = supabase_client.table('rules').select('id,workflow_id,rule_type,search_text,replace_text,time_min,time_max').execute().data or []
            except Exception as e:
                debug_log(f"[OUTCOMES] Error fetching workflows from Supabase: {e}")

        wf_map = {}
        for w in wf_rows:
            wf_map[w['id']] = {
                'id': w['id'],
                'name': w['name'],
                'is_active': bool(w.get('is_active', True)),
                'source_channel': w.get('source_channel') or '',
                'rules': [],
                'parsed_filters': {}
            }

        for r in rule_rows:
            wid = r.get('workflow_id')
            if wid in wf_map:
                wf_map[wid]['rules'].append(r)

        for wid, wf_data in wf_map.items():
            sig_rule = next((r for r in wf_data['rules'] if r.get('rule_type') == 'signal_type'), None)
            if sig_rule:
                st = str(sig_rule.get('search_text') or 'both').lower()
                wf_data['signal_type'] = st
                wf_data['system'] = 'paid' if st in ('dex_paid', 'paid') else ('validated' if st == 'validated' else 'all')
            else:
                src = str(wf_data.get('source_channel') or '').lower()
                system_hint = 'paid' if any(k in src for k in ('paid', 'wallet', 'helius')) else 'validated'
                wf_data['signal_type'] = 'both'
                wf_data['system'] = system_hint

            pf = {
                'min_mc': '',
                'max_mc': '',
                'min_age': '',
                'max_age': '',
                'age_kind': 'creation',
                'min_liq': '',
                'max_liq': '',
                'volume_tf': '24h',
                'min_vol': '',
                'max_vol': '',
                'perf_tf': '5m',
                'min_perf': '',
                'max_perf': '',
                'platform': 'all',
                'exclude_platform': '',
                'migrated': 'all',
                'category': 'all',
                'dex_payment_min': '',
                'dex_payment_max': '',
                'dex_payment_label': '',
                'name_mode': 'include',
                'name_filter': '',
                'word_mode': 'include',
                'word_filter': '',
                'extract_ca': 'all'
            }
            for r in wf_data['rules']:
                rtype = r.get('rule_type')
                stext = r.get('search_text') or ''
                rtext = r.get('replace_text') or ''
                tmin = r.get('time_min')
                tmax = r.get('time_max')

                if rtype == 'market_cap':
                    pf['min_mc'] = tmin if tmin is not None else ''
                    pf['max_mc'] = tmax if tmax is not None else ''
                elif rtype == 'token_age':
                    pf['min_age'] = tmin if tmin is not None else ''
                    pf['max_age'] = tmax if tmax is not None else ''
                    pf['age_kind'] = 'migration' if 'migration' in str(stext).lower() else 'creation'
                elif rtype == 'liquidity':
                    pf['min_liq'] = tmin if tmin is not None else ''
                    pf['max_liq'] = tmax if tmax is not None else ''
                elif rtype == 'volume':
                    pf['min_vol'] = tmin if tmin is not None else ''
                    pf['max_vol'] = tmax if tmax is not None else ''
                    if stext:
                        pf['volume_tf'] = str(stext).lower().replace('hr', 'h')
                elif rtype == 'performance':
                    pf['min_perf'] = tmin if tmin is not None else ''
                    pf['max_perf'] = tmax if tmax is not None else ''
                    if stext:
                        pf['perf_tf'] = str(stext).lower().replace('hr', 'h')
                elif rtype == 'platform':
                    pf['platform'] = str(stext).lower().strip() or 'all'
                elif rtype == 'exclude_platform':
                    pf['exclude_platform'] = str(stext).strip()
                elif rtype == 'migrated':
                    s = str(stext).lower().strip()
                    pf['migrated'] = 'migrated' if s in ('yes', 'migrated', 'true', '1') else ('unmigrated' if s in ('no', 'unmigrated', 'false', '0') else 'all')
                elif rtype == 'category':
                    pf['category'] = str(stext).lower().strip() or 'all'
                elif rtype == 'dex_payment':
                    pf['dex_payment_min'] = tmin if tmin is not None else ''
                    pf['dex_payment_max'] = tmax if tmax is not None else ''
                    pf['dex_payment_label'] = str(stext).strip()
                elif rtype == 'token_name':
                    pf['name_mode'] = str(rtext).lower().strip() if rtext in ('include', 'exclude') else 'include'
                    pf['name_filter'] = str(stext).strip()
                elif rtype == 'filter':
                    pf['word_mode'] = str(rtext).lower().strip() if rtext in ('include', 'exclude') else 'include'
                    pf['word_filter'] = str(stext).strip()
                elif rtype == 'extract_ca':
                    pf['extract_ca'] = 'yes'
            wf_data['parsed_filters'] = pf

        result_list = list(wf_map.values())
        result_list.sort(key=lambda x: x['name'].lower())
        return jsonify({'success': True, 'workflows': result_list})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def parse_filter_minutes(val):
    if val is None or str(val).strip() == '':
        return None
    s = str(val).lower().strip()
    try:
        return float(s)
    except ValueError:
        pass
    d = int(re.search(r'(\d+)\s*(?:d|day)', s).group(1)) if re.search(r'(\d+)\s*(?:d|day)', s) else 0
    h = int(re.search(r'(\d+)\s*(?:h|hr|hour)', s).group(1)) if re.search(r'(\d+)\s*(?:h|hr|hour)', s) else 0
    m = int(re.search(r'(\d+)\s*(?:m|min|minute)', s).group(1)) if re.search(r'(\d+)\s*(?:m|min|minute)', s) else 0
    total = (d * 1440) + (h * 60) + m
    return float(total) if (d or h or m or '0' in s) else None


def safe_float_filter(val):
    if val in (None, ''):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    clean = re.sub(r'[\$,%]', '', str(val)).strip()
    try:
        return float(clean)
    except Exception:
        return None


def build_rules_from_custom_filters(custom_filters):
    """Convert Outcomes Lab custom filters into workflow rules.

    Shared by the simulation and 'Save as Workflow' so a saved workflow
    applies exactly the rules that were backtested.
    """
    rules = []
    if not custom_filters:
        return rules
    # Market Cap ($)
    min_mc = safe_float_filter(custom_filters.get('min_mc') if custom_filters.get('min_mc') is not None else custom_filters.get('min_market_cap'))
    max_mc = safe_float_filter(custom_filters.get('max_mc') if custom_filters.get('max_mc') is not None else custom_filters.get('max_market_cap'))
    if min_mc is not None or max_mc is not None:
        rules.append({
            'rule_type': 'market_cap',
            'time_min': min_mc,
            'time_max': max_mc
        })

    # Token Age (Minutes)
    min_age_raw = custom_filters.get('min_age')
    max_age_raw = custom_filters.get('max_age')
    parsed_min_age = parse_filter_minutes(min_age_raw)
    parsed_max_age = parse_filter_minutes(max_age_raw)
    age_kind = (custom_filters.get('age_kind') or 'creation').lower().strip()
    if parsed_min_age is not None or parsed_max_age is not None:
        rules.append({
            'rule_type': 'token_age',
            'time_min': parsed_min_age,
            'time_max': parsed_max_age,
            'search_text': age_kind
        })

    # Liquidity ($)
    min_liq = safe_float_filter(custom_filters.get('min_liq') if custom_filters.get('min_liq') is not None else custom_filters.get('min_liquidity'))
    max_liq = safe_float_filter(custom_filters.get('max_liq') if custom_filters.get('max_liq') is not None else custom_filters.get('max_liquidity'))
    if min_liq is not None or max_liq is not None:
        rules.append({
            'rule_type': 'liquidity',
            'time_min': min_liq,
            'time_max': max_liq
        })

    # Volume ($)
    min_vol = safe_float_filter(custom_filters.get('min_vol') if custom_filters.get('min_vol') is not None else custom_filters.get('min_volume'))
    max_vol = safe_float_filter(custom_filters.get('max_vol') if custom_filters.get('max_vol') is not None else custom_filters.get('max_volume'))
    vol_tf = custom_filters.get('volume_tf') or '24h'
    if min_vol is not None or max_vol is not None:
        rules.append({
            'rule_type': 'volume',
            'time_min': min_vol,
            'time_max': max_vol,
            'search_text': vol_tf
        })

    # Pre-signal Performance (% change)
    min_perf = safe_float_filter(custom_filters.get('min_perf'))
    max_perf = safe_float_filter(custom_filters.get('max_perf'))
    perf_tf = custom_filters.get('perf_tf') or '5m'
    if min_perf is not None or max_perf is not None:
        rules.append({
            'rule_type': 'performance',
            'time_min': min_perf,
            'time_max': max_perf,
            'search_text': perf_tf
        })

    # Category (boost, cto, profile, ad, etc.)
    cat = custom_filters.get('category')
    if cat and str(cat).lower() not in ('all', '', 'any'):
        rules.append({
            'rule_type': 'category',
            'search_text': str(cat).strip()
        })

    # Migrated Status (yes, no, all)
    mig = custom_filters.get('migrated')
    if mig and str(mig).lower() not in ('all', '', 'any'):
        rules.append({
            'rule_type': 'migrated',
            'search_text': 'yes' if str(mig).lower() in ('yes', 'migrated', 'true', '1') else 'no'
        })

    # Platform
    plat = custom_filters.get('platform')
    if plat and str(plat).lower() not in ('all', '', 'any'):
        rules.append({
            'rule_type': 'platform',
            'search_text': str(plat).strip()
        })

    # Exclude Platform
    ex_plat = custom_filters.get('exclude_platform')
    if ex_plat and str(ex_plat).lower() not in ('all', '', 'any'):
        rules.append({
            'rule_type': 'exclude_platform',
            'search_text': str(ex_plat).strip()
        })

    # Token Name / Symbol Filter (substring, exact, or regex)
    name_flt = custom_filters.get('name_filter') if custom_filters.get('name_filter') is not None else custom_filters.get('keywords')
    name_mode = custom_filters.get('name_mode') or 'include'
    if name_flt and str(name_flt).strip():
        rules.append({
            'rule_type': 'token_name',
            'search_text': str(name_flt).strip(),
            'replace_text': name_mode
        })

    # Message / Word Filter
    word_flt = custom_filters.get('word_filter')
    word_mode = custom_filters.get('word_mode') or 'include'
    if word_flt and str(word_flt).strip():
        rules.append({
            'rule_type': 'filter',
            'search_text': str(word_flt).strip(),
            'replace_text': word_mode
        })

    # Extract CA Requirement
    extract_ca = custom_filters.get('extract_ca')
    if extract_ca and str(extract_ca).lower() in ('yes', 'true', '1'):
        rules.append({
            'rule_type': 'extract_ca',
            'search_text': 'yes'
        })

    # DEX Payment ($)
    p_min = safe_float_filter(custom_filters.get('dex_payment_min'))
    p_max = safe_float_filter(custom_filters.get('dex_payment_max'))
    p_label = custom_filters.get('dex_payment_label')
    if p_min is not None or p_max is not None or (p_label and str(p_label).strip()):
        rules.append({
            'rule_type': 'dex_payment',
            'time_min': p_min,
            'time_max': p_max,
            'search_text': str(p_label).strip() if p_label else ''
        })

    # Akoueflow Indicator Preset
    ind_preset = str(custom_filters.get('ind_preset') or custom_filters.get('indicator_preset') or 'all').strip().lower()
    if ind_preset and ind_preset != 'all':
        rules.append({
            'rule_type': 'indicator_preset',
            'search_text': ind_preset
        })

    # Granular Akoueflow Indicators (10 metrics)
    indicator_specs = [
        ('net_buy_pressure', 'min_net_buy', 'max_net_buy'),
        ('buy_dominance_pct', 'min_buy_dom', 'max_buy_dom'),
        ('buy_pressure_accel', 'min_buy_press_accel', 'max_buy_press_accel'),
        ('buy_vol_accel', 'min_buy_accel', 'max_buy_accel'),
        ('sell_vol_accel', 'min_sell_accel', 'max_sell_accel'),
        ('net_buy_to_liq', 'min_buy_liq', 'max_buy_liq'),
        ('vol_to_mc', 'min_vol_mc', 'max_vol_mc'),
        ('vol_to_liq', 'min_vol_liq', 'max_vol_liq'),
        ('buyer_seller_ratio', 'min_bs_ratio', 'max_bs_ratio'),
        ('holder_concentration', 'min_holder_conc', 'max_holder_conc'),
        ('round_trip_cost', 'min_round_trip', 'max_round_trip'),
        ('bonding_curve_pct', 'min_bonding_curve', 'max_bonding_curve'),
        ('dev_prior_tokens', 'min_dev_tokens', 'max_dev_tokens'),
    ]
    for ikey, min_k, max_k in indicator_specs:
        imin = safe_float_filter(custom_filters.get(min_k))
        imax = safe_float_filter(custom_filters.get(max_k))
        if imin is not None or imax is not None:
            rules.append({
                'rule_type': 'indicator',
                'search_text': ikey,
                'time_min': imin,
                'time_max': imax
            })
    return rules


@app.route('/api/outcomes/filters_to_rules', methods=['POST'])
def outcomes_filters_to_rules():
    """Return the workflow rules for the current Outcomes Lab filters (for 'Save as Workflow')."""
    try:
        req_data = request.get_json() or {}
        custom_filters = req_data.get('custom_filters') or {}
        system = str(req_data.get('system') or 'all').lower().strip()
        rules = build_rules_from_custom_filters(custom_filters)
        # Live workflows match exclude_platform as a regex against the message, while the
        # Outcomes Lab matches loosely (punctuation ignored, pump CAs count as pump.fun).
        # Translate to regexes that exclude the same tokens live.
        live_exclude_patterns = {
            'pumpfun': r'pump\.?fun|pumpswap|CA:\s*\S*pump\b',
            'pump': r'pump\.?fun|pumpswap|CA:\s*\S*pump\b',
            'raydium': r'raydium',
            'moonshot': r'moonshot',
            'meteora': r'meteora',
        }
        for rule in rules:
            if rule.get('rule_type') == 'exclude_platform':
                key = re.sub(r'[^a-z0-9]', '', str(rule.get('search_text') or '').lower())
                if key in live_exclude_patterns:
                    rule['search_text'] = live_exclude_patterns[key]
        signal_type = {'validated': 'validated', 'paid': 'dex_paid'}.get(system, 'both')
        return jsonify({'success': True, 'rules': rules, 'signal_type': signal_type})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/outcomes/simulate', methods=['POST'])
def simulate_outcomes():
    """Simulate workflow rules or custom filter criteria against historical signals."""
    try:
        req_data = request.get_json() or {}
        system = str(req_data.get('system') or 'validated').lower().strip()
        timeframe = str(req_data.get('timeframe') or 'all').lower().strip()
        date_from = req_data.get('date_from') or req_data.get('from_date') or req_data.get('from')
        date_to = req_data.get('date_to') or req_data.get('to_date') or req_data.get('to')
        workflow_id = req_data.get('workflow_id')
        custom_filters = req_data.get('custom_filters') or req_data.get('filters') or {}

        # 1. Fetch historical candidate tokens
        candidates = get_historical_candidate_tokens(system, timeframe, date_from=date_from, date_to=date_to)

        # 2. Build rules list
        rules = []
        workflow_meta = None

        if workflow_id and supabase_client:
            try:
                wf_id_int = int(workflow_id)
                wf_res = supabase_client.table('workflows').select('id,name,is_active').eq('id', wf_id_int).execute()
                if wf_res.data:
                    workflow_meta = wf_res.data[0]
                    if not custom_filters:
                        rule_res = supabase_client.table('rules').select('id,workflow_id,rule_type,search_text,replace_text,time_min,time_max').eq('workflow_id', wf_id_int).execute()
                        rules.extend(rule_res.data or [])
            except Exception as e:
                debug_log(f"[OUTCOMES] Error fetching workflow {workflow_id}: {e}")

        # If user explicitly selected a system ('paid' or 'validated'), don't let a conflicting workflow signal_type rule block candidate tokens
        if system in ('paid', 'validated'):
            rules = [r for r in rules if r.get('rule_type') != 'signal_type']

        # Process custom filters
        rules.extend(build_rules_from_custom_filters(custom_filters))

        # 3. Baseline metrics across all candidates
        tot_cand = len(candidates)
        all_peaks_1h = [min(float(x.get('peak_mult_1h') or 1.0), 100.0) for x in candidates]
        all_peaks_best = [min(max(float(x.get('peak_mult_4h') or 1.0), float(x.get('peak_mult_1h') or 1.0)), 100.0) for x in candidates]
        baseline_win_2x = round((sum(1 for p in all_peaks_best if p >= 2.0) / tot_cand) * 100.0, 1) if tot_cand else 0.0
        baseline_avg_1h = round(sum(all_peaks_1h) / tot_cand, 2) if tot_cand else 1.0
        baseline_avg_best = round(sum(all_peaks_best) / tot_cand, 2) if tot_cand else 1.0

        # 4. Filter candidates
        matched = []
        for t in candidates:
            if token_matches_workflow_rules(t, rules):
                matched.append(t)

        matched_count = len(matched)
        peaks_1h = [min(float(x.get('peak_mult_1h') or 1.0), 100.0) for x in matched]
        peaks_4h = [min(float(x.get('peak_mult_4h') or x.get('peak_mult_1h') or 1.0), 100.0) for x in matched]
        peaks_best = [min(max(float(x.get('peak_mult_4h') or 1.0), float(x.get('peak_mult_1h') or 1.0)), 100.0) for x in matched]
        drawdowns = [float(x.get('pre_peak_dd_1h_pct') or 0.0) for x in matched]

        wins_1_5x = sum(1 for p in peaks_best if p >= 1.5)
        wins_2x = sum(1 for p in peaks_best if p >= 2.0)
        wins_3x = sum(1 for p in peaks_best if p >= 3.0)
        wins_5x = sum(1 for p in peaks_best if p >= 5.0)

        tot_peaks = len(peaks_1h) or 1
        avg_1h = sum(peaks_1h) / tot_peaks if peaks_1h else 1.0
        avg_4h = sum(peaks_4h) / tot_peaks if peaks_4h else 1.0
        avg_best = sum(peaks_best) / tot_peaks if peaks_best else 1.0
        avg_dd = sum(drawdowns) / tot_peaks if drawdowns else 0.0
        worst_dd = min(drawdowns) if drawdowns else 0.0

        # Performance Tier Distribution (<1x slate grey, 1x-1.5x amber, 1.5x-2x cyan, 2x-3x emerald, 3x-5x purple, 5x+ gold)
        t_lt_1x = sum(1 for p in peaks_best if p < 1.05)
        t_1x_1_5x = sum(1 for p in peaks_best if 1.05 <= p < 1.5)
        t_1_5x_2x = sum(1 for p in peaks_best if 1.5 <= p < 2.0)
        t_2x_3x = sum(1 for p in peaks_best if 2.0 <= p < 3.0)
        t_3x_5x = sum(1 for p in peaks_best if 3.0 <= p < 5.0)
        t_5x_plus = sum(1 for p in peaks_best if p >= 5.0)

        tier_stats = {
            'lt_1x': t_lt_1x,
            'lt_1x_pct': round((t_lt_1x / tot_peaks) * 100.0, 1),
            '1x_1_5x': t_1x_1_5x,
            '1x_1_5x_pct': round((t_1x_1_5x / tot_peaks) * 100.0, 1),
            '1_5x_2x': t_1_5x_2x,
            '1_5x_2x_pct': round((t_1_5x_2x / tot_peaks) * 100.0, 1),
            '2x_3x': t_2x_3x,
            '2x_3x_pct': round((t_2x_3x / tot_peaks) * 100.0, 1),
            '3x_5x': t_3x_5x,
            '3x_5x_pct': round((t_3x_5x / tot_peaks) * 100.0, 1),
            '5x_plus': t_5x_plus,
            '5x_plus_pct': round((t_5x_plus / tot_peaks) * 100.0, 1),
        }

        # Trajectory Breakdown
        paths = [str(x.get('path_1h') or 'UNKNOWN').upper() for x in matched]
        tot_paths = len(paths) or 1
        trajectories = {
            'direct_pump': paths.count('DIRECT_PUMP'),
            'direct_pump_pct': round((paths.count('DIRECT_PUMP') / tot_paths) * 100.0, 1),
            'dip_then_pump': paths.count('DIP_THEN_PUMP'),
            'dip_then_pump_pct': round((paths.count('DIP_THEN_PUMP') / tot_paths) * 100.0, 1),
            'straight_dump': paths.count('STRAIGHT_DUMP'),
            'straight_dump_pct': round((paths.count('STRAIGHT_DUMP') / tot_paths) * 100.0, 1),
            'pump_then_dump': paths.count('PUMP_THEN_DUMP'),
            'pump_then_dump_pct': round((paths.count('PUMP_THEN_DUMP') / tot_paths) * 100.0, 1),
        }

        # Sort matched tokens by best peak multiplier descending
        matched_sorted = sorted(matched, key=lambda x: max(float(x.get('peak_mult_4h') or 1.0), float(x.get('peak_mult_1h') or 1.0)), reverse=True)
        best_token = matched_sorted[0] if matched_sorted else {}
        best_token_peak = max(float(best_token.get('peak_mult_4h') or 1.0), float(best_token.get('peak_mult_1h') or 1.0)) if best_token else 1.0

        tokens_data = []
        for t in matched_sorted:
            ca = t.get('ca') or ''
            ev_ts = t.get('event_ts') or t.get('timestamp') or t.get('created_at') or t.get('fetched_at')
            if isinstance(ev_ts, (int, float)) and ev_ts > 1000000000000:
                ev_ts = int(ev_ts / 1000)
            date_str = ''
            if ev_ts:
                try:
                    if isinstance(ev_ts, (int, float)):
                        date_str = datetime.fromtimestamp(ev_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                    else:
                        date_str = str(ev_ts)
                except Exception:
                    pass
            tokens_data.append({
                'id': t.get('id'),
                'name': t.get('name') or t.get('token_name') or 'Unknown',
                'ca': ca,
                'platform': t.get('platform') or '',
                'market_cap': t.get('market_cap') or '',
                'entry_mc': parse_mc_usd(t.get('market_cap')) or parse_mc_usd(t.get('entry_mc')),
                'age': t.get('age') or '',
                'liquidity_usd': t.get('liquidity_usd'),
                'volume_24h': t.get('volume_24h'),
                'signal_type': t.get('signal_type') or 'dex',
                'source_system': t.get('source_system') or 'validated',
                'peak_mult_1h': round(float(t.get('peak_mult_1h') or 1.0), 2),
                'peak_mult_4h': round(float(t.get('peak_mult_4h') or t.get('peak_mult_1h') or 1.0), 2),
                'best_peak': round(max(float(t.get('peak_mult_4h') or 1.0), float(t.get('peak_mult_1h') or 1.0)), 2),
                'pre_peak_dd_1h_pct': round(float(t.get('pre_peak_dd_1h_pct') or 0.0), 1),
                'path_1h': str(t.get('path_1h') or 'UNKNOWN').upper(),
                'dex_url': t.get('dex_url') or (f"https://dexscreener.com/solana/{ca}" if ca else ''),
                'event_ts': ev_ts,
                'event_date': date_str,
                'timestamp': t.get('timestamp') or ev_ts,
                'created_at': t.get('created_at'),
                'indicators': {
                    'net_buy_pressure': get_token_indicator_value(t, 'net_buy_pressure'),
                    'buy_dominance_pct': get_token_indicator_value(t, 'buy_dominance_pct'),
                    'buy_pressure_accel': get_token_indicator_value(t, 'buy_pressure_accel'),
                    'buy_vol_accel': get_token_indicator_value(t, 'buy_vol_accel'),
                    'sell_vol_accel': get_token_indicator_value(t, 'sell_vol_accel'),
                    'net_buy_to_liq': get_token_indicator_value(t, 'net_buy_to_liq'),
                    'vol_to_mc': get_token_indicator_value(t, 'vol_to_mc'),
                    'vol_to_liq': get_token_indicator_value(t, 'vol_to_liq'),
                    'buyer_seller_ratio': get_token_indicator_value(t, 'buyer_seller_ratio'),
                    'holder_concentration': get_token_indicator_value(t, 'holder_concentration'),
                    'round_trip_cost': get_token_indicator_value(t, 'round_trip_cost'),
                    'bonding_curve_pct': get_token_indicator_value(t, 'bonding_curve_pct'),
                    'dev_prior_tokens': get_token_indicator_value(t, 'dev_prior_tokens'),
                }
            })

        return jsonify({
            'success': True,
            'system': system,
            'timeframe': timeframe,
            'timeframe_label': f"Custom ({date_from or 'Start'} - {date_to or 'Present'})" if timeframe == 'custom' else {'day': 'Past 24 Hours', 'week': 'Past 7 Days', 'month': 'Past 30 Days', 'all': 'All Time History'}.get(timeframe, 'Custom Timeframe'),
            'date_from': date_from,
            'date_to': date_to,
            'workflow': workflow_meta,
            'active_rules': rules,
            'rules_count': len(rules),
            'total_candidates': tot_cand,
            'matched_count': matched_count,
            'match_rate_pct': round((matched_count / tot_cand) * 100.0, 1) if tot_cand else 0.0,
            'win_rate_1_5x': round((wins_1_5x / matched_count) * 100.0, 1) if matched_count else 0.0,
            'win_rate_2x': round((wins_2x / matched_count) * 100.0, 1) if matched_count else 0.0,
            'win_rate_3x': round((wins_3x / matched_count) * 100.0, 1) if matched_count else 0.0,
            'win_rate_5x': round((wins_5x / matched_count) * 100.0, 1) if matched_count else 0.0,
            'avg_peak_1h': round(avg_1h, 2),
            'avg_peak_4h': round(avg_4h, 2),
            'avg_pre_peak_dd_pct': round(avg_dd, 1),
            'worst_dd_pct': round(worst_dd, 1),
            'best_token_name': best_token.get('name') or 'None',
            'best_token_ca': best_token.get('ca') or '',
            'best_token_peak': round(float(best_token_peak), 2) if best_token else 1.0,
            'tier_stats': tier_stats,
            'trajectories': trajectories,
            'baseline': {
                'total_candidates': tot_cand,
                'win_rate_2x': baseline_win_2x,
                'avg_peak_1h': baseline_avg_1h,
                'avg_peak_4h': baseline_avg_best
            },
            'tokens': tokens_data
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
