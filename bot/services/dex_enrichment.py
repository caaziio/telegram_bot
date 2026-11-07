"""DexScreener pair metrics, migration/rugcheck checks and Akoueflow indicators."""

import asyncio
import os
import struct
import base64
import time
import requests

from bot.config import debug_log
from bot.db.connection import get_settings
from bot.services.helius import helius_request_with_failover
from bot.services.tokens import get_token_price_usd

def get_sorted_trading_pairs(pairs):
    """
    Sort pairs so the most active, liquid trading pool is first.
    Prioritizes pairs that report non-null price changes, then highest liquidity USD, then 24h volume.
    """
    if not pairs:
        return []
    def pair_score(p):
        liq = 0.0
        vol = 0.0
        try:
            liq = float((p.get('liquidity') or {}).get('usd') or 0.0)
        except (ValueError, TypeError):
            pass
        try:
            vol = float((p.get('volume') or {}).get('h24') or 0.0)
        except (ValueError, TypeError):
            pass
        pc = p.get('priceChange') or {}
        has_pc = 1 if isinstance(pc, dict) and any(pc.get(k) is not None for k in ('m5', 'h1', 'h6', 'h24')) else 0
        return (has_pc, liq, vol)
    return sorted(pairs, key=pair_score, reverse=True)

def extract_best_price_change(pairs):
    """
    Extract best m5, h1, h6, h24 price change percentages across all pairs.
    Prioritizes the most active, liquid trading pairs, safely handling nulls and empty dicts.
    """
    if not pairs:
        return 0.0, 0.0, 0.0, 0.0

    sorted_pairs = get_sorted_trading_pairs(pairs)

    m5, h1, h6, h24 = None, None, None, None
    for p in sorted_pairs:
        pc = p.get('priceChange')
        if not pc or not isinstance(pc, dict):
            continue

        if m5 is None and pc.get('m5') is not None:
            try: m5 = float(pc['m5'])
            except (ValueError, TypeError): pass
        if h1 is None and pc.get('h1') is not None:
            try: h1 = float(pc['h1'])
            except (ValueError, TypeError): pass
        if h6 is None and pc.get('h6') is not None:
            try: h6 = float(pc['h6'])
            except (ValueError, TypeError): pass
        if h24 is None and pc.get('h24') is not None:
            try: h24 = float(pc['h24'])
            except (ValueError, TypeError): pass

    return (
        m5 if m5 is not None else 0.0,
        h1 if h1 is not None else 0.0,
        h6 if h6 is not None else 0.0,
        h24 if h24 is not None else 0.0
    )

def extract_pair_market_metrics(pair):
    """Return liquidity and rolling USD volume for the selected Dexscreener pair."""
    pair = pair or {}
    liquidity = pair.get('liquidity') or {}
    volume = pair.get('volume') or {}

    def number(source, key):
        try:
            return float(source.get(key) or 0.0)
        except (TypeError, ValueError, AttributeError):
            return 0.0

    return (
        number(liquidity, 'usd'),
        number(volume, 'm5'),
        number(volume, 'h1'),
        number(volume, 'h6'),
        number(volume, 'h24'),
    )

def format_usd_metric(value):
    try:
        value = float(value or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:.2f}"

async def check_token_migration(ca, pairs):
    """
    Checks if a token has migrated and identifies where it is right now.
    Returns:
      is_migrated (bool): True if originated on bonding curve and graduated to a standard DEX
      migration_detail (str): e.g. "Yes (to Raydium)", "No (Still on pump.fun)", or "No (Direct Launch on Raydium)"
      current_location (str): e.g. "Raydium", "pump.fun", "Meteora", etc.
    """
    # 1. Determine if token originated on a bonding curve platform
    is_bonding_curve = False
    origin_platform = "pump.fun"

    if ca.lower().endswith("pump"):
        is_bonding_curve = True
        origin_platform = "pump.fun"
    else:
        for p in pairs:
            d_id = p.get("dexId", "").lower()
            if d_id == "pumpfun":
                is_bonding_curve = True
                origin_platform = "pump.fun"
                break
            elif d_id == "moonshot":
                is_bonding_curve = True
                origin_platform = "Moonshot"
                break
            elif d_id == "pumpswap":
                is_bonding_curve = True
                origin_platform = "Pumpswap"
                break

    # Format nice display names
    dex_names = {
        "pumpfun": "pump.fun",
        "moonshot": "Moonshot",
        "pumpswap": "Pumpswap",
        "raydium": "Raydium",
        "meteora": "Meteora",
        "orca": "Orca",
        "fluxbeam": "Fluxbeam",
        "phoenix": "Phoenix",
        "openbook": "Openbook",
        "lifinity": "Lifinity",
        "pancakeswap": "Pancakeswap"
    }

    # 2. Determine current location (primary trading exchange)
    sorted_pairs = get_sorted_trading_pairs(pairs)
    if sorted_pairs:
        primary_dex = sorted_pairs[0].get("dexId", "").lower()
        current_location = dex_names.get(primary_dex, primary_dex.capitalize())
    else:
        primary_dex = ""
        current_location = "Unknown"

    # 3. Check for pump.fun bonding curve completion on-chain to handle DexScreener lag.
    is_completed_on_chain = False
    if is_bonding_curve and origin_platform == "pump.fun":
        bonding_curve_address = None
        for p in pairs:
            if p.get("dexId", "").lower() == "pumpfun":
                bonding_curve_address = p.get("pairAddress")
                break

        if bonding_curve_address:
            payload = {
                "jsonrpc": "2.0",
                "id": "get-bonding-curve",
                "method": "getAccountInfo",
                "params": [
                    bonding_curve_address,
                    {
                        "encoding": "base64"
                    }
                ]
            }
            headers = {"Content-Type": "application/json"}
            try:
                r = await asyncio.to_thread(helius_request_with_failover, "POST", "https://mainnet.helius-rpc.com/?api-key={key}", json=payload, headers=headers, timeout=5)
                if r and r.status_code == 200:
                    res = r.json()
                    value = res.get("result", {}).get("value")
                    if value and "data" in value:
                        data_b64 = value["data"][0]
                        import base64
                        data_bytes = base64.b64decode(data_b64)
                        if len(data_bytes) >= 49:
                            # complete is a bool (1 byte) at offset 48 (0x30)
                            is_completed_on_chain = (data_bytes[48] == 1)
                            if is_completed_on_chain:
                                debug_log(f"[MIGRATION] On-chain check confirmed migration for {ca}.")
            except Exception as e:
                print(f"[MIGRATION] Error verifying bonding curve on-chain: {e}", flush=True)

    # 4. Determine migration status and details
    if is_completed_on_chain:
        is_migrated = True
        current_location = "Raydium"
        migration_detail = "Yes (to Raydium)"
    elif primary_dex == "pumpfun":
        is_migrated = False
        migration_detail = "No (Still on pump.fun)"
    elif primary_dex == "":
        is_migrated = False
        migration_detail = "No (Unknown)"
    else:
        is_migrated = True
        migration_detail = f"Yes (to {current_location})"

    return is_migrated, migration_detail, current_location

_rugcheck_cache = {}

def fetch_token_rugcheck_sync(ca):
    """Fetch live top 10 non-LP holder concentration & creator security metrics from RugCheck API."""
    if not ca:
        return {}
    cached = _rugcheck_cache.get(ca)
    if cached and (time.time() - cached[0] < 600):
        return cached[1]
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{ca}/report", headers=headers, timeout=6)
        if r.status_code == 200:
            d = r.json()
            holders = d.get('topHolders') or []
            top10 = sum(float(h.get('pct') or 0.0) for h in holders[:10])
            creator = d.get('creator')
            c_tokens = d.get('creatorTokens') or []
            res = {
                'top10_concentration_pct': round(top10, 2) if holders else None,
                'creator_wallet': creator,
                'creator_wallet_history': {
                    'total_created': len(c_tokens),
                    'rugged': d.get('rugged', False),
                    'risks_count': len(d.get('risks') or [])
                },
                'freeze_authority': d.get('freezeAuthority'),
                'mint_authority': d.get('mintAuthority')
            }
            _rugcheck_cache[ca] = (time.time(), res)
            return res
    except Exception:
        pass
    _rugcheck_cache[ca] = (time.time(), {})
    return {}

async def enrich_dex_validated_token(ca, pairs, helius_api_key=None):
    """
    Collect the 12 priority metrics exclusively for DEX-validated tokens:
    1. 5m buy volume USD
    2. 5m sell volume USD
    3. 1h buy volume USD
    4. 1h sell volume USD
    5. Unique buyers
    6. Unique sellers
    7. Holder concentration (Top 10 non-LP holders %)
    8. Token mint creation time
    9. Creator wallet history
    10. Bonding-curve reserves (for pump.fun)
    11. Estimated buy/sell slippage (simulated 1 SOL)
    12. Mint / freeze authority
    """
    if not ca:
        return {}

    settings = get_settings()
    helius_key = helius_api_key or settings.get('helius_api_key') or os.environ.get('HELIUS_API_KEY')

    metrics = {
        'volume_buy_5m_usd': 0.0,
        'volume_sell_5m_usd': 0.0,
        'volume_buy_1h_usd': 0.0,
        'volume_sell_1h_usd': 0.0,
        'unique_buyers': 0,
        'unique_sellers': 0,
        'volume_source': 'dexscreener_est',
        'top10_concentration_pct': None,
        'token_mint_creation_time': None,
        'creator_wallet': None,
        'creator_wallet_history': None,
        'bonding_curve': None,
        'slippage': {
            'test_sol': 1.0,
            'buy_slippage_pct': None,
            'sell_slippage_pct': None,
            'sellable': True,
            'error': None
        },
        'authorities': {
            'mint_authority': None,
            'freeze_authority': None,
            'is_mint_renounced': None,
            'is_freeze_renounced': None
        }
    }

    # 1. Creation time from pairs
    creation_times = [p.get("pairCreatedAt") for p in pairs if p.get("pairCreatedAt")]
    if creation_times:
        oldest_ts = min(creation_times)
        metrics['token_mint_creation_time'] = oldest_ts

    # Subroutine: Helius account info (authorities & supply)
    def _fetch_helius_auth():
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [ca, {"encoding": "jsonParsed"}]
            }
            res = helius_request_with_failover("POST", "https://mainnet.helius-rpc.com/?api-key={key}", json=payload, timeout=6)
            if res and res.status_code == 200:
                val = res.json().get('result', {}).get('value')
                if val and 'data' in val and isinstance(val['data'], dict):
                    info = val['data'].get('parsed', {}).get('info', {})
                    m_auth = info.get('mintAuthority')
                    f_auth = info.get('freezeAuthority')
                    return {
                        'mint_authority': m_auth,
                        'freeze_authority': f_auth,
                        'is_mint_renounced': m_auth is None,
                        'is_freeze_renounced': f_auth is None,
                        'supply': info.get('supply'),
                        'decimals': info.get('decimals')
                    }
        except Exception:
            pass
        return {}

    # Subroutine: Pump.fun bonding curve on-chain reserves
    def _fetch_bonding_curve():
        bc_address = None
        for p in pairs:
            if p.get("dexId", "").lower() == "pumpfun":
                bc_address = p.get("pairAddress")
                break
        if not bc_address and ca.lower().endswith("pump"):
            for p in pairs:
                if p.get("pairAddress"):
                    bc_address = p.get("pairAddress")
                    break

        if not bc_address:
            return None

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "bonding-curve",
                "method": "getAccountInfo",
                "params": [bc_address, {"encoding": "base64"}]
            }
            res = helius_request_with_failover("POST", "https://mainnet.helius-rpc.com/?api-key={key}", json=payload, timeout=6)
            if res and res.status_code == 200:
                val = res.json().get('result', {}).get('value')
                if val and val.get('owner') == '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P' and 'data' in val:
                    data_bytes = base64.b64decode(val['data'][0])
                    if len(data_bytes) >= 49:
                        v_tok = struct.unpack('<Q', data_bytes[8:16])[0] / 1e6
                        v_sol = struct.unpack('<Q', data_bytes[16:24])[0] / 1e9
                        r_tok = struct.unpack('<Q', data_bytes[24:32])[0] / 1e6
                        r_sol = struct.unpack('<Q', data_bytes[32:40])[0] / 1e9
                        comp = (data_bytes[48] == 1)
                        # A completed (graduated) curve has its SOL withdrawn, so report 100%
                        prog = 100.0 if comp else round(min(100.0, (r_sol / 85.0) * 100), 2)
                        return {
                            'is_pump': True,
                            'bonding_curve_address': bc_address,
                            'virtual_sol_reserves': round(v_sol, 4),
                            'virtual_token_reserves': round(v_tok, 2),
                            'real_sol_reserves': round(r_sol, 4),
                            'real_token_reserves': round(r_tok, 2),
                            'is_completed': comp,
                            'progress_pct': prog
                        }
        except Exception:
            pass
        return None

    # Subroutine: Jupiter simulated 1 SOL swap slippage (Buy & Sell)
    def _fetch_jupiter_slippage():
        sol_mint = 'So11111111111111111111111111111111111111112'
        amt_lamports = 1_000_000_000  # 1 SOL
        buy_url = f"https://api.jup.ag/swap/v1/quote?inputMint={sol_mint}&outputMint={ca}&amount={amt_lamports}&slippageBps=500"
        try:
            r_buy = requests.get(buy_url, timeout=5)
            if r_buy.status_code == 200:
                b_data = r_buy.json()
                buy_impact = float(b_data.get('priceImpactPct') or 0.0)
                out_tokens = int(b_data.get('outAmount') or 0)
                sell_impact = None
                sellable = False
                if out_tokens > 0:
                    sell_url = f"https://api.jup.ag/swap/v1/quote?inputMint={ca}&outputMint={sol_mint}&amount={out_tokens}&slippageBps=500"
                    r_sell = requests.get(sell_url, timeout=5)
                    if r_sell.status_code == 200:
                        s_data = r_sell.json()
                        sell_impact = float(s_data.get('priceImpactPct') or 0.0)
                        sellable = True
                    else:
                        sellable = False
                return {
                    'test_sol': 1.0,
                    'buy_slippage_pct': round(buy_impact, 2),
                    'sell_slippage_pct': round(sell_impact, 2) if sell_impact is not None else None,
                    'sellable': sellable,
                    'error': None
                }
            else:
                return {
                    'test_sol': 1.0,
                    'buy_slippage_pct': None,
                    'sell_slippage_pct': None,
                    'sellable': False,
                    'error': 'No standard DEX route on Jupiter'
                }
        except Exception as e:
            return {
                'test_sol': 1.0,
                'buy_slippage_pct': None,
                'sell_slippage_pct': None,
                'sellable': False,
                'error': str(e)
            }

    # Subroutine: RugCheck for top 10 concentration & creator history
    def _fetch_rugcheck():
        return fetch_token_rugcheck_sync(ca)

    # Subroutine: Helius enhanced transactions for unique buyers, sellers & on-chain buy/sell volume
    def _fetch_helius_trades():
        try:
            url_tmpl = f"https://api.helius.xyz/v0/addresses/{ca}/transactions?api-key={{key}}&limit=100"
            r = helius_request_with_failover("GET", url_tmpl, timeout=6)
            if not r or r.status_code != 200:
                return None
            txs = r.json()
            if not isinstance(txs, list) or not txs:
                return None

            now = int(time.time())
            sol_mint = 'So11111111111111111111111111111111111111112'
            buyers_5m, sellers_5m = set(), set()
            buyers_1h, sellers_1h = set(), set()
            buy_sol_5m, sell_sol_5m = 0.0, 0.0
            buy_sol_1h, sell_sol_1h = 0.0, 0.0

            for tx in txs:
                ts = tx.get('timestamp') or 0
                age = now - ts
                if age > 3600 or age < 0:
                    continue

                payer = tx.get('feePayer')
                token_transfers = tx.get('tokenTransfers') or []
                native_transfers = tx.get('nativeTransfers') or []

                is_buy, is_sell = False, False
                for tt in token_transfers:
                    if tt.get('mint') == ca:
                        u_to = tt.get('toUserAccount')
                        u_from = tt.get('fromUserAccount')
                        if payer and payer == u_to:
                            is_buy = True
                        elif payer and payer == u_from:
                            is_sell = True

                sol_amt = 0.0
                for tt in token_transfers:
                    if tt.get('mint') == sol_mint:
                        sol_amt = max(sol_amt, float(tt.get('tokenAmount') or 0.0))
                if sol_amt == 0.0:
                    for nt in native_transfers:
                        sol_amt = max(sol_amt, (nt.get('amount') or 0) / 1e9)

                if is_buy:
                    buyers_1h.add(payer)
                    buy_sol_1h += sol_amt
                    if age <= 300:
                        buyers_5m.add(payer)
                        buy_sol_5m += sol_amt
                elif is_sell:
                    sellers_1h.add(payer)
                    sell_sol_1h += sol_amt
                    if age <= 300:
                        sellers_5m.add(payer)
                        sell_sol_5m += sol_amt

            return {
                'buyers_5m': len(buyers_5m),
                'sellers_5m': len(sellers_5m),
                'buyers_1h': len(buyers_1h),
                'sellers_1h': len(sellers_1h),
                'buy_sol_5m': buy_sol_5m,
                'sell_sol_5m': sell_sol_5m,
                'buy_sol_1h': buy_sol_1h,
                'sell_sol_1h': sell_sol_1h,
            }
        except Exception:
            return None

    # Execute network workers in parallel
    h_auth, bc_data, jup_slip, rc_data, h_trades = await asyncio.gather(
        asyncio.to_thread(_fetch_helius_auth),
        asyncio.to_thread(_fetch_bonding_curve),
        asyncio.to_thread(_fetch_jupiter_slippage),
        asyncio.to_thread(_fetch_rugcheck),
        asyncio.to_thread(_fetch_helius_trades),
        return_exceptions=True
    )

    # 1. Fill Authorities
    if isinstance(h_auth, dict) and h_auth and ('mint_authority' in h_auth or 'freeze_authority' in h_auth):
        metrics['authorities'] = h_auth
    elif isinstance(rc_data, dict) and ('mint_authority' in rc_data or 'freeze_authority' in rc_data):
        m_auth = rc_data.get('mint_authority')
        f_auth = rc_data.get('freeze_authority')
        metrics['authorities'] = {
            'mint_authority': m_auth,
            'freeze_authority': f_auth,
            'is_mint_renounced': m_auth is None,
            'is_freeze_renounced': f_auth is None
        }

    # 2. Fill Bonding Curve
    if isinstance(bc_data, dict) and bc_data:
        metrics['bonding_curve'] = bc_data

    # 3. Fill Slippage
    if isinstance(jup_slip, dict) and jup_slip:
        metrics['slippage'] = jup_slip
        if not jup_slip.get('sellable') and metrics.get('bonding_curve'):
            v_sol = metrics['bonding_curve'].get('virtual_sol_reserves') or 30.0
            est_impact = round((1.0 / (v_sol + 1.0)) * 100, 2)
            metrics['slippage']['buy_slippage_pct'] = est_impact
            metrics['slippage']['sell_slippage_pct'] = est_impact
            metrics['slippage']['sellable'] = True
            metrics['slippage']['error'] = 'Pump.fun Bonding Curve'

    # 4. Fill RugCheck data
    if isinstance(rc_data, dict) and rc_data:
        if rc_data.get('top10_concentration_pct') is not None:
            metrics['top10_concentration_pct'] = rc_data['top10_concentration_pct']
        if rc_data.get('creator_wallet'):
            metrics['creator_wallet'] = rc_data['creator_wallet']
        if rc_data.get('creator_wallet_history'):
            metrics['creator_wallet_history'] = rc_data['creator_wallet_history']

    # 5. Fill Volume & Unique Traders (Helius on-chain swaps with DexScreener fallback)
    primary = pairs[0] if pairs else {}
    vol_5m = float(primary.get('volume', {}).get('m5') or 0.0)
    vol_1h = float(primary.get('volume', {}).get('h1') or 0.0)
    sol_usd = get_token_price_usd("SOL") or 150.0

    if isinstance(h_trades, dict) and h_trades and (h_trades['buyers_1h'] > 0 or h_trades['sellers_1h'] > 0):
        b_sol_5m = h_trades['buy_sol_5m']
        s_sol_5m = h_trades['sell_sol_5m']
        tot_sol_5m = b_sol_5m + s_sol_5m
        if tot_sol_5m > 0 and vol_5m > 0:
            metrics['volume_buy_5m_usd'] = round(vol_5m * (b_sol_5m / tot_sol_5m), 2)
            metrics['volume_sell_5m_usd'] = round(vol_5m * (s_sol_5m / tot_sol_5m), 2)
        else:
            metrics['volume_buy_5m_usd'] = round(b_sol_5m * sol_usd, 2)
            metrics['volume_sell_5m_usd'] = round(s_sol_5m * sol_usd, 2)

        b_sol_1h = h_trades['buy_sol_1h']
        s_sol_1h = h_trades['sell_sol_1h']
        tot_sol_1h = b_sol_1h + s_sol_1h
        if tot_sol_1h > 0 and vol_1h > 0:
            metrics['volume_buy_1h_usd'] = round(vol_1h * (b_sol_1h / tot_sol_1h), 2)
            metrics['volume_sell_1h_usd'] = round(vol_1h * (s_sol_1h / tot_sol_1h), 2)
        else:
            metrics['volume_buy_1h_usd'] = round(b_sol_1h * sol_usd, 2)
            metrics['volume_sell_1h_usd'] = round(s_sol_1h * sol_usd, 2)

        metrics['unique_buyers'] = h_trades['buyers_5m']
        metrics['unique_sellers'] = h_trades['sellers_5m']
        metrics['volume_source'] = 'helius_onchain'
    else:
        tx_5m = primary.get('txns', {}).get('m5') or {}
        b_5m = int(tx_5m.get('buys') or 0)
        s_5m = int(tx_5m.get('sells') or 0)
        tot_5m = b_5m + s_5m
        tx_1h = primary.get('txns', {}).get('h1') or {}
        b_1h = int(tx_1h.get('buys') or 0)
        s_1h = int(tx_1h.get('sells') or 0)
        tot_1h = b_1h + s_1h
        metrics['volume_buy_5m_usd'] = round(vol_5m * (b_5m / tot_5m), 2) if tot_5m > 0 else 0.0
        metrics['volume_sell_5m_usd'] = round(vol_5m * (s_5m / tot_5m), 2) if tot_5m > 0 else 0.0
        metrics['volume_buy_1h_usd'] = round(vol_1h * (b_1h / tot_1h), 2) if tot_1h > 0 else 0.0
        metrics['volume_sell_1h_usd'] = round(vol_1h * (s_1h / tot_1h), 2) if tot_1h > 0 else 0.0
        metrics['unique_buyers'] = b_5m
        metrics['unique_sellers'] = s_5m
        metrics['volume_source'] = 'dexscreener_est'

    # Compute the 10 Akoueflow trading indicators
    metrics['indicators'] = compute_akoueflow_indicators(metrics, pairs)

    return metrics

def compute_akoueflow_indicators(metrics, pairs=None, market_cap=None, liquidity_usd=None, volume_24h=None):
    """
    Computes the 10 Akoueflow trading indicators:
    1. Net Buy Pressure: Directional trading imbalance ($ net 5m and % buy dominance)
    2. Buy Pressure Acceleration: Changes in buying dominance (5m buy ratio - 1h buy ratio)
    3. Buy Volume Acceleration: Growth in buying activity ((Buy 5m * 12) / Buy 1h)
    4. Sell Volume Acceleration: Growth in selling activity ((Sell 5m * 12) / Sell 1h)
    5. Net Buy Volume / Liquidity: Directional turnover relative to pool size ((Buy 5m - Sell 5m) / Liq * 100)
    6. Volume / Market Cap: Trading intensity relative to valuation (24h Vol / Market Cap)
    7. Volume / Liquidity: Trading intensity relative to pool liquidity (24h Vol / Liquidity)
    8. Unique Buyer/Seller Ratio: Breadth of wallet participation (Buyers 5m / Sellers 5m)
    9. Holder Concentration: Supply concentration risk (Top 10 non-LP %)
    10. Executable Round-Trip Cost: Practical entry/exit feasibility (Buy Slip % + Sell Slip % + DEX fee ~0.5%)
    """
    if not isinstance(metrics, dict):
        metrics = {}
    primary = pairs[0] if pairs and isinstance(pairs, list) and len(pairs) > 0 else {}

    # 1. Volumes
    buy_5m = float(metrics.get('volume_buy_5m_usd') or 0.0)
    sell_5m = float(metrics.get('volume_sell_5m_usd') or 0.0)
    tot_5m = buy_5m + sell_5m
    net_buy_5m = round(buy_5m - sell_5m, 2)
    buy_dom_5m = round((buy_5m / tot_5m) * 100.0, 1) if tot_5m > 0 else 50.0

    buy_1h = float(metrics.get('volume_buy_1h_usd') or 0.0)
    sell_1h = float(metrics.get('volume_sell_1h_usd') or 0.0)
    tot_1h = buy_1h + sell_1h

    # 2. Buy Pressure Acceleration: Buy Ratio 5m - Buy Ratio 1h
    ratio_5m = (buy_5m / tot_5m) if tot_5m > 0 else 0.5
    ratio_1h = (buy_1h / tot_1h) if tot_1h > 0 else 0.5
    buy_pressure_accel = round((ratio_5m - ratio_1h) * 100.0, 1)

    # 3. Buy Volume Acceleration: (Buy 5m * 12) / Buy 1h
    if buy_1h > 0:
        buy_vol_accel = round((buy_5m * 12.0) / buy_1h, 2)
    elif buy_5m > 0:
        buy_vol_accel = 12.0
    else:
        buy_vol_accel = 1.0

    # 4. Sell Volume Acceleration: (Sell 5m * 12) / Sell 1h
    if sell_1h > 0:
        sell_vol_accel = round((sell_5m * 12.0) / sell_1h, 2)
    elif sell_5m > 0:
        sell_vol_accel = 12.0
    else:
        sell_vol_accel = 1.0

    # Pool liquidity & 24h volume
    liq = 0.0
    if liquidity_usd is not None:
        try:
            liq = float(liquidity_usd)
        except (ValueError, TypeError):
            liq = 0.0
    elif primary:
        liq = float(primary.get('liquidity', {}).get('usd') or 0.0)

    v24 = 0.0
    if volume_24h is not None:
        try:
            v24 = float(volume_24h)
        except (ValueError, TypeError):
            v24 = 0.0
    elif primary:
        v24 = float(primary.get('volume', {}).get('h24') or 0.0)

    mc = 0.0
    if market_cap is not None:
        try:
            mc = float(market_cap)
        except (ValueError, TypeError):
            mc = 0.0
    elif primary:
        mc = float(primary.get('marketCap') or primary.get('fdv') or 0.0)

    # 5. Net Buy Volume / Liquidity: (Net Buy 5m / Liquidity) * 100
    net_buy_to_liq = round((net_buy_5m / liq) * 100.0, 2) if liq > 0 else 0.0

    # 6. Volume / Market Cap: 24h Volume / Market Cap
    vol_to_mc = round(v24 / mc, 2) if mc > 0 else None

    # 7. Volume / Liquidity: 24h Volume / Liquidity
    vol_to_liq = round(v24 / liq, 2) if liq > 0 else None

    # 8. Unique Buyer/Seller Ratio
    ub = int(metrics.get('unique_buyers') or 0)
    us = int(metrics.get('unique_sellers') or 0)
    if us > 0:
        bs_ratio = round(ub / us, 2)
    elif ub > 0:
        bs_ratio = float(ub)
    else:
        bs_ratio = 1.0

    # 9. Holder Concentration: Top 10 non-LP %
    hc = metrics.get('top10_concentration_pct')
    holder_concentration = round(float(hc), 2) if hc is not None else None

    # 10. Executable Round-Trip Cost: Buy Slip % + Sell Slip % + DEX fee ~0.50%
    slip = metrics.get('slippage') or {}
    buy_slip = slip.get('buy_slippage_pct')
    sell_slip = slip.get('sell_slippage_pct')
    sellable = slip.get('sellable')
    dex_fee_pct = 0.50
    round_trip_cost = None
    if sellable is False:
        round_trip_cost = None  # Unsellable
    elif buy_slip is not None and sell_slip is not None:
        round_trip_cost = round(buy_slip + sell_slip + dex_fee_pct, 2)
    elif buy_slip is not None:
        round_trip_cost = round(buy_slip * 2.0 + dex_fee_pct, 2)

    return {
        'net_buy_pressure': net_buy_5m,
        'buy_dominance_pct': buy_dom_5m,
        'buy_pressure_accel': buy_pressure_accel,
        'buy_vol_accel': buy_vol_accel,
        'sell_vol_accel': sell_vol_accel,
        'net_buy_to_liq': net_buy_to_liq,
        'vol_to_mc': vol_to_mc,
        'vol_to_liq': vol_to_liq,
        'buyer_seller_ratio': bs_ratio,
        'holder_concentration': holder_concentration,
        'round_trip_cost': round_trip_cost,
    }

def format_akoueflow_indicators_block(indicators):
    """Format the 10 Akoueflow trading indicators for the Telegram signal message."""
    if not indicators or not isinstance(indicators, dict):
        return ""
    lines = ["\n📊 AKOUEFLOW TRADING INDICATORS"]

    # 1. Net Buy Pressure
    nb = indicators.get('net_buy_pressure', 0.0)
    dom = indicators.get('buy_dominance_pct', 50.0)
    nb_sign = "+" if nb > 0 else ("-" if nb < 0 else "")
    lines.append(f"┃ 1. Net Buy Pressure: {nb_sign}{format_usd_metric(abs(nb))} ({dom:.1f}% Buy Dom)")

    # 2. Buy Pressure Acceleration
    bpa = indicators.get('buy_pressure_accel', 0.0)
    bpa_sign = "+" if bpa > 0 else ""
    lines.append(f"┃ 2. Buy Press Accel: {bpa_sign}{bpa:.1f}% (5m vs 1h)")

    # 3 & 4. Buy / Sell Vol Accel
    b_acc = indicators.get('buy_vol_accel', 1.0)
    s_acc = indicators.get('sell_vol_accel', 1.0)
    lines.append(f"┃ 3. Buy Vol Accel: {b_acc:.1f}x | 4. Sell Vol Accel: {s_acc:.1f}x")

    # 5. Net Buy / Liquidity
    nbl = indicators.get('net_buy_to_liq', 0.0)
    nbl_sign = "+" if nbl > 0 else ""
    lines.append(f"┃ 5. Net Buy / Liq: {nbl_sign}{nbl:.2f}%")

    # 6 & 7. Vol / MC & Vol / Liq
    vmc = indicators.get('vol_to_mc')
    vliq = indicators.get('vol_to_liq')
    vmc_str = f"{vmc:.2f}x" if vmc is not None else "N/A"
    vliq_str = f"{vliq:.2f}x" if vliq is not None else "N/A"
    lines.append(f"┃ 6. Vol/MC: {vmc_str} | 7. Vol/Liq: {vliq_str}")

    # 8. Unique Buyer/Seller Ratio
    bsr = indicators.get('buyer_seller_ratio', 1.0)
    lines.append(f"┃ 8. Buyer/Seller Ratio: {bsr:.2f}")

    # 9. Holder Concentration
    hc = indicators.get('holder_concentration')
    hc_str = f"{hc:.1f}%" if hc is not None else "N/A"
    lines.append(f"┃ 9. Holder Concentration: {hc_str} (Top 10)")

    # 10. Executable Round-Trip Cost
    rtc = indicators.get('round_trip_cost')
    rtc_str = f"~{rtc:.2f}%" if rtc is not None else "❌ Unsellable / High Risk"
    lines.append(f"┃ 10. Round-Trip Cost: {rtc_str} (Slip + Fee)")

    return "\n".join(lines)

def format_dex_validated_metrics_block(metrics_data):
    """Format on-chain audit and slippage metrics for signal message text."""
    if not metrics_data or not isinstance(metrics_data, dict):
        return ""
    lines = ["\n🛡️ SECURITY & ON-CHAIN METRICS"]

    auth = metrics_data.get('authorities') or {}
    mint_auth = auth.get('mint_authority')
    freeze_auth = auth.get('freeze_authority')
    mint_str = "✅ Revoked" if mint_auth is None else f"⚠️ Active ({mint_auth[:4]}...{mint_auth[-4:]})"
    freeze_str = "✅ Revoked" if freeze_auth is None else f"🚨 Active ({freeze_auth[:4]}...{freeze_auth[-4:]})"
    lines.append(f"┃ Mint Authority: {mint_str}")
    lines.append(f"┃ Freeze Authority: {freeze_str}")

    slip = metrics_data.get('slippage') or {}
    buy_slip = slip.get('buy_slippage_pct')
    sell_slip = slip.get('sell_slippage_pct')
    sellable = slip.get('sellable')
    if buy_slip is not None or sell_slip is not None:
        sellable_str = "✅ Passed" if sellable else "❌ Unsellable"
        b_str = f"{buy_slip:.2f}%" if buy_slip is not None else "N/A"
        s_str = f"{sell_slip:.2f}%" if sell_slip is not None else "N/A"
        lines.append(f"┃ 1 SOL Slippage: Buy {b_str} | Sell {s_str} ({sellable_str})")

    bc = metrics_data.get('bonding_curve') or {}
    bc_pct = metrics_data.get('bonding_curve_pct')
    if bc_pct is None and bc.get('is_pump'):
        bc_pct = 100.0 if bc.get('is_completed') else bc.get('progress_pct')
    if bc_pct is not None:
        bc_pct = float(bc_pct)
        if bc_pct >= 100.0:
            lines.append("┃ Pump Bonding Curve: 100.0% (Graduated ✅)")
        elif bc.get('is_pump'):
            r_sol = bc.get('real_sol_reserves', 0.0) or 0.0
            lines.append(f"┃ Pump Bonding Curve: {bc_pct:.1f}% ({r_sol:.1f} SOL / 85 SOL)")
        else:
            lines.append(f"┃ Pump Bonding Curve: {bc_pct:.1f}%")
    else:
        lines.append("┃ Pump Bonding Curve: N/A")

    conc = metrics_data.get('top10_concentration_pct')
    if conc is not None:
        lines.append(f"┃ Top 10 Concentration: {conc:.1f}%")

    creator = metrics_data.get('creator_wallet')
    c_hist = metrics_data.get('creator_wallet_history') or {}
    dev_prior = metrics_data.get('dev_prior_tokens')
    if dev_prior is None and c_hist.get('total_created') is not None:
        dev_prior = c_hist.get('total_created')
    creator_str = f"{creator[:4]}...{creator[-4:]}" if creator else "Unknown"
    if dev_prior is not None:
        lines.append(f"┃ Creator: {creator_str} ({int(dev_prior)} prior tokens)")
    else:
        lines.append(f"┃ Creator: {creator_str} (prior tokens N/A)")

    v_buy_5m = metrics_data.get('volume_buy_5m_usd', 0.0)
    v_sell_5m = metrics_data.get('volume_sell_5m_usd', 0.0)
    v_buy_1h = metrics_data.get('volume_buy_1h_usd', 0.0)
    v_sell_1h = metrics_data.get('volume_sell_1h_usd', 0.0)
    u_b = metrics_data.get('unique_buyers', 0)
    u_s = metrics_data.get('unique_sellers', 0)
    lines.append(f"┃ 5m Buy/Sell: {format_usd_metric(v_buy_5m)} / {format_usd_metric(v_sell_5m)}")
    lines.append(f"┃ 1h Buy/Sell: {format_usd_metric(v_buy_1h)} / {format_usd_metric(v_sell_1h)}")
    lines.append(f"┃ 5m Traders: {u_b:,} Buyers / {u_s:,} Sellers")

    return "\n".join(lines)
