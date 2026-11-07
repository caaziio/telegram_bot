"""Token price and metadata lookups."""

import time
import requests

from bot.caches import bounded_cache_set, token_metadata_cache, token_price_cache

def get_token_price_usd(mint):
    if not mint:
        return 0.0

    search_mint = mint
    if mint == "SOL":
        search_mint = "So11111111111111111111111111111111111111112"

    # Stablecoins USDC/USDT are $1.0
    if search_mint in ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d'):
        return 1.0

    now = time.time()
    if search_mint in token_price_cache:
        price, ts = token_price_cache[search_mint]
        if now - ts < 60:
            return price

    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{search_mint}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            pairs = data.get("pairs")
            if pairs and isinstance(pairs, list):
                # Filter by Solana chain only to avoid matching native tokens on other chains (e.g. Wrapped Fogo on Fogo chain using same address)
                solana_pairs = [p for p in pairs if p.get("chainId", "").lower() == "solana"]
                if solana_pairs:
                    # 1. Look for a pair where search_mint is baseToken and quote is USDC/USDT
                    for p in solana_pairs:
                        base_addr = p.get("baseToken", {}).get("address")
                        quote_sym = p.get("quoteToken", {}).get("symbol", "").upper()
                        if base_addr == search_mint and quote_sym in ("USDC", "USDT"):
                            price_str = p.get("priceUsd")
                            if price_str:
                                price = float(price_str)
                                bounded_cache_set(token_price_cache, search_mint, (price, now))
                                return price

                    # 2. Look for any pair where search_mint is baseToken
                    for p in solana_pairs:
                        base_addr = p.get("baseToken", {}).get("address")
                        if base_addr == search_mint:
                            price_str = p.get("priceUsd")
                            if price_str:
                                price = float(price_str)
                                bounded_cache_set(token_price_cache, search_mint, (price, now))
                                return price

                    # 3. Fallback to first Solana pair priceUsd
                    for p in solana_pairs:
                        price_str = p.get("priceUsd")
                        if price_str:
                            price = float(price_str)
                            bounded_cache_set(token_price_cache, search_mint, (price, now))
                            return price
    except Exception as e:
        print(f"[PRICE FETCH] Error fetching price for {search_mint}: {e}", flush=True)

    # Fallback to expired cache
    if search_mint in token_price_cache:
        return token_price_cache[search_mint][0]

    # If SOL price fetch failed entirely, fallback to a sensible baseline
    if search_mint == "So11111111111111111111111111111111111111112":
        return 140.0

    return 0.0

def get_token_metadata(mint):
    if not mint:
        return ""
    if mint in token_metadata_cache:
        return token_metadata_cache[mint]

    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            pairs = data.get("pairs")
            if pairs and isinstance(pairs, list) and len(pairs) > 0:
                base_token = pairs[0].get("baseToken", {})
                symbol = base_token.get("symbol", "")
                name = base_token.get("name", "")
                if symbol:
                    display_name = f"{symbol} ({name})" if name else symbol
                    bounded_cache_set(token_metadata_cache, mint, display_name)
                    return display_name
    except Exception as e:
        print(f"[METADATA FETCH] Error fetching token metadata for {mint}: {e}", flush=True)

    return ""
