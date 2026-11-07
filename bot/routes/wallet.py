"""Wallet tracker API."""

import asyncio
import json
from datetime import datetime, timezone
import time
import requests
from flask import Flask, render_template, request, jsonify

from bot import state
from bot.config import debug_log
from bot.db.connection import db_lock, get_db, get_settings
from bot.db.supabase_sync import (
    sync_dex_validated_from_supabase,
    sync_wallet_payments_from_supabase,
)
from bot.engine.rules import process_message_logic
from bot.services.dex_enrichment import (
    check_token_migration,
    extract_best_price_change,
    extract_pair_market_metrics,
    format_akoueflow_indicators_block,
    format_dex_validated_metrics_block,
    format_usd_metric,
    get_sorted_trading_pairs,
)
from bot.services.telegram_utils import safe_send_message
from bot.services.token_indicators import synthesize_token_indicators
from bot.services.tokens import get_token_metadata, get_token_price_usd
from bot.services.wallet_tracker import find_token_from_payer_history
from bot.web import app

@app.route('/api/wallet/save_tracked', methods=['POST'])
def save_tracked_wallet():
    data = request.json
    address = data.get('address', '').strip()
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('tracked_wallet_address', address))
        conn.commit()
    return jsonify({"success": True})

@app.route('/api/wallet/transactions', methods=['POST'])
def get_wallet_history_transactions():
    data = request.json
    address = data.get('address', '').strip()
    time_unit = data.get('time_unit', 'hours')
    try:
        time_value = float(data.get('time_value', 12))
    except (ValueError, TypeError):
        time_value = 12.0

    try:
        min_amount = float(data.get('min_amount')) if data.get('min_amount') else None
    except (ValueError, TypeError):
        min_amount = None

    try:
        max_amount = float(data.get('max_amount')) if data.get('max_amount') else None
    except (ValueError, TypeError):
        max_amount = None

    workflow_id = data.get('workflow_id')

    if not address:
        return jsonify({"success": False, "error": "Address is required"})

    addresses = [a.strip() for a in address.split(',') if len(a.strip()) >= 32]
    if not addresses:
        return jsonify({"success": False, "error": "No valid addresses provided"})

    settings = get_settings()
    api_key = settings.get('helius_api_key')
    if not api_key:
        return jsonify({"success": False, "error": "Helius API Key not configured in Settings."})

    workflow_rules = None
    wf_target_channel = None
    wf_target_channel_id = None
    if workflow_id:
        try:
            with db_lock:
                conn = get_db()
                cursor = conn.cursor()
                wf = cursor.execute("SELECT id, target_channel, target_channel_id FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                if wf:
                    wf_id = wf[0] if isinstance(wf, tuple) else wf['id']
                    wf_target_channel = wf[1] if isinstance(wf, tuple) else wf['target_channel']
                    wf_target_channel_id = wf[2] if isinstance(wf, tuple) else wf['target_channel_id']
                    rules_rows = cursor.execute("SELECT rule_type, search_text, replace_text, time_min, time_max FROM rules WHERE workflow_id = ?", (wf_id,)).fetchall()
                    workflow_rules = []
                    for r in rules_rows:
                        if isinstance(r, tuple):
                            workflow_rules.append({
                                "rule_type": r[0],
                                "search_text": r[1],
                                "replace_text": r[2],
                                "time_min": r[3],
                                "time_max": r[4]
                            })
                        else:
                            workflow_rules.append({
                                "rule_type": r['rule_type'],
                                "search_text": r['search_text'],
                                "replace_text": r['replace_text'],
                                "time_min": r['time_min'],
                                "time_max": r['time_max']
                            })
        except Exception as e:
            print(f"[TRACKER DECORATOR] Error loading workflow rules for testing: {e}", flush=True)

    # Calculate cutoff timestamp
    multiplier = 60.0
    if time_unit == 'hours':
        multiplier = 3600.0
    elif time_unit == 'days':
        multiplier = 86400.0

    cutoff_ts = time.time() - (time_value * multiplier)

    senders_map = {}
    sol_price = get_token_price_usd("SOL")

    for addr in addresses:
        last_sig = None
        stop_paginating = False

        for page in range(5):
            url = f"https://api.helius.xyz/v0/addresses/{addr}/transactions?api-key={api_key}"
            if last_sig:
                url += f"&before={last_sig}"

            try:
                r = requests.get(url, timeout=15)
                if r.status_code != 200:
                    if r.status_code == 429:
                        return jsonify({"success": False, "error": "Helius API key rate limit or credit limit exceeded (429: max usage reached). Please check/upgrade your Helius plan or add credits."})
                    elif r.status_code in (401, 403):
                        return jsonify({"success": False, "error": f"Helius API authorization failed (status {r.status_code}). Please verify your Helius API key in Settings."})
                    else:
                        return jsonify({"success": False, "error": f"Helius API returned status code {r.status_code}: {r.text.strip()}"})

                txs = r.json()
                if not txs or not isinstance(txs, list):
                    break

                for tx in txs:
                    tx_ts = tx.get("timestamp", 0)
                    if tx_ts < cutoff_ts:
                        stop_paginating = True
                        break

                    sig = tx.get("signature")

                    for transfer in tx.get('nativeTransfers', []):
                        to_addr = transfer.get('toUserAccount')
                        from_addr = transfer.get('fromUserAccount')
                        amount_lamports = transfer.get('amount', 0)

                        if to_addr == addr and from_addr != addr:
                            amount_sol = amount_lamports / 1_000_000_000.0
                            amount_usd = amount_sol * sol_price

                            if min_amount is not None and amount_usd < min_amount:
                                continue
                            if max_amount is not None and amount_usd > max_amount:
                                continue

                            if from_addr not in senders_map:
                                senders_map[from_addr] = {
                                    "address": from_addr,
                                    "total_sent_sol": 0.0,
                                    "total_sent_usd": 0.0,
                                    "tx_count": 0,
                                    "latest_timestamp": 0,
                                    "transfers": []
                                }

                            senders_map[from_addr]["total_sent_sol"] += amount_sol
                            senders_map[from_addr]["total_sent_usd"] += amount_usd
                            senders_map[from_addr]["tx_count"] += 1
                            if tx_ts > senders_map[from_addr]["latest_timestamp"]:
                                senders_map[from_addr]["latest_timestamp"] = tx_ts

                            senders_map[from_addr]["transfers"].append({
                                "amount": amount_sol,
                                "mint": "SOL",
                                "token_name": "SOL",
                                "amount_usd": amount_usd,
                                "signature": sig,
                                "timestamp": tx_ts
                            })

                    for transfer in tx.get('tokenTransfers', []):
                        to_addr = transfer.get('toUserAccount')
                        from_addr = transfer.get('fromUserAccount')
                        token_amount = transfer.get('tokenAmount', 0)
                        mint = transfer.get('mint', '')

                        if to_addr == addr and from_addr != addr:
                            token_price = get_token_price_usd(mint)
                            amount_usd = token_amount * token_price

                            if min_amount is not None and amount_usd < min_amount:
                                continue
                            if max_amount is not None and amount_usd > max_amount:
                                continue

                            if from_addr not in senders_map:
                                senders_map[from_addr] = {
                                    "address": from_addr,
                                    "total_sent_sol": 0.0,
                                    "total_sent_usd": 0.0,
                                    "tx_count": 0,
                                    "latest_timestamp": 0,
                                    "transfers": []
                                }

                            senders_map[from_addr]["total_sent_usd"] += amount_usd
                            if sol_price > 0:
                                senders_map[from_addr]["total_sent_sol"] += amount_usd / sol_price

                            senders_map[from_addr]["tx_count"] += 1
                            if tx_ts > senders_map[from_addr]["latest_timestamp"]:
                                senders_map[from_addr]["latest_timestamp"] = tx_ts

                            senders_map[from_addr]["transfers"].append({
                                "amount": token_amount,
                                "mint": mint,
                                "token_name": get_token_metadata(mint),
                                "amount_usd": amount_usd,
                                "signature": sig,
                                "timestamp": tx_ts
                            })

                if stop_paginating:
                    break

                last_sig = txs[-1].get("signature")
                if not last_sig:
                    break
            except Exception as e:
                import traceback
                print(f"[TRACKER DECORATOR] Exception during Helius scan: {e}", flush=True)
                traceback.print_exc()
                return jsonify({"success": False, "error": f"Error scanning transactions from Helius: {str(e)}"})

    sorted_senders = list(senders_map.values())
    sorted_senders.sort(key=lambda x: x["latest_timestamp"], reverse=True)

    # Decorate senders with last bought token before returning
    if api_key and sorted_senders:
        async def decorate_senders_with_tokens(senders):
            tasks = [find_token_from_payer_history(s["address"], s["latest_timestamp"], api_key) for s in senders]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for s, token in zip(senders, results):
                if isinstance(token, str) and token:
                    s["last_bought_token"] = token
                    s["last_bought_token_name"] = await asyncio.to_thread(get_token_metadata, token)

                    if workflow_rules:
                        # Fetch token details for workflow rules evaluation
                        token_url = f"https://api.dexscreener.com/latest/dex/tokens/{token}"
                        try:
                            token_response = await asyncio.to_thread(requests.get, token_url, timeout=10)

                            mc_str = "Unknown"
                            age_creation_str = "Unknown"
                            age_migration_str = "0"
                            perf_5m = 0.0
                            perf_1h = 0.0
                            perf_6h = 0.0
                            perf_24h = 0.0
                            is_migrated = False
                            type_display = "Token Profile"
                            project_name = s.get("last_bought_token_name") or "Unknown"
                            chain_id = "Solana (SOL)"

                            if token_response.status_code == 200:
                                token_data = token_response.json()
                                pairs = token_data.get("pairs", [])
                                if pairs:
                                    sorted_pairs = get_sorted_trading_pairs(pairs)
                                    primary_pair = sorted_pairs[0]
                                    raw_chain = primary_pair.get("chainId", "unknown").lower()
                                    project_name = primary_pair.get("baseToken", {}).get("name", "Unknown")

                                    # mc
                                    all_mcs = [p.get("marketCap") or p.get("fdv") for p in pairs if isinstance(p.get("marketCap") or p.get("fdv"), (int, float)) and (p.get("marketCap") or p.get("fdv")) > 0]
                                    raw_mc = max(all_mcs) if all_mcs else (primary_pair.get("marketCap") or primary_pair.get("fdv"))
                                    if isinstance(raw_mc, (int, float)):
                                        if raw_mc >= 1_000_000:
                                            mc_str = f"${raw_mc/1_000_000:.2f}M"
                                        elif raw_mc >= 1_000:
                                            mc_str = f"${raw_mc/1_000:.2f}K"
                                        else:
                                            mc_str = f"${raw_mc:.2f}"

                                    # Age (From creation) - oldest pair creation time
                                    creation_times = [p.get("pairCreatedAt") for p in pairs if p.get("pairCreatedAt")]
                                    if creation_times:
                                        oldest_created_at = min(creation_times)
                                        current_time_ms = int(time.time() * 1000)
                                        age_creation_ms = current_time_ms - oldest_created_at
                                        if age_creation_ms >= 0:
                                            age_creation_minutes = int(age_creation_ms / (1000 * 60))
                                            if age_creation_minutes < 60:
                                                age_creation_str = f"{age_creation_minutes}m"
                                            else:
                                                age_creation_str = f"{int(age_creation_minutes / 60)}h {age_creation_minutes % 60}m"

                                    # If market cap or creation age is unknown, skip further fetching/processing
                                    if mc_str == "Unknown" or age_creation_str == "Unknown":
                                        s["test_status"] = "dropped"
                                        s["test_reason"] = f"Dropped: Market Cap is {mc_str}, Age is {age_creation_str}"
                                        continue

                                    # migration and current location
                                    is_migrated, migration_detail, current_location = await check_token_migration(token, pairs)

                                    # Age (After migration) - creation time of the standard DEX pair
                                    if is_migrated:
                                        standard_dex_pair = None
                                        for p in sorted_pairs:
                                            d_id = p.get("dexId", "").lower()
                                            if d_id not in ["pumpfun", "moonshot", "pumpswap"]:
                                                standard_dex_pair = p
                                                break

                                        if standard_dex_pair and standard_dex_pair.get("pairCreatedAt"):
                                            migration_created_at = standard_dex_pair.get("pairCreatedAt")
                                            current_time_ms = int(time.time() * 1000)
                                            age_migration_ms = current_time_ms - migration_created_at
                                            if age_migration_ms >= 0:
                                                age_migration_minutes = int(age_migration_ms / (1000 * 60))
                                                if age_migration_minutes < 60:
                                                    age_migration_str = f"{age_migration_minutes}m"
                                                else:
                                                    age_migration_str = f"{int(age_migration_minutes / 60)}h {age_migration_minutes % 60}m"
                                        else:
                                            age_migration_str = "0"

                                    # perf & volume
                                    perf_5m, perf_1h, perf_6h, perf_24h = extract_best_price_change(pairs)
                                    liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h = extract_pair_market_metrics(primary_pair)
                                    s["perf_5m"] = perf_5m
                                    s["perf_1h"] = perf_1h
                                    s["perf_6h"] = perf_6h
                                    s["perf_24h"] = perf_24h
                                    s["liquidity_usd"] = liquidity_usd
                                    s["volume_5m"] = volume_5m
                                    s["volume_1h"] = volume_1h
                                    s["volume_6h"] = volume_6h
                                    s["volume_24h"] = volume_24h
                                    s["market_cap"] = mc_str
                                    s["creation_age"] = age_creation_str
                                    s["migration_age"] = age_migration_str

                            payment_amount_usd = s["total_sent_usd"]

                            payment_time_str = "Unknown"
                            if s.get("latest_timestamp"):
                                try:
                                    dt = datetime.fromtimestamp(s["latest_timestamp"], tz=timezone.utc)
                                    payment_time_str = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                                except Exception:
                                    pass

                            metrics_block = (f"📊 PRICE PERFORMANCE\n"
                                             f"┃ 🟩 5m: {perf_5m:+.2f}%\n"
                                             f"┃ ⚡ 1h: {perf_1h:+.2f}%\n"
                                             f"┃ 📉 6h: {perf_6h:+.2f}%\n"
                                             f"┃ 🟥 24h: {perf_24h:+.2f}%\n\n"
                                             f"💧 LIQUIDITY & VOLUME\n"
                                             f"┃ Liquidity: {format_usd_metric(liquidity_usd)}\n"
                                             f"┃ 5m Volume: {format_usd_metric(volume_5m)}\n"
                                             f"┃ 1h Volume: {format_usd_metric(volume_1h)}\n"
                                             f"┃ 6h Volume: {format_usd_metric(volume_6h)}\n"
                                             f"┃ 24h Volume: {format_usd_metric(volume_24h)}")

                            syn_metrics = synthesize_token_indicators({
                                'perf_5m': perf_5m, 'perf_1h': perf_1h, 'perf_6h': perf_6h, 'perf_24h': perf_24h,
                                'liquidity_usd': liquidity_usd, 'volume_5m': volume_5m, 'volume_1h': volume_1h,
                                'volume_6h': volume_6h, 'volume_24h': volume_24h, 'market_cap': mc_str
                            })
                            validated_metrics_block = ""
                            if syn_metrics:
                                s_b = format_dex_validated_metrics_block(syn_metrics)
                                i_b = format_akoueflow_indicators_block(syn_metrics.get('indicators'))
                                if s_b: validated_metrics_block += f"\n{s_b}\n"
                                if i_b: validated_metrics_block += f"{i_b}\n"

                            msg = (f"🚀 PROJECT: {str(project_name).upper()} 🚀\n"
                                   f"━━━━━━━━━━━\n"
                                   f"💰 Market Cap: {mc_str}\n"
                                   f"🌐 Platform: {chain_id}\n"
                                   f"🧬 CA: {token}\n"
                                   f"━━━━━━━━━━━\n\n"
                                   f"⏱️ TOKEN TIMINGS\n"
                                   f"┃ Age (From creation): {age_creation_str}\n"
                                   f"┃ Age (After migration): {age_migration_str}\n"
                                   f"┃ Order: 📦 {type_display}\n"
                                   f"┃ DEX Payment: ${payment_amount_usd:.2f} USD\n"
                                   f"┃ Migrated: {migration_detail}\n"
                                   f"┃ Current DEX: {current_location}\n"
                                   f"┃ Status: ✅ APPROVED\n"
                                   f"┃ Order Placed: {payment_time_str}\n\n"
                                   f"{metrics_block}\n"
                                   f"{validated_metrics_block}"
                                   f"━━━━━━━━━━━\n"
                                   f"📈 Chart: https://dexscreener.com/solana/{token}")
                            s["formatted_message"] = msg
                            s["metrics_data"] = syn_metrics
                            s["indicators"] = syn_metrics.get("indicators") if syn_metrics else {}

                            _, dropped, reason = process_message_logic(msg, workflow_rules, context={
                                "payer_address": s.get("address"),
                                "token_name": project_name,
                                "signal_source": "dex_paid",
                                "metrics_data": syn_metrics,
                                "indicators": syn_metrics.get("indicators") if syn_metrics else {}
                            })
                            if dropped:
                                s["test_status"] = "dropped"
                                s["test_reason"] = reason
                            else:
                                s["test_status"] = "passed"
                                s["test_reason"] = "Passed"

                                # Actually send to Telegram target group
                                if state.tg_client:
                                    t_id = str(wf_target_channel_id or '').strip()
                                    t_username = wf_target_channel or ''
                                    final_target = t_id if t_id else t_username
                                    if final_target:
                                        try:
                                            send_text = _ if _ else msg
                                            await safe_send_message(state.tg_client, final_target, send_text)
                                            debug_log(f"[TEST RUNNER] Successfully forwarded passing test token {token} to {final_target}")
                                            s["test_reason"] = f"Passed & Forwarded to Telegram!"
                                        except Exception as tg_err:
                                            print(f"[TEST RUNNER] Failed to send test token {token} to {final_target}: {tg_err}", flush=True)
                                            s["test_reason"] = f"Passed but Telegram send failed: {tg_err}"
                                else:
                                    print(f"[TEST RUNNER] WARNING: Token {token} passed filters, but Telegram client is not running/authorized! Cannot send to target.", flush=True)
                                    s["test_reason"] = "Passed but Telegram client is not running/authorized!"
                        except Exception as e:
                            s["test_status"] = "error"
                            s["test_reason"] = f"Test Error: {e}"
                else:
                    s["last_bought_token"] = None
                    s["last_bought_token_name"] = None
                    if workflow_rules:
                        s["test_status"] = "dropped"
                        s["test_reason"] = "No token bought before payment"

        if state.telethon_loop and state.telethon_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(decorate_senders_with_tokens(sorted_senders), state.telethon_loop)
            try:
                future.result(timeout=15)
            except Exception as ex:
                print(f"[TRACKER DECORATOR] Error waiting for token decoration: {ex}", flush=True)
        else:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(decorate_senders_with_tokens(sorted_senders))
            except Exception as ex:
                print(f"[TRACKER DECORATOR] Fallback loop error during token decoration: {ex}", flush=True)
            finally:
                loop.close()

    return jsonify({"success": True, "senders": sorted_senders})

@app.route('/api/wallet/address_transactions', methods=['POST'])
def get_address_recent_transactions():
    data = request.json
    address = data.get('address', '').strip()

    if not address:
        return jsonify({"success": False, "error": "Address is required"})

    settings = get_settings()
    api_key = settings.get('helius_api_key')
    if not api_key:
        return jsonify({"success": False, "error": "Helius API Key not configured"})

    url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={api_key}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return jsonify({"success": False, "error": f"Helius API error: {r.status_code}"})

        txs = r.json()
        formatted_txs = []
        excluded_mints = {
            'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', # USDC
            'So11111111111111111111111111111111111111112', # WSOL
            'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d', # USDT
            '11111111111111111111111111111111'
        }

        if isinstance(txs, list):
            for tx in txs:
                sig = tx.get("signature")
                timestamp = tx.get("timestamp", 0)
                tx_type = tx.get("type", "UNKNOWN")

                incoming_tokens = []
                outgoing_tokens = []
                incoming_sol = 0.0
                outgoing_sol = 0.0

                incoming_sol_native = 0.0
                outgoing_sol_native = 0.0
                incoming_stable = 0.0
                outgoing_stable = 0.0

                for transfer in tx.get("nativeTransfers", []):
                    to_u = transfer.get("toUserAccount")
                    from_u = transfer.get("fromUserAccount")
                    amt = transfer.get("amount", 0) / 1e9
                    if to_u == address:
                        incoming_sol += amt
                        incoming_sol_native += amt
                    if from_u == address:
                        outgoing_sol += amt
                        outgoing_sol_native += amt

                for transfer in tx.get("tokenTransfers", []):
                    to_u = transfer.get("toUserAccount")
                    from_u = transfer.get("fromUserAccount")
                    amt = transfer.get("tokenAmount", 0)
                    mint = transfer.get("mint", "")

                    if not mint:
                        continue

                    if to_u == address:
                        if mint not in excluded_mints:
                            incoming_tokens.append((mint, amt))
                        else:
                            if mint in ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d'):
                                incoming_sol += amt
                                incoming_stable += amt
                    if from_u == address:
                        if mint not in excluded_mints:
                            outgoing_tokens.append((mint, amt))
                        else:
                            if mint in ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d'):
                                outgoing_sol += amt
                                outgoing_stable += amt

                tx_action = "OTHER"
                target_token = ""
                target_amount = 0.0
                value_exchanged = ""

                sol_price = get_token_price_usd("SOL")

                # Check for buy
                if incoming_tokens and (outgoing_sol > 0 or tx_type == "SWAP"):
                    tx_action = "BUY"
                    target_token = incoming_tokens[0][0]
                    target_amount = incoming_tokens[0][1]
                    if outgoing_sol > 0:
                        usd_val = (outgoing_sol_native * sol_price) + outgoing_stable
                        if outgoing_sol_native > 0 and outgoing_stable > 0:
                            value_exchanged = f"{outgoing_sol_native:.3f} SOL + ${outgoing_stable:.2f} (${usd_val:.2f})"
                        elif outgoing_sol_native > 0:
                            value_exchanged = f"{outgoing_sol_native:.3f} SOL (${usd_val:.2f})"
                        else:
                            value_exchanged = f"${usd_val:.2f}"
                    else:
                        # Fallback: check if we transferred out WSOL
                        wsol_out = sum(t.get('tokenAmount', 0) for t in tx.get('tokenTransfers', []) if t.get('fromUserAccount') == address and t.get('mint') == 'So11111111111111111111111111111111111111112')
                        if wsol_out > 0:
                            usd_val = wsol_out * sol_price
                            value_exchanged = f"{wsol_out:.3f} SOL (${usd_val:.2f})"
                        else:
                            value_exchanged = "-"
                # Check for sell
                elif outgoing_tokens and (incoming_sol > 0 or tx_type == "SWAP"):
                    tx_action = "SELL"
                    target_token = outgoing_tokens[0][0]
                    target_amount = outgoing_tokens[0][1]
                    if incoming_sol > 0:
                        usd_val = (incoming_sol_native * sol_price) + incoming_stable
                        if incoming_sol_native > 0 and incoming_stable > 0:
                            value_exchanged = f"{incoming_sol_native:.3f} SOL + ${incoming_stable:.2f} (${usd_val:.2f})"
                        elif incoming_sol_native > 0:
                            value_exchanged = f"{incoming_sol_native:.3f} SOL (${usd_val:.2f})"
                        else:
                            value_exchanged = f"${usd_val:.2f}"
                    else:
                        wsol_in = sum(t.get('tokenAmount', 0) for t in tx.get('tokenTransfers', []) if t.get('toUserAccount') == address and t.get('mint') == 'So11111111111111111111111111111111111111112')
                        if wsol_in > 0:
                            usd_val = wsol_in * sol_price
                            value_exchanged = f"{wsol_in:.3f} SOL (${usd_val:.2f})"
                        else:
                            value_exchanged = "-"
                else:
                    # Fallback to standard transfer type
                    tx_action = tx_type
                    all_mints = [m[0] for m in incoming_tokens + outgoing_tokens]
                    if all_mints:
                        target_token = all_mints[0]
                        target_amount = incoming_tokens[0][1] if incoming_tokens else outgoing_tokens[0][1]
                    else:
                        target_token = ""
                        target_amount = 0.0
                    value_exchanged = ""

                description = tx.get("description", "")
                if not description:
                    if tx_action == "BUY":
                        description = f"Bought {target_amount:,.2f} of token {target_token[:6]}..."
                    elif tx_action == "SELL":
                        description = f"Sold {target_amount:,.2f} of token {target_token[:6]}..."
                    else:
                        description = f"Transaction type {tx_action}"

                formatted_txs.append({
                    "signature": sig,
                    "timestamp": timestamp,
                    "action": tx_action,
                    "token_bought": target_token,
                    "token_name": get_token_metadata(target_token),
                    "amount_bought": target_amount,
                    "value_exchanged": value_exchanged,
                    "description": description
                })

        return jsonify({"success": True, "transactions": formatted_txs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/wallet/payments_log', methods=['GET'])
def get_wallet_payments_log():
    try:
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            rows = cursor.execute('''
                SELECT id, tracked_address, sender_address, amount, mint, signature, timestamp,
                       token_name, ca, platform, market_cap, age, perf_5m, perf_1h, perf_6h, perf_24h,
                       dex_url, migration_status, status, reason, formatted_message, created_at, metrics_data
                FROM wallet_payments
                WHERE token_name IS NOT NULL
                  AND TRIM(token_name) != ''
                  AND token_name NOT IN ('SOL', 'USDT', 'USDC', 'WSOL', 'Wrapped SOL', 'Unknown')
                ORDER BY id DESC LIMIT 100
            ''').fetchall()
            rows_list = [dict(r) if isinstance(r, dict) else {
                "id": r[0],
                "tracked_address": r[1],
                "sender_address": r[2],
                "amount": r[3],
                "mint": r[4],
                "signature": r[5],
                "timestamp": r[6],
                "token_name": r[7],
                "ca": r[8],
                "platform": r[9],
                "market_cap": r[10],
                "age": r[11],
                "perf_5m": r[12],
                "perf_1h": r[13],
                "perf_6h": r[14],
                "perf_24h": r[15],
                "dex_url": r[16],
                "migration_status": r[17],
                "status": r[18],
                "reason": r[19],
                "formatted_message": r[20],
                "created_at": r[21],
                "metrics_data": r[22] if len(r) > 22 else None
            } for r in rows]

        results = []
        for r in rows_list:
            amount = r['amount']
            mint = r['mint']
            price = get_token_price_usd(mint)
            amount_usd = amount * price if price else 0.0

            res_dict = dict(r)
            m_data = res_dict.get('metrics_data')
            if isinstance(m_data, str) and m_data.strip():
                try:
                    res_dict['metrics_data'] = json.loads(m_data)
                except Exception:
                    pass
            res_dict['amount_usd'] = amount_usd
            if not res_dict.get('token_name'):
                res_dict['token_name'] = "SOL" if mint == "SOL" else get_token_metadata(mint)
            results.append(res_dict)
        return jsonify({"success": True, "payments": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/wallet/sync_supabase', methods=['POST', 'GET'])
def trigger_supabase_wallet_sync():
    try:
        full_sync = request.args.get('full') == 'true' or (request.is_json and request.json.get('full', False))
        synced_wp = sync_wallet_payments_from_supabase(limit_records=1000, full_sync=full_sync)
        synced_dv = sync_dex_validated_from_supabase(full_sync=full_sync)
        synced_count = synced_wp + synced_dv
        with db_lock:
            conn = get_db()
            cnt = conn.cursor().execute("SELECT COUNT(*) FROM wallet_payments").fetchone()[0]
            dv_cnt = conn.cursor().execute("SELECT COUNT(*) FROM dex_validated").fetchone()[0]
        return jsonify({
            "success": True,
            "message": f"Synced {synced_count} records from Supabase",
            "synced_count": synced_count,
            "count": cnt,
            "dex_validated_count": dv_cnt
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
