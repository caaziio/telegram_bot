"""Payer history tracing and wallet tracker polling."""

import asyncio
import time

from bot.config import debug_log
from bot.db.connection import db_lock, get_db, get_settings
from bot.services.cto_processor import process_single_cto_item, verify_and_forward_cto_token
from bot.services.helius import helius_request_with_failover
from bot.services.tokens import get_token_price_usd

async def find_token_from_payer_history(payer_address, payment_timestamp, api_key, depth=0):
    if not payer_address or not api_key:
        return None
    if depth > 2:
        return None

    try:
        debug_log(f"[PAYER HISTORY] Scanning history for payer {payer_address} around payment time {payment_timestamp} (depth={depth})...")
        url_tmpl = f"https://api.helius.xyz/v0/addresses/{payer_address}/transactions?api-key={{key}}"
        r = await asyncio.to_thread(helius_request_with_failover, "GET", url_tmpl, timeout=10)
        if not r or r.status_code != 200:
            err_text = r.text.strip() if r else "No response"
            print(f"[PAYER HISTORY] Helius API error: {err_text}", flush=True)
            return None

        txs = r.json()
        if not txs or not isinstance(txs, list):
            print(f"[PAYER HISTORY] No transactions found for {payer_address}.", flush=True)
            return None

        excluded_mints = {
            'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', # USDC
            'So11111111111111111111111111111111111111112', # WSOL
            'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d', # USDT
            '11111111111111111111111111111111'
        }

        txs_sorted = sorted(txs, key=lambda x: x.get("timestamp", 0), reverse=True)

        # 1. Primary pass (Recent 2 hours): Look for positive balance changes or token transfer inflows (Buy/Swap)
        for tx in txs_sorted:
            tx_ts = tx.get("timestamp", 0)
            if tx_ts > payment_timestamp + 60 or tx_ts < payment_timestamp - 7200:
                continue

            candidate_mints = set()

            # Check token transfers where payer is recipient
            for transfer in tx.get("tokenTransfers", []):
                mint = transfer.get("mint")
                if mint and mint not in excluded_mints:
                    if transfer.get("toUserAccount") == payer_address:
                        candidate_mints.add(mint)

            # Check token balance changes where balance increased
            for change in tx.get("tokenBalanceChanges", []):
                mint = change.get("mint")
                user = change.get("userAccount")
                if mint and mint not in excluded_mints and user == payer_address:
                    raw_change = change.get("rawTokenAmount", {})
                    try:
                        diff = float(raw_change.get("tokenAmount", 0))
                        if diff > 0:
                            candidate_mints.add(mint)
                    except Exception:
                        pass

            if candidate_mints:
                mint = list(candidate_mints)[0]
                debug_log(f"[PAYER HISTORY] Found token {mint} from positive balance/transfer in tx {tx.get('signature')} at {tx_ts} (payment ts: {payment_timestamp})")
                return mint

        # 1b. Expanded primary pass (Past 24 hours): If nothing in 2 hours
        for tx in txs_sorted:
            tx_ts = tx.get("timestamp", 0)
            if tx_ts > payment_timestamp + 60 or tx_ts < payment_timestamp - 86400:
                continue

            candidate_mints = set()
            for transfer in tx.get("tokenTransfers", []):
                mint = transfer.get("mint")
                if mint and mint not in excluded_mints:
                    if transfer.get("toUserAccount") == payer_address:
                        candidate_mints.add(mint)

            for change in tx.get("tokenBalanceChanges", []):
                mint = change.get("mint")
                user = change.get("userAccount")
                if mint and mint not in excluded_mints and user == payer_address:
                    raw_change = change.get("rawTokenAmount", {})
                    try:
                        diff = float(raw_change.get("tokenAmount", 0))
                        if diff > 0:
                            candidate_mints.add(mint)
                    except Exception:
                        pass

            if candidate_mints:
                mint = list(candidate_mints)[0]
                debug_log(f"[PAYER HISTORY] Found token {mint} from 24h positive balance/transfer in tx {tx.get('signature')} at {tx_ts} (payment ts: {payment_timestamp})")
                return mint

        # 2. Secondary fallback: Look for any custom token transaction activity within 24 hours
        for tx in txs_sorted:
            tx_ts = tx.get("timestamp", 0)
            if tx_ts > payment_timestamp + 60 or tx_ts < payment_timestamp - 86400:
                continue

            for transfer in tx.get("tokenTransfers", []):
                mint = transfer.get("mint")
                if mint and mint not in excluded_mints:
                    debug_log(f"[PAYER HISTORY] Fallback found token {mint} in transfers in tx {tx.get('signature')} at {tx_ts} (payment ts: {payment_timestamp})")
                    return mint

            for change in tx.get("tokenBalanceChanges", []):
                mint = change.get("mint")
                if mint and mint not in excluded_mints:
                    debug_log(f"[PAYER HISTORY] Fallback found token {mint} in balance changes in tx {tx.get('signature')} at {tx_ts} (payment ts: {payment_timestamp})")
                    return mint

        # 3. Funder trace: if no token found, check if this wallet was funded by another address within 24 hours
        if depth < 2:
            funders = []
            for tx in txs_sorted:
                tx_ts = tx.get("timestamp", 0)
                if tx_ts > payment_timestamp + 60 or tx_ts < payment_timestamp - 86400:
                    continue
                for transfer in tx.get("nativeTransfers", []):
                    if transfer.get("toUserAccount") == payer_address:
                        from_addr = transfer.get("fromUserAccount")
                        if from_addr and from_addr != payer_address:
                            amount_sol = transfer.get("amount", 0) / 1e9
                            if amount_sol >= 0.001:  # filter out dust/spam
                                funders.append((from_addr, tx_ts))

            if funders:
                # Sort funders by time closest to payment timestamp (newest first)
                funders.sort(key=lambda x: x[1], reverse=True)
                debug_log(f"[PAYER HISTORY] Wallet {payer_address} had no custom token history. Tracing {len(funders)} funders...")
                for funder_addr, funding_ts in funders:
                    token = await find_token_from_payer_history(funder_addr, funding_ts, api_key, depth=depth + 1)
                    if token:
                        debug_log(f"[PAYER HISTORY] Successfully identified token {token} from funding address {funder_addr}")
                        return token

        debug_log(f"[PAYER HISTORY] Could not find any custom token activity for payer {payer_address} near payment timestamp {payment_timestamp}.")
    except Exception as e:
        print(f"[PAYER HISTORY] Error looking up history for {payer_address}: {e}", flush=True)

    return None

async def process_payment_payer_history(payer_address, payment_timestamp, api_key, amount_usd=0.0):
    token = await find_token_from_payer_history(payer_address, payment_timestamp, api_key)
    if token:
        debug_log(f"[HELIUS DISCOVERY] Successfully isolated token from payer history: {token}. Proceeding to verify.")
        await verify_and_forward_cto_token(token, amount_usd, payer_address=payer_address)
    else:
        debug_log(f"[HELIUS DISCOVERY] No token isolated from payer history for {payer_address}.")

async def get_latest_signatures(address, api_key=None, limit=5):
    payload = {
        "jsonrpc": "2.0",
        "id": "helius-polling",
        "method": "getSignaturesForAddress",
        "params": [
            address,
            {
                "limit": limit
            }
        ]
    }
    headers = {"Content-Type": "application/json"}
    try:
        r = await asyncio.to_thread(helius_request_with_failover, "POST", "https://mainnet.helius-rpc.com/?api-key={key}", json=payload, headers=headers, timeout=10)
        if r and r.status_code == 200:
            res = r.json()
            if "result" in res and isinstance(res["result"], list):
                return [tx.get("signature") for tx in res["result"] if tx.get("signature")]
    except Exception as e:
        print(f"[HELIUS RPC] Error fetching signatures for {address}: {e}", flush=True)
    return []

# Helper to trace and process payment sender history in background
async def process_payment_and_forward(payer_address, tx_timestamp, amount_usd=0.0, signature=None, tracked_address=None, amount=None, mint=None):
    try:
        debug_log(f"[WALLET TRACKER] Tracing sender history for {payer_address}...")
        loop_settings = get_settings()
        loop_api_key = loop_settings.get('helius_api_key')
        if not loop_api_key:
            print("[WALLET TRACKER] Helius API key not found in settings. Skipping history trace.", flush=True)
            return

        token = await find_token_from_payer_history(payer_address, tx_timestamp, loop_api_key)
        if token:
            debug_log(f"[WALLET TRACKER] Discovered token {token} from sender {payer_address}. Forwarding to filters...")
            target = loop_settings.get('cto_target_channel', '')
            wf_id = loop_settings.get('cto_workflow_id')
            t_mode = loop_settings.get('cto_test_mode', 'false').lower() == 'true'

            item = {
                "tokenAddress": token,
                "order_type": "Tracked Wallet Payment",
                "order_status": "detected",
                "payment_timestamp": tx_timestamp * 1000,
                "amount_usd": amount_usd,
                "amount": amount if amount is not None else amount_usd,
                "mint": mint or "SOL",
                "signature": signature,
                "payer_address": payer_address,
                "tracked_address": tracked_address
            }
            await process_single_cto_item(item, target, wf_id, t_mode)
        else:
            debug_log(f"[WALLET TRACKER] No token discovered for sender {payer_address}. Skipping recording.")
            if signature:
                with db_lock:
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM wallet_payments WHERE signature = ?", (signature,))
                    conn.commit()
    except Exception as ex:
        print(f"[WALLET TRACKER] Error processing payment sender history: {ex}", flush=True)

async def wallet_tracker_polling_loop():
    processed_signatures = set()
    initialized_addresses = set()
    print("[WALLET TRACKER POLLING] Started background payment monitoring loop.", flush=True)

    while True:
        try:
            settings = get_settings()
            is_auto = str(settings.get('cto_auto_scan', 'false')).lower() == 'true'
            poll_enabled = settings.get('wallet_tracker_poll_enabled', 'false').lower() == 'true'
            poll_interval = int(settings.get('polling_interval', '10'))

            if not is_auto or not poll_enabled:
                await asyncio.sleep(poll_interval)
                continue

            api_key = settings.get('helius_api_key')
            tracked_address_str = settings.get('tracked_wallet_address')

            if api_key and tracked_address_str:
                addresses = [a.strip() for a in tracked_address_str.split(',') if len(a.strip()) >= 32]

                for tracked_address in addresses:
                    # Check signatures first (1 credit RPC request)
                    sigs = await get_latest_signatures(tracked_address, api_key, limit=10)

                    if tracked_address not in initialized_addresses:
                        for s in sigs:
                            processed_signatures.add(s)
                        initialized_addresses.add(tracked_address)
                        continue

                    # Check if there are any new signatures
                    has_new = any(s not in processed_signatures for s in sigs)
                    if not has_new:
                        continue

                    # Only poll Enhanced Transactions when a new signature is detected
                    url_tmpl = f"https://api.helius.xyz/v0/addresses/{tracked_address}/transactions?api-key={{key}}"
                    r = await asyncio.to_thread(helius_request_with_failover, "GET", url_tmpl, timeout=10)
                    if r and r.status_code == 200:
                        txs = r.json()
                        if isinstance(txs, list):
                            for tx in txs:
                                sig = tx.get("signature")
                                if not sig or sig in processed_signatures:
                                    continue

                                processed_signatures.add(sig)
                                if len(processed_signatures) > 5000:
                                    processed_signatures.clear()
                                    initialized_addresses.clear()
                                    processed_signatures.add(sig)
                                    initialized_addresses.add(tracked_address)

                                timestamp = tx.get("timestamp", int(time.time()))

                                # Process native transfers
                                for transfer in tx.get('nativeTransfers', []):
                                    to_addr = transfer.get('toUserAccount')
                                    from_addr = transfer.get('fromUserAccount')
                                    amount_lamports = transfer.get('amount', 0)
                                    if to_addr == tracked_address and from_addr != tracked_address:
                                        amount_sol = amount_lamports / 1_000_000_000.0
                                        sol_price = get_token_price_usd("SOL")
                                        amount_usd = amount_sol * sol_price if sol_price else 0.0
                                        debug_log(f"[WALLET TRACKER] Detected real-time native payment: {amount_sol} SOL from {from_addr} to {tracked_address}. Tracing payer history...")
                                        # Trigger background history tracing; record to database only once a real token is confirmed
                                        asyncio.create_task(process_payment_and_forward(from_addr, timestamp, amount_usd, signature=sig, tracked_address=tracked_address, amount=amount_sol, mint="SOL"))

                                # Process token transfers
                                for transfer in tx.get('tokenTransfers', []):
                                    to_addr = transfer.get('toUserAccount')
                                    from_addr = transfer.get('fromUserAccount')
                                    token_amount = transfer.get('tokenAmount', 0)
                                    mint = transfer.get('mint', '')
                                    if to_addr == tracked_address and from_addr != tracked_address:
                                        token_price = get_token_price_usd(mint)
                                        amount_usd = token_amount * token_price if token_price else 0.0

                                        # If it is a custom token, forward it directly. Otherwise trace payer history.
                                        if mint not in ('So11111111111111111111111111111111111111112', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d', 'SOL'):
                                            debug_log(f"[WALLET TRACKER] Custom token detected: {mint}. Forwarding directly to filters.")
                                            target = settings.get('cto_target_channel', '')
                                            wf_id = settings.get('cto_workflow_id')
                                            t_mode = settings.get('cto_test_mode', 'false').lower() == 'true'
                                            item = {
                                                "tokenAddress": mint,
                                                "order_type": "Tracked Wallet Swap",
                                                "order_status": "detected",
                                                "payment_timestamp": timestamp * 1000,
                                                "amount_usd": amount_usd,
                                                "amount": token_amount,
                                                "mint": mint,
                                                "signature": sig,
                                                "payer_address": from_addr,
                                                "tracked_address": tracked_address
                                            }
                                            asyncio.create_task(process_single_cto_item(item, target, wf_id, t_mode))
                                        else:
                                            debug_log(f"[WALLET TRACKER] Detected base token payment: {token_amount} {mint[:8]}... from {from_addr} to {tracked_address}. Tracing payer history...")
                                            asyncio.create_task(process_payment_and_forward(from_addr, timestamp, amount_usd, signature=sig, tracked_address=tracked_address, amount=token_amount, mint=mint))
                    else:
                        print(f"[WALLET TRACKER POLLING] Helius API error {r.status_code} for {tracked_address}: {r.text.strip()}", flush=True)
        except Exception as e:
            print(f"[WALLET TRACKER POLLING] Loop error: {e}", flush=True)

        await asyncio.sleep(poll_interval)
