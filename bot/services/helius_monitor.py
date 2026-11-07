"""Helius polling loop for tracked payment addresses."""

import asyncio
import time

from bot.config import debug_log
from bot.db.connection import get_settings
from bot.services.cto_processor import verify_and_forward_cto_token
from bot.services.helius import helius_request_with_failover
from bot.services.tokens import get_token_price_usd
from bot.services.wallet_tracker import get_latest_signatures, process_payment_payer_history

async def helius_polling_loop():
    processed_signatures = set()
    print("[HELIUS POLLING] Started fallback transaction scanning loop.", flush=True)

    while True:
        try:
            settings = get_settings()
            is_auto = str(settings.get('cto_auto_scan', 'false')).lower() == 'true'
            poll_enabled = settings.get('helius_poll_enabled', 'false').lower() == 'true'
            poll_interval = int(settings.get('polling_interval', '10'))

            if not is_auto or not poll_enabled:
                await asyncio.sleep(poll_interval)
                continue

            api_key = settings.get('helius_api_key')
            payment_address = settings.get('cto_dex_payment_address')

            if api_key and payment_address:
                # Check signatures first via standard RPC (1 credit cost)
                sigs = await get_latest_signatures(payment_address, api_key, limit=10)

                # Initialize set on startup if empty
                if not processed_signatures:
                    for s in sigs:
                        processed_signatures.add(s)
                    await asyncio.sleep(poll_interval)
                    continue

                has_new = any(s not in processed_signatures for s in sigs)
                if not has_new:
                    await asyncio.sleep(poll_interval)
                    continue

                # Fetch parsed transaction history ONLY when a new transaction is detected
                url_tmpl = f"https://api.helius.xyz/v0/addresses/{payment_address}/transactions?api-key={{key}}"
                r = await asyncio.to_thread(helius_request_with_failover, "GET", url_tmpl, timeout=10)
                if r and r.status_code == 200:
                    txs = r.json()
                    if isinstance(txs, list):
                        new_tokens_to_check = set()
                        for tx in txs:
                            sig = tx.get("signature")
                            if not sig:
                                continue

                            if sig in processed_signatures:
                                continue

                            processed_signatures.add(sig)
                            if len(processed_signatures) > 2000:
                                processed_signatures.clear()
                                processed_signatures.add(sig)

                            # Extract potential token mints from transfers & balance changes
                            mints = set()
                            for tc in tx.get('tokenBalanceChanges', []):
                                mints.add(tc.get('mint'))
                            for tt in tx.get('tokenTransfers', []):
                                mints.add(tt.get('mint'))

                            excluded_mints = {
                                'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', # USDC
                                'So11111111111111111111111111111111111111112', # WSOL
                                'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d', # USDT
                                payment_address
                            }

                            # Find payer address if it is a payment to payment_address
                            is_payment = False
                            payer_address = None
                            amount_usd = 0.0

                            for transfer in tx.get('tokenTransfers', []):
                                if transfer.get('toUserAccount') == payment_address:
                                    is_payment = True
                                    payer_address = transfer.get('fromUserAccount')
                                    amt = transfer.get('tokenAmount', 0)
                                    mint = transfer.get('mint', '')
                                    if mint in ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d'): # USDC/USDT
                                        amount_usd += amt
                                    else:
                                        t_price = get_token_price_usd(mint)
                                        amount_usd += amt * t_price if t_price else 0.0
                                    break
                            if not is_payment:
                                for transfer in tx.get('nativeTransfers', []):
                                    if transfer.get('toUserAccount') == payment_address:
                                        is_payment = True
                                        payer_address = transfer.get('fromUserAccount')
                                        amount_sol = transfer.get('amount', 0) / 1e9
                                        sol_price = get_token_price_usd("SOL")
                                        amount_usd += amount_sol * sol_price if sol_price else 0.0
                                        break

                            has_custom_token = False
                            for mint in mints:
                                if mint and mint not in excluded_mints:
                                    new_tokens_to_check.add(mint)
                                    has_custom_token = True

                            if not has_custom_token and is_payment and payer_address:
                                tx_ts = tx.get("timestamp", int(time.time()))
                                debug_log(f"[HELIUS POLLING] No base tokens in payment tx. Triggering payer history search for {payer_address}...")
                                asyncio.create_task(process_payment_payer_history(payer_address, tx_ts, api_key, amount_usd))

                        for token in new_tokens_to_check:
                            debug_log(f"[HELIUS POLLING] Instantly verifying token {token}...")
                            await verify_and_forward_cto_token(token, amount_usd, payer_address=payer_address)
        except Exception as e:
            print(f"[HELIUS POLLING] Loop error: {e}", flush=True)

        await asyncio.sleep(poll_interval)
