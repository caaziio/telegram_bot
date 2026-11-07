"""Workflow loading and message rule processing engine."""

import re
import json

from bot.config import debug_log
from bot.db.connection import custom_row_factory, db_lock, get_db
from bot.services.token_indicators import (
    INDICATOR_PRESET_LABELS,
    TOKEN_LAUNCH_METRIC_KEYS,
    compute_token_launch_metric,
    indicator_preset_passes,
)

def regex_matches(value, pattern):
    """Case-insensitively match a user-supplied regex.

    Invalid expressions fall back to literal substring matching so a typo in a
    saved rule cannot crash message processing.
    """
    value = str(value or '')
    pattern = str(pattern or '').strip()
    if not value or not pattern:
        return False
    try:
        return re.search(pattern, value, re.IGNORECASE) is not None
    except re.error:
        return pattern.casefold() in value.casefold()


def token_name_pattern_for_sql(pattern):
    """Return the user-supplied regex used by SQLite's REGEXP function."""
    return str(pattern or '').strip()

def get_workflows():
    with db_lock:
        conn = get_db()
        conn.row_factory = custom_row_factory
        cursor = conn.cursor()

        workflows = []
        try:
            for wf in cursor.execute('SELECT * FROM workflows').fetchall():
                wf_dict = dict(wf)
                rules = cursor.execute('SELECT * FROM rules WHERE workflow_id = ?', (wf['id'],)).fetchall()
                wf_dict['rules'] = [dict(r) for r in rules]
                workflows.append(wf_dict)
        except Exception:
            pass

        return workflows

def process_message_logic(text, rules, context=None):
    if not text:
        return text, False, ""

    processed_text = text
    context = context or {}
    payer_address = context.get('payer_address')
    signal_category = str(context.get('category') or '').strip().lower()
    if not signal_category:
        category_match = re.search(r'Category:\s*(BOOST|CTO|DEX PROFILE|DEX AD)', text, re.IGNORECASE)
        if category_match:
            signal_category = category_match.group(1).lower().replace(' ', '_')

    for rule in rules:
        rule_type = rule.get('rule_type')
        debug_log(f"      [RULE] type={rule_type}, search_text={rule.get('search_text')}, time_min={rule.get('time_min')}, time_max={rule.get('time_max')}")

        if rule_type == 'category':
            selected_category = str(rule.get('search_text') or '').strip().lower()
            if not signal_category or signal_category != selected_category:
                return None, True, f"Dropped by Category Filter (Required: {selected_category or 'none'}, Found: {signal_category or 'none'})"

        elif rule_type == 'signal_type':
            desired = str(rule.get('search_text') or 'both').lower().strip()
            current_src = str(context.get('signal_source') or '').lower().strip()
            if desired in ('dex_paid', 'paid') and current_src not in ('dex_paid', 'paid'):
                return None, True, f"Dropped: Workflow requires DEX Paid signals only (found {current_src or 'validated'})"
            elif desired == 'validated' and current_src in ('dex_paid', 'paid'):
                return None, True, "Dropped: Workflow requires Validated signals only (found DEX Paid)"

        # TOKEN AGE FILTER LOGIC
        elif rule_type == 'token_age':
            try:
                min_val = rule.get('time_min')
                max_val = rule.get('time_max')
                min_age = float(min_val) if min_val and str(min_val).strip() != "" else 0
                max_age = float(max_val) if max_val and str(max_val).strip() != "" else 5256000
            except (ValueError, TypeError):
                min_age = 0
                max_age = 5256000

            age_type = rule.get('search_text') or 'creation'
            age_type = age_type.lower().strip()
            debug_log(f"      [TOKEN AGE] type={age_type}, min_age={min_age}, max_age={max_age}")

            # Find the relevant line for age extraction
            lines = text.split('\n') if text else []
            age_line = None

            if age_type == 'migration':
                # Look for migration age line
                for line in lines:
                    if 'after migration' in line.lower() or 'migration age' in line.lower() or 'age (after migration)' in line.lower():
                        age_line = line
                        break
            else:
                # Look for creation age line specifically first
                for line in lines:
                    if 'from creation' in line.lower() or 'creation age' in line.lower() or 'age (from creation)' in line.lower():
                        age_line = line
                        break
                # If not found, look for general "age" line that is NOT a migration line
                if not age_line:
                    for line in lines:
                        if 'age' in line.lower() and 'migration' not in line.lower():
                            age_line = line
                            break

            age_text = None
            if age_line:
                # Try to get text after colon
                if ':' in age_line:
                    age_text = age_line.split(':', 1)[1].strip()
                else:
                    age_text = age_line.strip()

            token_minutes = None

            if age_text:
                debug_log(f"      [TOKEN AGE] Extracted age text: '{age_text}'")
                # Check for "Just now" or "New" which imply 0 minutes
                if re.search(r'(?:Just now|New)', age_text, re.IGNORECASE):
                    token_minutes = 0
                elif re.search(r'Unknown', age_text, re.IGNORECASE):
                    token_minutes = None  # Explicitly unknown
                else:
                    # Robust regex to handle '1 day, 2 hours, 45 minutes', '1d 4h', '1hr 46min', etc.
                    d = int(re.search(r'(\d+)\s*(?:d|day)', age_text, re.IGNORECASE).group(1) if re.search(r'(\d+)\s*(?:d|day)', age_text, re.IGNORECASE) else 0)
                    h = int(re.search(r'(\d+)\s*(?:h|hr|hour)', age_text, re.IGNORECASE).group(1) if re.search(r'(\d+)\s*(?:h|hr|hour)', age_text, re.IGNORECASE) else 0)
                    m = int(re.search(r'(\d+)\s*(?:m|min|minute)', age_text, re.IGNORECASE).group(1) if re.search(r'(\d+)\s*(?:m|min|minute)', age_text, re.IGNORECASE) else 0)

                    # If we parsed 0 minutes but there was no actual "0" in the text, it means it's unknown/invalid
                    if d == 0 and h == 0 and m == 0 and not re.search(r'\b0\b', age_text):
                        token_minutes = None
                    else:
                        token_minutes = (d * 1440) + (h * 60) + m
                        debug_log(f"      [TOKEN AGE] Parsed: d={d}, h={h}, m={m} → {token_minutes} minutes")
            else:
                # If no age_line found, see if we can find a standalone time pattern in the whole text (only for creation fallback)
                if age_type != 'migration':
                    standalone_match = re.search(r'(?:(\d+)\s*(?:d|day)s?)?\s*,?\s*(?:(\d+)\s*(?:h|hr|hour)s?)?\s*,?\s*(?:(\d+)\s*(?:m|min|minute)s?)', text, re.IGNORECASE) if text else None
                    if standalone_match and (standalone_match.group(1) or standalone_match.group(2) or standalone_match.group(3)):
                        d = int(standalone_match.group(1) or 0)
                        h = int(standalone_match.group(2) or 0)
                        m = int(standalone_match.group(3) or 0)
                        token_minutes = (d * 1440) + (h * 60) + m
                        debug_log(f"      [TOKEN AGE] Parsed standalone: d={d}, h={h}, m={m} → {token_minutes} minutes")

            # If the age is unknown, drop the message
            if token_minutes is None:
                debug_log("      [TOKEN AGE] DROPPING: Token age is unknown or not found")
                return None, True, f"Dropped by Token Age Filter (Age is Unknown)"

            if not (min_age <= token_minutes <= max_age):
                debug_log(f"      [TOKEN AGE] DROPPING: {min_age} <= {token_minutes} <= {max_age} is FALSE")
                return None, True, f"Dropped by Token Age Filter (Allowed: {min_age}-{max_age}m, Found: {token_minutes}m)"
            else:
                debug_log(f"      [TOKEN AGE] PASSED: {min_age} <= {token_minutes} <= {max_age}")

        # EXTRACT CA LOGIC (Now acts purely as a filter)
        elif rule_type == 'extract_ca':
            ca_regex = r'(?:CA|Contract|ca)[\s:]*([1-9A-HJ-NP-Za-km-z]{32,44}|0x[a-fA-F0-9]{40})'
            match = re.search(ca_regex, text, re.IGNORECASE)
            if not match or not match.group(1):
                return None, True, "Dropped by Extract CA (No Contract Address found in text)"
            else:
                debug_log(f"      [EXTRACT CA] PASSED: Found CA={match.group(1)[:20]}...")

        # PERFORMANCE FILTER LOGIC
        elif rule_type == 'performance':
            timeframe = rule.get('search_text') or '5m'

            # Normalize timeframe to match message output format (e.g. 1hr/1h -> 1h, 6hr/6h -> 6h, 24hr/24h -> 24h)
            timeframe_clean = timeframe.lower().strip()
            if timeframe_clean in ['1hr', '1h']:
                timeframe_clean = '1h'
            elif timeframe_clean in ['6hr', '6h']:
                timeframe_clean = '6h'
            elif timeframe_clean in ['24hr', '24h']:
                timeframe_clean = '24h'

            min_str = rule.get('time_min')
            max_str = rule.get('time_max')
            try:
                min_perf = float(min_str) if min_str and str(min_str).strip() != "" else float('-inf')
                max_perf = float(max_str) if max_str and str(max_str).strip() != "" else float('inf')

                # Look for e.g., '5m: +67%' or '1h: -27%'
                pattern = rf'{timeframe_clean}:\s*([+\-]?\d+(?:\.\d+)?)%'
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    actual_performance = float(match.group(1))
                    if not (min_perf <= actual_performance <= max_perf):
                        return None, True, f"Dropped by Performance Filter ({timeframe_clean}: {actual_performance}% not between {min_perf}% and {max_perf}%)"
                    else:
                        debug_log(f"      [PERF] PASSED: {timeframe_clean} = {actual_performance}%")
                else:
                    debug_log(f"      [PERF] PASSED: No '{timeframe_clean}:' pattern found in text (filter skipped)")
            except ValueError:
                pass

        # DEX PAYMENT FILTER LOGIC
        elif rule_type == 'dex_payment':
            try:
                min_p_str = rule.get('time_min')
                max_p_str = rule.get('time_max')
                min_p = float(min_p_str) if min_p_str and str(min_p_str).strip() != "" else 0.0
                max_p = float(max_p_str) if max_p_str and str(max_p_str).strip() != "" else float('inf')

                # Extract payment from string: "DEX Payment: $12.34 USD" or "DEX Payment: $12 USD"
                match = re.search(r'DEX Payment:\s*\$?([\d,]+(?:\.\d+)?)\s*(?:USD)?', text, re.IGNORECASE)
                if match:
                    val = float(match.group(1).replace(',', ''))
                    if not (min_p <= val <= max_p):
                        debug_log(f"      [DEX PAYMENT] DROPPING: {min_p} <= {val} <= {max_p} is FALSE")
                        return None, True, f"Dropped by DEX Payment Filter (Allowed: ${min_p}-${max_p} USD, Found: ${val} USD)"
                    else:
                        debug_log(f"      [DEX PAYMENT] PASSED: {min_p} <= {val} <= {max_p}")
                        # Override Order Type display name if a custom label is specified
                        label = (rule.get('search_text') or '').strip()
                        if label:
                            processed_text = re.sub(r'(Order:\s*📦\s*)(.*)', rf'\1{label}', processed_text)
                else:
                    if min_p > 0:
                        return None, True, "Dropped by DEX Payment Filter (No payment amount found in text)"
            except Exception as e:
                print(f"      [DEX PAYMENT] Error evaluating filter: {e}", flush=True)

        # MIGRATION FILTER LOGIC
        elif rule_type == 'migrated':
            try:
                expected = (rule.get('search_text') or 'yes').strip().lower()
                match = re.search(r'Migrated:\s*(Yes|No)', text, re.IGNORECASE)
                if match:
                    actual = match.group(1).lower()
                    if actual != expected:
                        debug_log(f"      [MIGRATION] DROPPING: expected {expected}, found {actual}")
                        return None, True, f"Dropped by Migration Filter (Expected: {expected}, Found: {actual})"
                    else:
                        debug_log(f"      [MIGRATION] PASSED: {actual} == {expected}")
                else:
                    if expected == 'yes':
                        return None, True, "Dropped by Migration Filter (No migration info found in text)"
            except Exception as e:
                print(f"      [MIGRATION] Error evaluating filter: {e}", flush=True)

        # MARKET CAP FILTER LOGIC
        elif rule_type == 'market_cap':
            try:
                min_mc_str = rule.get('time_min')
                max_mc_str = rule.get('time_max')
                min_mc = float(min_mc_str) if min_mc_str and str(min_mc_str).strip() != "" else 0
                max_mc = float(max_mc_str) if max_mc_str and str(max_mc_str).strip() != "" else float('inf')

                # Extract MC from string: "💰 Market Cap: $1.20M" or "$500.5K" or "$500"
                mc_match = re.search(r'Market Cap:\s*\$?([\d\.]+)([KM]?)', text, re.IGNORECASE)
                if mc_match:
                    val = float(mc_match.group(1))
                    suffix = mc_match.group(2).upper()
                    if suffix == 'K':
                        val *= 1_000
                    elif suffix == 'M':
                        val *= 1_000_000

                    if not (min_mc <= val <= max_mc):
                        return None, True, f"Dropped by Market Cap Filter (Allowed: ${min_mc}-${max_mc}, Found: ${val})"
                else:
                    return None, True, "Dropped by Market Cap Filter (Market Cap is Unknown)"
            except Exception as e:
                return None, True, f"Dropped by Market Cap Filter (Error parsing: {e})"

        elif rule_type in ('liquidity', 'volume'):
            try:
                min_value = float(rule.get('time_min')) if str(rule.get('time_min') or '').strip() else 0.0
                max_value = float(rule.get('time_max')) if str(rule.get('time_max') or '').strip() else float('inf')
                timeframe = (rule.get('search_text') or '24h').strip().lower()
                label = 'Liquidity' if rule_type == 'liquidity' else f'{timeframe} Volume'
                pattern = rf'{re.escape(label)}:\s*\$?([\d,.]+)([KMB]?)'
                metric_match = re.search(pattern, text, re.IGNORECASE)
                if not metric_match:
                    return None, True, f"Dropped by {label} Filter ({label} is Unknown)"
                actual = float(metric_match.group(1).replace(',', ''))
                suffix = metric_match.group(2).upper()
                actual *= {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(suffix, 1)
                if not (min_value <= actual <= max_value):
                    return None, True, f"Dropped by {label} Filter (Allowed: ${min_value}-${max_value}, Found: ${actual})"
            except Exception as e:
                return None, True, f"Dropped by {rule_type.title()} Filter (Error parsing: {e})"

        # BOOST COUNT FILTER LOGIC
        elif rule_type == 'boost_count':
            try:
                min_b_str = rule.get('time_min')
                max_b_str = rule.get('time_max')
                min_b = float(min_b_str) if min_b_str and str(min_b_str).strip() != "" else 0
                max_b = float(max_b_str) if max_b_str and str(max_b_str).strip() != "" else float('inf')

                # First check context if provided
                boost_val = context.get('boost_count')
                if boost_val is None:
                    # Look for "Boosts: 🚀 500" or "Boosts: 500" in message text
                    boost_match = re.search(r'Boosts?:\s*(?:🚀\s*)?([\d,]+)', text, re.IGNORECASE)
                    if boost_match:
                        boost_val = float(boost_match.group(1).replace(',', ''))
                    else:
                        boost_val = 0

                boost_val = float(boost_val or 0)
                if not (min_b <= boost_val <= max_b):
                    debug_log(f"      [BOOST COUNT] DROPPING: {min_b} <= {boost_val} <= {max_b} is FALSE")
                    min_disp = f"{int(min_b):,}" if min_b != float('-inf') else "-∞"
                    max_disp = f"{int(max_b):,}" if max_b != float('inf') else "∞"
                    return None, True, f"Dropped by Boost Count Filter (Allowed: {min_disp}-{max_disp}, Found: {int(boost_val):,})"
                else:
                    debug_log(f"      [BOOST COUNT] PASSED: {min_b} <= {boost_val} <= {max_b}")
            except Exception as e:
                print(f"      [BOOST COUNT] Error evaluating filter: {e}", flush=True)

        # AKOUEFLOW INDICATORS FILTER LOGIC
        elif rule_type == 'indicator':
            try:
                indicator_key = (rule.get('search_text') or 'net_buy_pressure').strip().lower()
                min_str = rule.get('time_min')
                max_str = rule.get('time_max')
                min_val = float(min_str) if min_str is not None and str(min_str).strip() != "" else float('-inf')
                max_val = float(max_str) if max_str is not None and str(max_str).strip() != "" else float('inf')

                # Check context indicators first
                indicators = context.get('indicators')
                if not indicators and isinstance(context.get('metrics_data'), dict):
                    indicators = context.get('metrics_data', {}).get('indicators')

                val = None
                if indicator_key in TOKEN_LAUNCH_METRIC_KEYS:
                    # Bonding curve % / dev prior tokens: metrics first, then message text
                    stored = indicators.get(indicator_key) if isinstance(indicators, dict) else None
                    val = stored if stored is not None else compute_token_launch_metric(
                        context.get('metrics_data') or {}, text, indicator_key)
                elif isinstance(indicators, dict) and indicator_key in indicators:
                    val = indicators.get(indicator_key)
                else:
                    # Fallback: Extract from message text
                    if indicator_key == 'net_buy_pressure':
                        m = re.search(r'Net Buy Pressure:\s*([+\-]?)\$?([\d,.]+)([KMB]?)', text, re.IGNORECASE)
                        if m:
                            sign = -1.0 if m.group(1) == '-' else 1.0
                            num = float(m.group(2).replace(',', ''))
                            sfx = m.group(3).upper()
                            num *= {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(sfx, 1)
                            val = sign * num
                    elif indicator_key == 'buy_dominance_pct':
                        m = re.search(r'\((\d+(?:\.\d+)?)%\s*Buy Dom\)', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))
                    elif indicator_key == 'buy_pressure_accel':
                        m = re.search(r'Buy Press Accel:\s*([+\-]?\d+(?:\.\d+)?)%', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))
                    elif indicator_key == 'buy_vol_accel':
                        m = re.search(r'Vol Accel:\s*Buy\s*(\d+(?:\.\d+)?)x', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))
                    elif indicator_key == 'sell_vol_accel':
                        m = re.search(r'Vol Accel:.*?Sell\s*(\d+(?:\.\d+)?)x', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))
                    elif indicator_key == 'net_buy_to_liq':
                        m = re.search(r'Net Buy / Liq:\s*([+\-]?\d+(?:\.\d+)?)%', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))
                    elif indicator_key == 'vol_to_mc':
                        m = re.search(r'Vol/MC:\s*(\d+(?:\.\d+)?)x', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))
                    elif indicator_key == 'vol_to_liq':
                        m = re.search(r'Vol/Liq:\s*(\d+(?:\.\d+)?)x', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))
                    elif indicator_key == 'buyer_seller_ratio':
                        m = re.search(r'Buyer/Seller Ratio:\s*(\d+(?:\.\d+)?)', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))
                    elif indicator_key == 'holder_concentration':
                        m = re.search(r'Holder Concentration:\s*(\d+(?:\.\d+)?)%', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))
                    elif indicator_key == 'round_trip_cost':
                        m = re.search(r'Round-Trip Cost:\s*~?(\d+(?:\.\d+)?)%', text, re.IGNORECASE)
                        if m:
                            val = float(m.group(1))

                label = indicator_key.replace('_', ' ').title()
                if val is None:
                    if min_val != float('-inf') or max_val != float('inf'):
                        debug_log(f"      [INDICATOR] DROPPING: {indicator_key} is unavailable or unsellable")
                        return None, True, f"Dropped by Akoueflow Indicator Filter ({label} is unavailable or unsellable)"
                else:
                    val = float(val)
                    if not (min_val <= val <= max_val):
                        debug_log(f"      [INDICATOR] DROPPING: {min_val} <= {val} <= {max_val} is FALSE for {indicator_key}")
                        min_disp = f"{min_val}" if min_val != float('-inf') else "-∞"
                        max_disp = f"{max_val}" if max_val != float('inf') else "∞"
                        return None, True, f"Dropped by Akoueflow Indicator Filter ({label}: Allowed {min_disp} to {max_disp}, Found {val})"
                    else:
                        debug_log(f"      [INDICATOR] PASSED: {min_val} <= {val} <= {max_val} for {indicator_key}")
            except Exception as e:
                print(f"      [INDICATOR] Error evaluating filter: {e}", flush=True)

        # SHOW WALLET LOGIC
        elif rule_type == 'show_wallet':
            show_val = (rule.get('search_text') or 'yes').strip().lower()
            if show_val == 'yes':
                wallet = payer_address
                if wallet:
                    wallet_line = f"👛 Payer Wallet: https://solscan.io/account/{wallet}"
                    if wallet_line not in processed_text:
                        if "📈 Chart:" in processed_text:
                            processed_text = processed_text.replace("📈 Chart:", f"{wallet_line}\n📈 Chart:")
                        else:
                            processed_text += f"\n{wallet_line}"

        # EXCLUDE PLATFORM LOGIC
        elif rule_type == 'exclude_platform':
            platform_to_exclude = (rule.get('search_text') or '').strip()
            if platform_to_exclude:
                # Look for "Status: ✅ Migrated (on pump.fun)" or "Platform: Solana"
                if regex_matches(text, platform_to_exclude):
                    return None, True, f"Dropped by Platform Exclusion (Found '{platform_to_exclude}')"

        # PLATFORM (INCLUDE) LOGIC - mirrors the Outcomes Lab platform filter
        elif rule_type == 'platform':
            req_plat = (rule.get('search_text') or '').strip().lower()
            if req_plat and req_plat not in ('all', 'any'):
                corpus = text.lower()
                ca_match = re.search(r'CA:\s*([A-Za-z0-9]+)', text)
                if ca_match and ca_match.group(1).lower().endswith('pump'):
                    corpus += ' pumpfun'
                clean_req = re.sub(r'[^a-z0-9]', '', req_plat)
                if clean_req not in re.sub(r'[^a-z0-9]', '', corpus):
                    return None, True, f"Dropped by Platform Filter (requires '{req_plat}')"

        # AKOUEFLOW INDICATOR PRESET LOGIC - same thresholds as the Outcomes Lab
        elif rule_type == 'indicator_preset':
            preset = (rule.get('search_text') or '').strip().lower()
            if preset and preset != 'all':
                inds = context.get('indicators')
                m_data = context.get('metrics_data')
                if not inds and isinstance(m_data, dict):
                    inds = m_data.get('indicators')
                token_view = {
                    'indicators': inds if isinstance(inds, dict) else {},
                    'metrics_data': m_data if isinstance(m_data, dict) else None,
                    'formatted_message': text,
                }
                if not indicator_preset_passes(preset, token_view):
                    label = INDICATOR_PRESET_LABELS.get(preset, preset)
                    return None, True, f"Dropped by Akoueflow Indicator Preset ({label})"

        # TOKEN NAME FILTER LOGIC
        elif rule_type == 'token_name':
            pattern = (rule.get('search_text') or '').strip()
            mode = (rule.get('replace_text') or 'include').strip().lower()

            # Extract token/project name
            token_name = context.get('token_name') or context.get('project_name')
            if not token_name:
                proj_match = re.search(r'PROJECT:\s*([^🚀\n\r]+)', text, re.IGNORECASE)
                if proj_match:
                    token_name = proj_match.group(1).strip()
                else:
                    name_match = re.search(r'(?:Token Name|Token|Name):\s*([^\n\r]+)', text, re.IGNORECASE)
                    if name_match:
                        token_name = name_match.group(1).strip()
                    else:
                        token_name = ""

            structured_filter = None
            filter_prefix = 'token_name_filter:'
            if pattern.startswith(filter_prefix):
                try:
                    structured_filter = json.loads(pattern[len(filter_prefix):])
                except (TypeError, ValueError, json.JSONDecodeError):
                    structured_filter = {'exact': [], 'contains': []}

            if structured_filter is not None:
                normalized_name = token_name.casefold().strip()
                exact_names = []
                for value in structured_filter.get('exact', []):
                    entry = str(value)
                    if len(entry) >= 2 and entry[0] == entry[-1] == '"':
                        entry = entry[1:-1]
                    else:
                        entry = entry.strip()
                    if entry:
                        exact_names.append(entry.casefold())

                contained_entries = []
                for value in structured_filter.get('contains', []):
                    entry = str(value)
                    is_quoted = len(entry) >= 2 and entry[0] == entry[-1] == '"'
                    literal = entry[1:-1] if is_quoted else entry.strip()
                    if literal:
                        contained_entries.append((literal.casefold(), is_quoted))

                contains_match = any(
                    literal in normalized_name if is_quoted
                    else re.search(rf'(?<!\w){re.escape(literal)}(?!\w)', normalized_name, re.IGNORECASE) is not None
                    for literal, is_quoted in contained_entries
                )
                is_matched = normalized_name in exact_names or contains_match
                filter_description = f"{len(exact_names)} exact / {len(contained_entries)} contains"
            else:
                # Backward compatibility for workflows saved with the old regex input.
                pattern = pattern or '.*'
                is_matched = regex_matches(token_name, pattern)
                filter_description = pattern

            debug_log(f"      [TOKEN NAME] token_name='{token_name}', filter='{filter_description}', mode='{mode}'")

            if mode == 'exclude':
                if is_matched and token_name:
                    debug_log(f"      [TOKEN NAME] DROPPING: Token name '{token_name}' matched excluded filter '{filter_description}'")
                    return None, True, f"Dropped by Token Name Filter (Token '{token_name}' matched excluded filter)"
                else:
                    debug_log(f"      [TOKEN NAME] PASSED: Token name '{token_name}' did not match excluded filter '{filter_description}'")
            else:  # mode == 'include' (default)
                if not is_matched:
                    debug_log(f"      [TOKEN NAME] DROPPING: Token name '{token_name or 'Unknown'}' did not match required filter '{filter_description}'")
                    return None, True, f"Dropped by Token Name Filter (Token '{token_name or 'Unknown'}' did not match required filter)"
                else:
                    debug_log(f"      [TOKEN NAME] PASSED: Token name '{token_name}' matched required filter '{filter_description}'")

        # WORD FILTER LOGIC
        elif rule_type == 'filter':
            search = (rule.get('search_text') or '').strip()
            mode = (rule.get('replace_text') or 'include').strip().lower()
            if search:
                is_matched = regex_matches(text, search)
                if mode == 'exclude':
                    if is_matched:
                        debug_log(f"      [WORD FILTER] DROPPING: Regex matched: '{search}'")
                        return None, True, f"Dropped by Word Filter (Regex matched: '{search}')"
                    debug_log(f"      [WORD FILTER] PASSED: Regex '{search}' did not match")
                elif is_matched:
                    debug_log(f"      [WORD FILTER] PASSED: Regex matched: '{search}'")
                else:
                    debug_log(f"      [WORD FILTER] DROPPING: Required regex did not match: '{search}'")
                    return None, True, f"Dropped by Word Filter (Required regex did not match: '{search}')"

        # REPLACE LOGIC
        elif rule_type == 'replace':
            search = rule.get('search_text', '')
            replace = rule.get('replace_text', '')
            if search:
                try:
                    processed_text = re.sub(search, replace, processed_text, flags=re.IGNORECASE)
                except re.error:
                    processed_text = processed_text.replace(search, replace)

        # APPEND LOGIC
        elif rule_type == 'append':
            replace = rule.get('replace_text', '')
            if replace:
                processed_text += f"\n\n{replace}"

    debug_log("      [RESULT] All rules passed! Message will be forwarded.")
    return processed_text, False, ""

def process_message(text, rules, message_date):
    result_text, dropped, reason = process_message_logic(text, rules)
    return result_text
