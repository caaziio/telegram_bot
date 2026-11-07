"""DEX payment wallet discovery and payment info lookups."""

import requests

from bot.config import debug_log
from bot.db.connection import get_settings
from bot.services.helius import helius_request_with_failover
from bot.services.tokens import get_token_price_usd

def discover_payment_wallet_logic(token_address, payment_timestamp, api_key):
    if not api_key:
        return None, "Helius API Key not set."

    # payment_timestamp is in ms, convert to seconds
    ts_seconds = int(payment_timestamp / 1000)

    last_sig = None
    log_messages = []
    # Scan up to 10 pages of transaction history from Helius to find the payment
    for page in range(10):
        url = f"https://api.helius.xyz/v0/addresses/{token_address}/transactions?api-key={api_key}"
        if last_sig:
            url += f"&before={last_sig}"

        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                msg = f"Helius API HTTP {r.status_code} error on page {page+1}: {r.text.strip()}"
                debug_log(f"[HELIUS DISCOVERY] {msg}")
                return None, msg

            txs = r.json()
            if not txs or not isinstance(txs, list):
                msg = f"No transactions returned on page {page+1}."
                debug_log(f"[HELIUS DISCOVERY] {msg}")
                log_messages.append(msg)
                break

            # Scan transactions on current page
            for tx in txs:
                tx_ts = tx.get("timestamp")
                if not tx_ts:
                    continue

                # Check if within 90s window of payment
                if abs(tx_ts - ts_seconds) <= 90:
                    debug_log(f"[HELIUS DISCOVERY] Found matching transaction {tx.get('signature')} at {tx_ts} (target: {ts_seconds})")
                    # Look for token transfers (USDC)
                    for transfer in tx.get("tokenTransfers", []):
                        to_address = transfer.get("toUserAccount")
                        if to_address and to_address != token_address:
                            return to_address, "Success"

                    # Look for native transfers (SOL)
                    for transfer in tx.get("nativeTransfers", []):
                        to_address = transfer.get("toUserAccount")
                        if to_address and to_address != token_address:
                            return to_address, "Success"

            # Check if oldest transaction on this page is older than the search window
            oldest_tx_ts = txs[-1].get("timestamp", 0)
            if oldest_tx_ts < ts_seconds - 90:
                msg = f"Oldest transaction on page {page+1} ({oldest_tx_ts}) is older than target ({ts_seconds}). Stopping."
                debug_log(f"[HELIUS DISCOVERY] {msg}")
                log_messages.append(msg)
                break

            last_sig = txs[-1].get("signature")
            if not last_sig:
                break
        except Exception as e:
            msg = f"Error querying page {page+1}: {e}"
            debug_log(f"[HELIUS DISCOVERY] {msg}")
            return None, msg

    summary = "; ".join(log_messages) if log_messages else "No matching payment transaction found in analyzed history pages."
    return None, summary

def get_dex_payment_info(token_address, payment_timestamp, api_key):
    if not api_key or not token_address or not payment_timestamp:
        return 0.0, None

    # Convert payment_timestamp (in ms) to seconds
    ts_seconds = int(payment_timestamp / 1000)

    settings = get_settings()
    monitored_addresses = []

    # Get only the cto_dex_payment_address (where DEX payments are actually sent)
    # to avoid sequential loop timeouts when querying user wallets.
    cto_addr = settings.get('cto_dex_payment_address')
    if cto_addr and len(cto_addr.strip()) >= 32:
        monitored_addresses.append(cto_addr.strip())

    # We will try scanning the transaction history of the cto_dex_payment_address first.
    # If none are configured, fallback to scanning the token address.
    addresses_to_scan = monitored_addresses if monitored_addresses else [token_address]

    try:
        sol_price = get_token_price_usd("SOL")

        for scan_addr in addresses_to_scan:
            url_tmpl = f"https://api.helius.xyz/v0/addresses/{scan_addr}/transactions?api-key={{key}}"
            r = helius_request_with_failover("GET", url_tmpl, timeout=10)
            if r and r.status_code == 200:
                txs = r.json()
                if txs and isinstance(txs, list):
                    for tx in txs:
                        tx_ts = tx.get("timestamp")
                        # Allow a 120-second window around payment timestamp
                        if tx_ts and abs(tx_ts - ts_seconds) <= 120:
                            # Verify if this transaction references our token_address (the custom token CA)
                            tx_str = str(tx)
                            if token_address not in tx_str:
                                continue

                            total_usd = 0.0
                            payer_address = None

                            # Check token transfers to the scan_addr
                            for transfer in tx.get("tokenTransfers", []):
                                if transfer.get("toUserAccount") == scan_addr:
                                    mint = transfer.get("mint", "")
                                    amount = transfer.get("tokenAmount", 0)
                                    if not payer_address:
                                        payer_address = transfer.get("fromUserAccount")
                                    if mint in ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d'): # USDC/USDT
                                        total_usd += amount
                                    else:
                                        t_price = get_token_price_usd(mint)
                                        total_usd += amount * t_price if t_price else 0.0

                            # Check native transfers to the scan_addr
                            for transfer in tx.get("nativeTransfers", []):
                                if transfer.get("toUserAccount") == scan_addr:
                                    amount_sol = transfer.get("amount", 0) / 1e9
                                    total_usd += amount_sol * sol_price
                                    if not payer_address:
                                        payer_address = transfer.get("fromUserAccount")

                            if not payer_address and tx.get("feePayer"):
                                payer_address = tx.get("feePayer")

                            if total_usd > 0 or payer_address:
                                debug_log(f"[DEX PAYMENT FILTER] Found payment of ${total_usd:.2f} USD to {scan_addr} from payer {payer_address}")
                                return total_usd, payer_address

        # Final fallback: scan the token address itself if the monitored addresses scan didn't find it
        if addresses_to_scan != [token_address]:
            url_tmpl = f"https://api.helius.xyz/v0/addresses/{token_address}/transactions?api-key={{key}}"
            r = helius_request_with_failover("GET", url_tmpl, timeout=10)
            if r and r.status_code == 200:
                txs = r.json()
                if txs and isinstance(txs, list):
                    for tx in txs:
                        tx_ts = tx.get("timestamp")
                        if tx_ts and abs(tx_ts - ts_seconds) <= 120:
                            total_usd = 0.0
                            payer_address = None

                            for transfer in tx.get("tokenTransfers", []):
                                mint = transfer.get("mint", "")
                                amount = transfer.get("tokenAmount", 0)
                                if not payer_address:
                                    payer_address = transfer.get("fromUserAccount")
                                if mint in ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d'):
                                    total_usd += amount
                                else:
                                    t_price = get_token_price_usd(mint)
                                    total_usd += amount * t_price if t_price else 0.0

                            for transfer in tx.get("nativeTransfers", []):
                                amount_sol = transfer.get("amount", 0) / 1e9
                                total_usd += amount_sol * sol_price
                                if not payer_address:
                                    payer_address = transfer.get("fromUserAccount")

                            if not payer_address and tx.get("feePayer"):
                                payer_address = tx.get("feePayer")

                            if total_usd > 0 or payer_address:
                                debug_log(f"[DEX PAYMENT FILTER] Fallback token scan found payment of ${total_usd:.2f} USD from payer {payer_address}")
                                return total_usd, payer_address

    except Exception as e:
        print(f"[DEX PAYMENT FILTER] Error fetching payment info: {e}", flush=True)

    return 0.0, None

def get_dex_payment_amount_usd(token_address, payment_timestamp, api_key):
    amt, _ = get_dex_payment_info(token_address, payment_timestamp, api_key)
    return amt
