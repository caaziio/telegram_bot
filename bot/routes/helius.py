"""Helius webhook endpoint."""

import time
from flask import Flask, render_template, request, jsonify

from bot.async_bridge import run_bounded_async_coroutine
from bot.caches import claim_webhook_signature
from bot.config import debug_log
from bot.db.connection import get_settings
from bot.services.cto_processor import process_single_cto_item, verify_and_forward_cto_token
from bot.services.tokens import get_token_price_usd
from bot.services.wallet_tracker import process_payment_and_forward, process_payment_payer_history
from bot.web import app

@app.route('/api/helius/webhook', methods=['POST'])
def helius_webhook():
    try:
        transactions = request.json
        if not isinstance(transactions, list):
            return jsonify({"status": "ignored"}), 400

        # Debug: Save raw webhook payload to a local file for inspection
        try:
            import json
            with open("debug_webhook.json", "w") as f:
                json.dump(transactions, f, indent=2)
        except Exception as e:
            print(f"[HELIUS WEBHOOK] Error saving raw webhook data: {e}", flush=True)

        settings = get_settings()
        is_auto = str(settings.get('cto_auto_scan', 'false')).lower() == 'true'
        if not is_auto:
            debug_log("[HELIUS WEBHOOK] Ignored webhook transaction because live scan is inactive.")
            return jsonify({"status": "ignored", "reason": "live scan inactive"}), 200

        monitored_address = settings.get('cto_dex_payment_address')
        tracked_address_str = settings.get('tracked_wallet_address')

        # Build lists of addresses we care about
        tracked_addresses = []
        if tracked_address_str:
            tracked_addresses = [a.strip() for a in tracked_address_str.split(',') if len(a.strip()) >= 32]

        for tx in transactions:
            sig = tx.get("signature")
            if not sig:
                continue
            if not claim_webhook_signature(sig):
                debug_log(f"[HELIUS WEBHOOK] Ignoring duplicate transaction: {sig}")
                continue

            # 1. Process tracked wallet payments
            for tracked_address in tracked_addresses:
                # Process native transfers
                for transfer in tx.get('nativeTransfers', []):
                    to_addr = transfer.get('toUserAccount')
                    from_addr = transfer.get('fromUserAccount')
                    amount_lamports = transfer.get('amount', 0)
                    if to_addr == tracked_address and from_addr != tracked_address:
                        amount_sol = amount_lamports / 1_000_000_000.0
                        sol_price = get_token_price_usd("SOL")
                        amount_usd = amount_sol * sol_price if sol_price else 0.0
                        timestamp = tx.get("timestamp", int(time.time()))
                        debug_log(f"[HELIUS WEBHOOK] Detected real-time tracked native payment: {amount_sol} SOL from {from_addr} to {tracked_address}. Tracing payer history...")
                        run_bounded_async_coroutine(process_payment_and_forward(from_addr, timestamp, amount_usd, signature=sig, tracked_address=tracked_address, amount=amount_sol, mint="SOL"))

                # Process token transfers
                for transfer in tx.get('tokenTransfers', []):
                    to_addr = transfer.get('toUserAccount')
                    from_addr = transfer.get('fromUserAccount')
                    token_amount = transfer.get('tokenAmount', 0)
                    mint = transfer.get('mint', '')
                    if to_addr == tracked_address and from_addr != tracked_address:
                        token_price = get_token_price_usd(mint)
                        amount_usd = token_amount * token_price if token_price else 0.0
                        timestamp = tx.get("timestamp", int(time.time()))

                        # If it is a custom token, forward it directly. Otherwise trace payer history.
                        if mint not in ('So11111111111111111111111111111111111111112', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d', 'SOL'):
                            debug_log(f"[HELIUS WEBHOOK] Custom token detected: {mint}. Forwarding directly to filters.")
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
                            run_bounded_async_coroutine(process_single_cto_item(item, target, wf_id, t_mode))
                        else:
                            debug_log(f"[HELIUS WEBHOOK] Detected base token payment: {token_amount} {mint[:8]}... from {from_addr} to {tracked_address}. Tracing payer history...")
                            run_bounded_async_coroutine(process_payment_and_forward(from_addr, timestamp, amount_usd, signature=sig, tracked_address=tracked_address, amount=token_amount, mint=mint))

            # 2. Process DexScreener monitored payment address
            if monitored_address:
                is_payment = False

                # Check if this transaction sent tokens/SOL to our monitored address
                for transfer in tx.get('tokenTransfers', []):
                    if transfer.get('toUserAccount') == monitored_address:
                        is_payment = True
                        break

                if not is_payment:
                    for transfer in tx.get('nativeTransfers', []):
                        if transfer.get('toUserAccount') == monitored_address:
                            is_payment = True
                            break

                if is_payment:
                    # Find payer address
                    payer_address = None
                    for transfer in tx.get('tokenTransfers', []):
                        if transfer.get('toUserAccount') == monitored_address:
                            payer_address = transfer.get('fromUserAccount')
                            break
                    if not payer_address:
                        for transfer in tx.get('nativeTransfers', []):
                            if transfer.get('toUserAccount') == monitored_address:
                                payer_address = transfer.get('fromUserAccount')
                                break

                    # Extract any referenced base token mint addresses, excluding native assets & USDC
                    mints = set()
                    for tc in tx.get('tokenBalanceChanges', []):
                        mints.add(tc.get('mint'))
                    for tt in tx.get('tokenTransfers', []):
                        mints.add(tt.get('mint'))

                    excluded_mints = {
                        'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', # USDC
                        'So11111111111111111111111111111111111111112', # WSOL
                        'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d', # USDT
                        monitored_address
                    }
                    base_tokens = [m for m in mints if m and m not in excluded_mints]

                    debug_log(f"[HELIUS WEBHOOK] DexScreener payment transaction detected: {sig} | Discovered tokens: {base_tokens}")

                    # Calculate amount_usd for DexScreener payment if possible
                    amount_usd = 0.0
                    for transfer in tx.get('tokenTransfers', []):
                        if transfer.get('toUserAccount') == monitored_address:
                            amt = transfer.get('tokenAmount', 0)
                            mint = transfer.get('mint', '')
                            if mint in ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d'):
                                amount_usd += amt
                            else:
                                t_price = get_token_price_usd(mint)
                                amount_usd += amt * t_price if t_price else 0.0
                            break
                    if amount_usd == 0.0:
                        for transfer in tx.get('nativeTransfers', []):
                            if transfer.get('toUserAccount') == monitored_address:
                                amount_sol = transfer.get('amount', 0) / 1e9
                                sol_price = get_token_price_usd("SOL")
                                amount_usd += amount_sol * sol_price if sol_price else 0.0
                                break

                    if base_tokens:
                        # Instantly check each base token in background Telethon loop
                        for ca in base_tokens:
                            run_bounded_async_coroutine(verify_and_forward_cto_token(ca, amount_usd, payer_address=payer_address))
                    elif payer_address:
                        api_key = settings.get('helius_api_key')
                        tx_ts = tx.get("timestamp", int(time.time()))
                        debug_log(f"[HELIUS WEBHOOK] No base tokens in payment tx. Triggering payer history search for {payer_address}...")
                        run_bounded_async_coroutine(process_payment_payer_history(payer_address, tx_ts, api_key, amount_usd))

        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"[HELIUS WEBHOOK] Error handling webhook: {e}", flush=True)
        return jsonify({"status": "error", "error": str(e)}), 500
