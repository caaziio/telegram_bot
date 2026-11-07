"""Parsing helpers and token indicator extraction/synthesis."""

import re
import json
from datetime import datetime, timezone

from bot.config import debug_log
from bot.db.connection import db_lock, get_db
from bot.services.dex_enrichment import compute_akoueflow_indicators

def parse_mc_usd(val):
    if val is None or str(val).strip() in ('', 'None', 'Unknown'):
        return None
    try:
        clean_s = str(val).replace('$', '').replace(',', '').strip()
        m = re.search(r'([\d\.]+)\s*([KMB]?)', clean_s, re.I)
        if not m:
            return None
        num = float(m.group(1))
        suf = m.group(2).upper()
        if suf == 'K':
            num *= 1_000
        elif suf == 'M':
            num *= 1_000_000
        elif suf == 'B':
            num *= 1_000_000_000
        return num
    except Exception:
        return None

def parse_age_minutes(val):
    if val is None or str(val).strip() in ('', 'None', 'Unknown'):
        return None
    s = str(val).lower().strip()
    if 'just now' in s or 'new' in s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        pass
    m_d = re.search(r'(\d+)\s*(?:d|day)', s)
    m_h = re.search(r'(\d+)\s*(?:h|hr|hour)', s)
    m_m = re.search(r'(\d+)\s*(?:m|min|minute)', s)
    if not (m_d or m_h or m_m):
        return None
    total_m = 0.0
    if m_d:
        total_m += int(m_d.group(1)) * 1440
    if m_h:
        total_m += int(m_h.group(1)) * 60
    if m_m:
        total_m += int(m_m.group(1))
    return total_m

def extract_creation_age_str(age, formatted_message=None):
    if age and str(age).strip() not in ('', 'None', 'Unknown'):
        return str(age).strip()
    if formatted_message:
        m = re.search(r'(?:from creation|creation age)[^:\n]*:\s*([0-9a-z\s]+)', formatted_message, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and val != 'Unknown':
                return val
    return None

def parse_creation_age_minutes(age, formatted_message=None):
    s = extract_creation_age_str(age, formatted_message)
    return parse_age_minutes(s)

def extract_migration_age_str(migration_age, formatted_message=None):
    if migration_age and str(migration_age).strip() not in ('', 'None', '0', '-'):
        return str(migration_age).strip()
    if formatted_message:
        m = re.search(r'(?:after migration|migration age)[^:\n]*:\s*([0-9a-z\s]+)', formatted_message, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and val not in ('0', '-', 'None'):
                return val
    return None

def parse_migration_age_minutes(migration_age, formatted_message=None):
    s = extract_migration_age_str(migration_age, formatted_message)
    return parse_age_minutes(s)

def parse_date_to_timestamp(val, is_end_of_day=False):
    """
    Parse a date string (YYYY-MM-DD or ISO string or numeric timestamp) to a unix timestamp in seconds.
    If is_end_of_day is True for a pure date (YYYY-MM-DD), set time to 23:59:59 UTC.
    """
    if val is None or str(val).strip() in ('', 'None', 'null'):
        return None
    s = str(val).strip()
    try:
        f = float(s)
        if f > 1000000000000:
            return int(f / 1000.0)
        return int(f)
    except ValueError:
        pass

    # Check YYYY-MM-DD format
    if len(s) == 10 and re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        try:
            dt = datetime.strptime(s, '%Y-%m-%d')
            if is_end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            else:
                dt = dt.replace(hour=0, minute=0, second=0)
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            pass

    # Try ISO formats
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        pass

    return None

def sqlite_regexp(expr, item):
    if not expr:
        return True
    if item is None:
        return False
    try:
        return bool(re.search(expr, str(item), re.IGNORECASE))
    except re.error:
        return expr.lower() in str(item).lower()

def synthesize_token_indicators(item):
    """
    Synthesize Akoueflow metrics and the 10 Akoueflow indicators for a token row or dict
    if metrics_data is missing, empty, or lacks an indicators dict.
    Calculates directional volume split using price-change performance (5m & 1h).
    """
    if not isinstance(item, dict):
        return None

    # 1. If valid metrics_data with indicators already exists, return it
    m_data = item.get('metrics_data')
    if isinstance(m_data, str) and m_data.strip():
        try:
            m_data = json.loads(m_data)
            if isinstance(m_data, str) and m_data.strip():
                m_data = json.loads(m_data)
        except Exception:
            m_data = None

    if isinstance(m_data, dict):
        inds = m_data.get('indicators')
        if isinstance(inds, dict) and inds.get('net_buy_pressure') is not None and m_data.get('top10_concentration_pct') not in (25.0, 25):
            return m_data

    # 2. Extract numeric inputs
    try:
        p5m = float(item.get('perf_5m') if item.get('perf_5m') is not None else 0.0)
    except (ValueError, TypeError):
        p5m = 0.0
    try:
        v5m = float(item.get('volume_5m') if item.get('volume_5m') is not None else 0.0)
    except (ValueError, TypeError):
        v5m = 0.0
    try:
        p1h = float(item.get('perf_1h') if item.get('perf_1h') is not None else 0.0)
    except (ValueError, TypeError):
        p1h = 0.0
    try:
        v1h = float(item.get('volume_1h') if item.get('volume_1h') is not None else 0.0)
    except (ValueError, TypeError):
        v1h = 0.0
    try:
        v24 = float(item.get('volume_24h') if item.get('volume_24h') is not None else 0.0)
    except (ValueError, TypeError):
        v24 = 0.0
    try:
        liq = float(item.get('liquidity_usd') if item.get('liquidity_usd') is not None else 0.0)
    except (ValueError, TypeError):
        liq = 0.0
    try:
        mc = float(parse_mc_usd(item.get('market_cap')) or parse_mc_usd(item.get('entry_mc')) or 0.0)
    except (ValueError, TypeError):
        mc = 0.0

    # Fallback to formatted_message regex if volumes or perf were 0 in DB
    raw_msg = str(item.get('formatted_message') or '')
    if raw_msg:
        if v5m == 0.0:
            m_v5 = re.search(r'5m Volume:\s*\$?([\d,]+(?:\.\d+)?)([KMB]?)', raw_msg, re.I)
            if m_v5:
                n = float(m_v5.group(1).replace(',', ''))
                v5m = n * {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(m_v5.group(2).upper(), 1)
        if v1h == 0.0:
            m_v1 = re.search(r'1h Volume:\s*\$?([\d,]+(?:\.\d+)?)([KMB]?)', raw_msg, re.I)
            if m_v1:
                n = float(m_v1.group(1).replace(',', ''))
                v1h = n * {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(m_v1.group(2).upper(), 1)
        if v24 == 0.0:
            m_v24 = re.search(r'24h Volume:\s*\$?([\d,]+(?:\.\d+)?)([KMB]?)', raw_msg, re.I)
            if m_v24:
                n = float(m_v24.group(1).replace(',', ''))
                v24 = n * {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(m_v24.group(2).upper(), 1)
        if liq == 0.0:
            m_liq = re.search(r'Liquidity:\s*\$?([\d,]+(?:\.\d+)?)([KMB]?)', raw_msg, re.I)
            if m_liq:
                n = float(m_liq.group(1).replace(',', ''))
                liq = n * {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(m_liq.group(2).upper(), 1)

    # 3. Directional buy/sell estimation based on price change
    buy_share_5m = 0.50 if p5m == 0 else max(0.05, min(0.95, 0.50 + (p5m / 100.0) * 0.40))
    b5m = round(v5m * buy_share_5m, 2)
    s5m = round(v5m * (1.0 - buy_share_5m), 2)

    buy_share_1h = 0.50 if p1h == 0 else max(0.05, min(0.95, 0.50 + (p1h / 100.0) * 0.40))
    b1h = round(v1h * buy_share_1h, 2)
    s1h = round(v1h * (1.0 - buy_share_1h), 2)

    base_metrics = dict(m_data) if isinstance(m_data, dict) else {}
    if base_metrics.get('top10_concentration_pct') in (25.0, 25):
        base_metrics['top10_concentration_pct'] = None
    if not base_metrics.get('volume_buy_5m_usd') and not base_metrics.get('volume_sell_5m_usd'):
        base_metrics['volume_buy_5m_usd'] = b5m
        base_metrics['volume_sell_5m_usd'] = s5m
    if not base_metrics.get('volume_buy_1h_usd') and not base_metrics.get('volume_sell_1h_usd'):
        base_metrics['volume_buy_1h_usd'] = b1h
        base_metrics['volume_sell_1h_usd'] = s1h
    if not base_metrics.get('volume_source'):
        base_metrics['volume_source'] = 'synthetic_perf_est'

    # Estimated slippage if missing
    if 'slippage' not in base_metrics or not base_metrics.get('slippage'):
        est_slip = round(max(0.2, min(15.0, 15000.0 / liq)), 2) if liq > 1000 else 5.0
        base_metrics['slippage'] = {
            'test_sol': 1.0,
            'buy_slippage_pct': est_slip,
            'sell_slippage_pct': est_slip,
            'sellable': True,
            'error': None
        }

    # Holder concentration: extract from message if available
    if base_metrics.get('top10_concentration_pct') is None and raw_msg:
        m_hc = re.search(r'(?:Top 10 Concentration|Holder Concentration):\s*([\d.]+)', raw_msg, re.I)
        if m_hc:
            try:
                base_metrics['top10_concentration_pct'] = float(m_hc.group(1))
            except Exception:
                pass

    # Estimated unique buyers/sellers if missing
    if not base_metrics.get('unique_buyers') and not base_metrics.get('unique_sellers'):
        b_est = base_metrics.get('volume_buy_5m_usd') or b5m
        s_est = base_metrics.get('volume_sell_5m_usd') or s5m
        base_metrics['unique_buyers'] = max(1, int(b_est / 150.0)) if b_est > 0 else 1
        base_metrics['unique_sellers'] = max(1, int(s_est / 150.0)) if s_est > 0 else 1

    computed_inds = compute_akoueflow_indicators(base_metrics, None, market_cap=mc, liquidity_usd=liq, volume_24h=v24)
    base_metrics['indicators'] = computed_inds
    return base_metrics

def hydrate_wallet_payments_indicators():
    """Populate metrics_data with synthesized Akoueflow indicators for any wallet_payments rows missing them."""
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT id, perf_5m, perf_1h, perf_6h, perf_24h, liquidity_usd,
                       volume_5m, volume_1h, volume_6h, volume_24h, market_cap, formatted_message
                FROM wallet_payments
                WHERE metrics_data IS NULL OR metrics_data = '' OR metrics_data = 'null'
            """)
            rows = cursor.fetchall()
            if not rows:
                return 0
            updates = []
            for r in rows:
                row_dict = {
                    'perf_5m': r[1],
                    'perf_1h': r[2],
                    'perf_6h': r[3],
                    'perf_24h': r[4],
                    'liquidity_usd': r[5],
                    'volume_5m': r[6],
                    'volume_1h': r[7],
                    'volume_6h': r[8],
                    'volume_24h': r[9],
                    'market_cap': r[10],
                    'formatted_message': r[11]
                }
                syn = synthesize_token_indicators(row_dict)
                if syn:
                    updates.append((json.dumps(syn), r[0]))
            if updates:
                cursor.executemany("UPDATE wallet_payments SET metrics_data = ? WHERE id = ?", updates)
                conn.commit()
                debug_log(f"[HYDRATE] Hydrated {len(updates)} wallet_payments rows with Akoueflow indicators")
            return len(updates)
        except Exception as e:
            debug_log(f"[HYDRATE] Notice during wallet_payments indicator hydration: {e}")
            return 0

# ---------------------------------------------------------------------------
# Token launch metrics: pump.fun bonding-curve progress and dev (creator)
# history. Values come from metrics_data when the token was enriched, and fall
# back to the stored Telegram message text so older records stay filterable.
# ---------------------------------------------------------------------------
TOKEN_LAUNCH_METRIC_KEYS = ('bonding_curve_pct', 'dev_prior_tokens')

_MIGRATED_YES_RE = re.compile(r'Migrated:\s*Yes', re.IGNORECASE)
_BONDING_CURVE_RE = re.compile(r'Bonding Curve:\s*(\d+(?:\.\d+)?)%', re.IGNORECASE)
_DEV_PRIOR_RE = re.compile(r'\((\d+)\s+prior tokens\)', re.IGNORECASE)


def _as_metrics_dict(metrics_data_raw):
    data = metrics_data_raw
    for _ in range(2):
        if isinstance(data, str) and data.strip():
            try:
                data = json.loads(data)
            except Exception:
                return {}
    return data if isinstance(data, dict) else {}


def _is_migrated(formatted_message=None, migration_status=None):
    if migration_status and str(migration_status).strip().lower().startswith('yes'):
        return True
    return bool(formatted_message and _MIGRATED_YES_RE.search(str(formatted_message)))


def compute_bonding_curve_pct(metrics_data_raw=None, formatted_message=None, migration_status=None):
    """Pump.fun bonding-curve progress in % (100 = graduated/migrated), or None if unknown."""
    data = _as_metrics_dict(metrics_data_raw)
    stored = data.get('bonding_curve_pct')
    if stored is None:
        stored = (data.get('indicators') or {}).get('bonding_curve_pct') if isinstance(data.get('indicators'), dict) else None
    if stored is not None:
        try:
            return float(stored)
        except (TypeError, ValueError):
            pass

    if _is_migrated(formatted_message, migration_status):
        return 100.0

    bc = data.get('bonding_curve')
    if isinstance(bc, dict) and bc:
        if bc.get('is_completed'):
            return 100.0
        if bc.get('progress_pct') is not None:
            try:
                return float(bc['progress_pct'])
            except (TypeError, ValueError):
                pass

    if formatted_message:
        m = _BONDING_CURVE_RE.search(str(formatted_message))
        if m:
            return float(m.group(1))
    return None


def compute_dev_prior_tokens(metrics_data_raw=None, formatted_message=None):
    """Number of other tokens the creator wallet has launched, or None if unknown."""
    data = _as_metrics_dict(metrics_data_raw)
    stored = data.get('dev_prior_tokens')
    if stored is None:
        stored = (data.get('indicators') or {}).get('dev_prior_tokens') if isinstance(data.get('indicators'), dict) else None
    if stored is not None:
        try:
            return float(stored)
        except (TypeError, ValueError):
            pass

    hist = data.get('creator_wallet_history')
    if isinstance(hist, dict) and hist.get('total_created') is not None:
        try:
            return float(hist['total_created'])
        except (TypeError, ValueError):
            pass

    if formatted_message:
        m = _DEV_PRIOR_RE.search(str(formatted_message))
        if m:
            return float(m.group(1))
    return None


def compute_token_launch_metric(metrics_data_raw, formatted_message, key, migration_status=None):
    key = str(key or '').strip().lower()
    if key == 'bonding_curve_pct':
        return compute_bonding_curve_pct(metrics_data_raw, formatted_message, migration_status)
    if key == 'dev_prior_tokens':
        return compute_dev_prior_tokens(metrics_data_raw, formatted_message)
    return None


def sqlite_token_metric(metrics_data_raw, formatted_message, key):
    """SQLite function: get_token_metric(metrics_data, formatted_message, 'bonding_curve_pct')."""
    try:
        return compute_token_launch_metric(metrics_data_raw, formatted_message, key)
    except Exception:
        return None


def extract_indicator_val(metrics_data_raw, indicator_name):
    if not metrics_data_raw or not indicator_name:
        return None
    if str(indicator_name).strip().lower() in TOKEN_LAUNCH_METRIC_KEYS:
        return compute_token_launch_metric(metrics_data_raw, None, indicator_name)
    try:
        data = json.loads(metrics_data_raw) if isinstance(metrics_data_raw, str) else metrics_data_raw
        if isinstance(data, str) and data.strip():
            try:
                data = json.loads(data)
            except Exception:
                pass
        if not isinstance(data, dict):
            return None
        inds = data.get('indicators')
        key = str(indicator_name).strip().lower()
        if inds and isinstance(inds, dict) and key in inds and inds[key] is not None:
            return float(inds[key])
        # Fallback calculations if metrics_data exists but indicators dict was missing
        b = float(data.get('volume_buy_5m_usd') or 0.0)
        s = float(data.get('volume_sell_5m_usd') or 0.0)
        tot = b + s
        if key == 'net_buy_pressure':
            return b - s
        elif key == 'buy_dominance_pct':
            return round((b / tot) * 100.0, 1) if tot > 0 else 50.0
        elif key == 'buy_pressure_accel':
            r5 = (b / tot) if tot > 0 else 0.5
            b1 = float(data.get('volume_buy_1h_usd') or 0.0)
            s1 = float(data.get('volume_sell_1h_usd') or 0.0)
            tot1 = b1 + s1
            r1 = (b1 / tot1) if tot1 > 0 else 0.5
            return round((r5 - r1) * 100.0, 1)
        elif key == 'buy_vol_accel':
            b1 = float(data.get('volume_buy_1h_usd') or 0.0)
            return round((b * 12.0) / b1, 2) if b1 > 0 else (12.0 if b > 0 else 1.0)
        elif key == 'sell_vol_accel':
            s1 = float(data.get('volume_sell_1h_usd') or 0.0)
            return round((s * 12.0) / s1, 2) if s1 > 0 else (12.0 if s > 0 else 1.0)
        elif key == 'net_buy_to_liq':
            liq = float(data.get('liquidity_usd') or (data.get('liquidity', {}).get('usd') if isinstance(data.get('liquidity'), dict) else 0.0) or 0.0)
            return round(((b - s) / liq) * 100.0, 2) if liq > 0 else 0.0
        elif key == 'vol_to_mc':
            mc = float(data.get('market_cap') or data.get('fdv') or 0.0)
            v24 = float(data.get('volume_24h') or (data.get('volume', {}).get('h24') if isinstance(data.get('volume'), dict) else 0.0) or 0.0)
            return round(v24 / mc, 2) if mc > 0 else None
        elif key == 'vol_to_liq':
            liq = float(data.get('liquidity_usd') or (data.get('liquidity', {}).get('usd') if isinstance(data.get('liquidity'), dict) else 0.0) or 0.0)
            v24 = float(data.get('volume_24h') or (data.get('volume', {}).get('h24') if isinstance(data.get('volume'), dict) else 0.0) or 0.0)
            return round(v24 / liq, 2) if liq > 0 else None
        elif key == 'buyer_seller_ratio':
            ub = float(data.get('unique_buyers') or 0.0)
            us = float(data.get('unique_sellers') or 0.0)
            return round(ub / us, 2) if us > 0 else (float(ub) if ub > 0 else 1.0)
        elif key == 'holder_concentration':
            hc = data.get('top10_concentration_pct')
            return float(hc) if hc is not None else None
        elif key == 'round_trip_cost':
            slip = data.get('slippage') or {}
            bs = slip.get('buy_slippage_pct')
            ss = slip.get('sell_slippage_pct')
            if bs is not None and ss is not None:
                return round(float(bs) + float(ss) + 0.5, 2)
            elif bs is not None:
                return round(float(bs) * 2.0 + 0.5, 2)
    except Exception:
        return None
    return None

def get_token_indicator_value(token, indicator_key):
    """Extract numeric indicator value from token dict using indicators dict, metrics_data, or formatted_message regex."""
    if not token or not indicator_key:
        return None
    key = str(indicator_key).strip().lower()

    if key in TOKEN_LAUNCH_METRIC_KEYS:
        # Prefer stored metrics, then message text / migration status.
        return compute_token_launch_metric(
            token.get('metrics_data') or {'indicators': token.get('indicators') or {}},
            token.get('formatted_message'),
            key,
            migration_status=token.get('migration_status'),
        )

    # 1. From precomputed indicators dict
    inds = token.get('indicators')
    if isinstance(inds, dict) and inds.get(key) is not None:
        try:
            return float(inds[key])
        except (ValueError, TypeError):
            pass

    # 2. From metrics_data via extract_indicator_val
    m_data = token.get('metrics_data')
    if m_data:
        val = extract_indicator_val(m_data, key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass

    # 3. Fallback extraction from formatted_message via regex
    text = str(token.get('formatted_message') or '')
    if text:
        if key == 'net_buy_pressure':
            m = re.search(r'Net Buy Pressure:\s*([+\-]?)\$?([\d,.]+)([KMB]?)', text, re.I)
            if m:
                s = -1.0 if m.group(1) == '-' else 1.0
                n = float(m.group(2).replace(',', ''))
                sfx = m.group(3).upper()
                return s * n * {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(sfx, 1)
        elif key == 'buy_dominance_pct':
            m = re.search(r'\((\d+(?:\.\d+)?)%\s*Buy Dom\)', text, re.I)
            if m: return float(m.group(1))
        elif key == 'buy_pressure_accel':
            m = re.search(r'Buy Press Accel:\s*([+\-]?\d+(?:\.\d+)?)%', text, re.I)
            if m: return float(m.group(1))
        elif key == 'buy_vol_accel':
            m = re.search(r'Vol Accel:\s*Buy\s*(\d+(?:\.\d+)?)x', text, re.I)
            if m: return float(m.group(1))
        elif key == 'sell_vol_accel':
            m = re.search(r'Vol Accel:.*?Sell\s*(\d+(?:\.\d+)?)x', text, re.I)
            if m: return float(m.group(1))
        elif key == 'net_buy_to_liq':
            m = re.search(r'Net Buy / Liq:\s*([+\-]?\d+(?:\.\d+)?)%', text, re.I)
            if m: return float(m.group(1))
        elif key == 'vol_to_mc':
            m = re.search(r'Vol/MC:\s*(\d+(?:\.\d+)?)x', text, re.I)
            if m: return float(m.group(1))
        elif key == 'vol_to_liq':
            m = re.search(r'Vol/Liq:\s*(\d+(?:\.\d+)?)x', text, re.I)
            if m: return float(m.group(1))
        elif key == 'buyer_seller_ratio':
            m = re.search(r'Buyer/Seller Ratio:\s*(\d+(?:\.\d+)?)', text, re.I)
            if m: return float(m.group(1))
        elif key == 'holder_concentration':
            m = re.search(r'Holder Concentration:\s*(\d+(?:\.\d+)?)%', text, re.I)
            if m: return float(m.group(1))
        elif key == 'round_trip_cost':
            m = re.search(r'Round-Trip Cost:\s*~?(\d+(?:\.\d+)?)%', text, re.I)
            if m: return float(m.group(1))

    # 4. Fallback synthesis from token row metrics
    try:
        syn = synthesize_token_indicators(token)
        if syn and isinstance(syn, dict):
            syn_inds = syn.get('indicators')
            if isinstance(syn_inds, dict) and syn_inds.get(key) is not None:
                return float(syn_inds[key])
    except Exception:
        pass

    return None


INDICATOR_PRESET_LABELS = {
    'strong_bullish': 'Strong Bullish Flow',
    'buy_surge': 'Buy Volume Surge',
    'high_breadth': 'High Wallet Breadth',
    'heavy_turnover': 'Heavy Liquidity Turnover',
    'low_risk': 'Low Supply & Exit Risk',
    'heavy_selling': 'Heavy Selling Pressure',
}


def indicator_preset_passes(preset, token):
    """True if ``token`` satisfies the named Akoueflow indicator preset.

    Shared by the Outcomes Lab and live workflow rules so both apply the same thresholds.
    Unknown presets pass.
    """
    preset = str(preset or '').strip().lower()
    v = lambda k: get_token_indicator_value(token, k)
    if preset == 'strong_bullish':
        nb, bd = v('net_buy_pressure'), v('buy_dominance_pct')
        return nb is not None and nb >= 5000 and bd is not None and bd >= 60.0
    if preset == 'buy_surge':
        ba, bd = v('buy_vol_accel'), v('buy_dominance_pct')
        return ba is not None and ba >= 2.0 and bd is not None and bd >= 55.0
    if preset == 'high_breadth':
        bs = v('buyer_seller_ratio')
        return bs is not None and bs >= 1.5
    if preset == 'heavy_turnover':
        bl = v('net_buy_to_liq')
        return bl is not None and bl >= 5.0
    if preset == 'low_risk':
        hc, rt = v('holder_concentration'), v('round_trip_cost')
        return hc is not None and hc <= 35.0 and rt is not None and rt <= 6.0
    if preset == 'heavy_selling':
        nb, bd = v('net_buy_pressure'), v('buy_dominance_pct')
        return (nb is not None and nb < 0) or (bd is not None and bd < 40.0)
    return True
