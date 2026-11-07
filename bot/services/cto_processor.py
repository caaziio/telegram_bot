"""Processing, verification and forwarding of CTO / DEX-paid token items."""

import asyncio
import threading
import json
from datetime import datetime, timezone
import time
import requests

from bot import state
from bot.config import debug_log
from bot.db.connection import db_lock, get_db, get_settings, supabase_client
from bot.db.supabase_sync import push_to_supabase_table
from bot.engine.rules import get_workflows, process_message_logic
from bot.services.dex_enrichment import (
    check_token_migration,
    compute_akoueflow_indicators,
    enrich_dex_validated_token,
    extract_best_price_change,
    extract_pair_market_metrics,
    format_akoueflow_indicators_block,
    format_dex_validated_metrics_block,
    format_usd_metric,
    get_sorted_trading_pairs,
)
from bot.services.dex_payments import get_dex_payment_info
from bot.services.telegram_utils import safe_send_message
from bot.services.token_indicators import (
    compute_bonding_curve_pct,
    compute_dev_prior_tokens,
    synthesize_token_indicators,
)


def _attach_launch_metrics(metrics_data, is_migrated):
    """Store bonding-curve progress and dev prior-token count on metrics_data.

    Saved both at top level and inside ``indicators`` so SQL filters, workflow
    rules and the Outcomes Lab can all read them.
    """
    if not isinstance(metrics_data, dict):
        return
    bc_pct = compute_bonding_curve_pct(metrics_data, None, 'yes' if is_migrated else None)
    dev_prior = compute_dev_prior_tokens(metrics_data)
    metrics_data['bonding_curve_pct'] = round(bc_pct, 2) if bc_pct is not None else None
    metrics_data['dev_prior_tokens'] = int(dev_prior) if dev_prior is not None else None
    inds = metrics_data.get('indicators')
    if isinstance(inds, dict):
        inds['bonding_curve_pct'] = metrics_data['bonding_curve_pct']
        inds['dev_prior_tokens'] = metrics_data['dev_prior_tokens']


async def process_single_cto_item(item, target_channel, workflow_id, test_mode):
    ca = item.get("tokenAddress")
    order_type = item.get("order_type")
    signal_type = item.get("event_category") or order_type
    storage_table = 'dex_validated' if item.get('storage_table') == 'dex_validated' else 'cto_signals'
    order_status = item.get("order_status")
    payment_timestamp = item.get("payment_timestamp")
    if not ca:
        return None

    token_info = {
        "ca": ca,
        "name": "Unknown",
        "platform": "Solana (SOL)",
        "migration_status": "Migrated",
        "market_cap": "Unknown",
        "age": "Unknown",
        "perf_5m": 0.0,
        "perf_1h": 0.0,
        "perf_6h": 0.0,
        "perf_24h": 0.0,
        "liquidity_usd": 0.0,
        "volume_5m": 0.0,
        "volume_1h": 0.0,
        "volume_6h": 0.0,
        "volume_24h": 0.0,
        "status": "passed",
        "reason": "Passed and Sent!",
        "formatted_message": "",
        "dex_url": "",
        "signal_type": signal_type,
        "order_status": order_status,
        "payment_timestamp": payment_timestamp
    }
    published_at = item.get('published_at')
    fetched_at = item.get('fetched_at') or int(time.time() * 1000)
    token_info['fetched_at'] = fetched_at
    token_info['detection_seconds'] = (
        max(0.0, round((fetched_at - published_at) / 1000, 2))
        if isinstance(published_at, (int, float)) and published_at > 0 else None
    )

    # Skip if we already processed this exact signal
    persist_db = bool(item.get('persist_db'))
    force_rescan = bool(item.get('force_rescan'))
    is_duplicate = False
    if (not test_mode or persist_db) and not force_rescan:
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            existing = cursor.execute(f'''
                SELECT id, status FROM {storage_table}
                WHERE ca = ? AND signal_type = ? AND payment_timestamp = ? AND order_status = ?
            ''', (ca, signal_type, payment_timestamp, order_status)).fetchone()
            if existing:
                existing_status = str(existing[1] or '').lower()
                # If we previously only recorded it as 'detected' (no TG send) and now test_mode is False,
                # allow re-processing so it can be forwarded to Telegram.
                if test_mode or existing_status != 'detected':
                    is_duplicate = True

    if is_duplicate:
        token_info["status"] = "duplicate"
        token_info["reason"] = f"Skipped: Already processed ({order_type} - {order_status})"
        return token_info

    type_display_map = {
        "tokenBoost": "Token Boost",
        "tokenProfile": "Token Profile",
        "communityTakeover": "Community Takeover",
        "tokenAd": "Token Ad",
        "trendingBarAd": "Trending Bar Ad"
    }
    type_display = type_display_map.get(order_type, order_type)
    migration_status_formatted = f"{type_display} ({order_status.upper()})"

    def _save_dex_validated_early(status_val, reason_str, proj_name="Unknown", plat="Solana (SOL)", mc_val="Unknown", age_val="Unknown"):
        if storage_table == 'dex_validated' and (not test_mode or persist_db):
            with db_lock:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO dex_validated (
                        ca, name, platform, market_cap, age,
                        perf_5m, perf_1h, perf_6h, perf_24h,
                        liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                        status, dex_url, migration_status, signal_type,
                        payment_timestamp, order_status, published_at, fetched_at,
                        detection_seconds, reason, formatted_message
                    ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ca, signal_type, payment_timestamp, order_status) DO UPDATE SET
                        fetched_at=excluded.fetched_at,
                        reason=excluded.reason
                ''', (
                    ca, proj_name, plat, mc_val, age_val,
                    status_val, f"https://dexscreener.com/solana/{ca}", migration_status_formatted, signal_type,
                    payment_timestamp, order_status, published_at, fetched_at,
                    token_info.get('detection_seconds'), reason_str, ''
                ))
                conn.commit()

    token_url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    try:
        token_response = await asyncio.to_thread(requests.get, token_url, timeout=10)
        if token_response.status_code != 200:
            token_info["status"] = "error"
            token_info["reason"] = f"Dexscreener token API returned {token_response.status_code}"
            _save_dex_validated_early("skipped", token_info["reason"])
            return token_info
    except Exception as e:
        token_info["status"] = "error"
        token_info["reason"] = f"Failed to fetch token details: {e}"
        _save_dex_validated_early("skipped", token_info["reason"])
        return token_info

    token_data = token_response.json()
    pairs = token_data.get("pairs") or []

    if not pairs:
        token_info["status"] = "skipped" if storage_table == 'dex_validated' else "error"
        token_info["reason"] = "No active trading pairs found on Dexscreener"
        _save_dex_validated_early("skipped", token_info["reason"])
        if not test_mode:
            sig = item.get("signature")
            payer = item.get("payer_address")
            if sig or payer:
                with db_lock:
                    conn = get_db()
                    cursor = conn.cursor()
                    if sig:
                        cursor.execute("UPDATE wallet_payments SET ca=?, status='dropped', reason=? WHERE signature=?", (ca, token_info["reason"], sig))
                    elif payer:
                        cursor.execute("UPDATE wallet_payments SET ca=?, status='dropped', reason=? WHERE sender_address=? AND (timestamp * 1000 BETWEEN ? AND ?)", (ca, token_info["reason"], payer, payment_timestamp - 180000, payment_timestamp + 180000))
                    conn.commit()
        return token_info

    sorted_pairs = get_sorted_trading_pairs(pairs)
    primary_pair = sorted_pairs[0]
    raw_chain = primary_pair.get("chainId", "unknown").lower()
    if raw_chain != "solana":
        token_info["status"] = "skipped"
        token_info["reason"] = f"Skipped: Chain is {raw_chain} (not Solana)"
        _save_dex_validated_early("skipped", token_info["reason"], plat=raw_chain.capitalize())
        if not test_mode:
            sig = item.get("signature")
            payer = item.get("payer_address")
            if sig or payer:
                with db_lock:
                    conn = get_db()
                    cursor = conn.cursor()
                    if sig:
                        cursor.execute("UPDATE wallet_payments SET ca=?, status='dropped', reason=? WHERE signature=?", (ca, token_info["reason"], sig))
                    elif payer:
                        cursor.execute("UPDATE wallet_payments SET ca=?, status='dropped', reason=? WHERE sender_address=? AND (timestamp * 1000 BETWEEN ? AND ?)", (ca, token_info["reason"], payer, payment_timestamp - 180000, payment_timestamp + 180000))
                    conn.commit()
        return token_info

    project_name = primary_pair.get("baseToken", {}).get("name", "Unknown")
    chain_map = {
        "solana": "Solana (SOL)",
        "ethereum": "Ethereum (ETH)",
        "bsc": "BSC (BNB)",
        "base": "Base (ETH)",
        "arbitrum": "Arbitrum (ETH)",
        "polygon": "Polygon (MATIC)"
    }
    chain_id = chain_map.get(raw_chain, raw_chain.capitalize())

    all_mcs = []
    for p in pairs:
        val = p.get("marketCap") or p.get("fdv")
        if isinstance(val, (int, float)) and val > 0:
            all_mcs.append(val)
    raw_mc = max(all_mcs) if all_mcs else (primary_pair.get("marketCap") or primary_pair.get("fdv"))
    if isinstance(raw_mc, (int, float)):
        if raw_mc >= 1_000_000:
            mc_str = f"${raw_mc/1_000_000:.2f}M"
        elif raw_mc >= 1_000:
            mc_str = f"${raw_mc/1_000:.2f}K"
        else:
            mc_str = f"${raw_mc:.2f}"
    else:
        mc_str = "Unknown"

    # Calculate Age (From creation) and Age (After migration)
    age_creation_str = "Unknown"
    age_migration_str = "0"

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

    # If market cap or creation age is unknown on non-dex_validated flows, skip further fetching/processing and abort
    if storage_table != 'dex_validated' and (mc_str == "Unknown" or age_creation_str == "Unknown"):
        token_info["status"] = "skipped"
        token_info["reason"] = f"Skipped: Market Cap is {mc_str}, Age is {age_creation_str}"
        if not test_mode:
            sig = item.get("signature")
            payer = item.get("payer_address")
            if sig or payer:
                with db_lock:
                    conn = get_db()
                    cursor = conn.cursor()
                    if sig:
                        cursor.execute("UPDATE wallet_payments SET ca=?, token_name=?, status='dropped', reason=? WHERE signature=?", (ca, project_name, token_info["reason"], sig))
                    elif payer:
                        cursor.execute("UPDATE wallet_payments SET ca=?, token_name=?, status='dropped', reason=? WHERE sender_address=? AND (timestamp * 1000 BETWEEN ? AND ?)", (ca, project_name, token_info["reason"], payer, payment_timestamp - 180000, payment_timestamp + 180000))
                    conn.commit()
        return token_info

    # Detect if token has migrated and current location
    is_migrated, migration_detail, current_location = await check_token_migration(ca, pairs)

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

    perf_5m, perf_1h, perf_6h, perf_24h = extract_best_price_change(pairs)
    liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h = extract_pair_market_metrics(primary_pair)

    payer_address = item.get("payer_address")

    # Fetch DEX payment amount in USD & payer wallet if needed
    payment_amount_usd = item.get("amount_usd")
    if storage_table == 'dex_validated':
        # Published feeds are not payment events and should not trigger a
        # Helius transaction-history lookup.
        payment_amount_usd = 0.0
    elif payment_amount_usd is None or payment_amount_usd == 0.0 or not payer_address:
        settings = get_settings()
        api_key = settings.get('helius_api_key')
        fetched_amt, fetched_payer = await asyncio.to_thread(get_dex_payment_info, ca, payment_timestamp, api_key)
        if payment_amount_usd is None or payment_amount_usd == 0.0:
            payment_amount_usd = fetched_amt
        if not payer_address:
            payer_address = fetched_payer

    dex_url = f"https://dexscreener.com/{raw_chain}/{ca}"

    token_info["name"] = project_name
    token_info["platform"] = chain_id
    token_info["migration_status"] = migration_status_formatted
    token_info["market_cap"] = mc_str
    token_info["age"] = age_creation_str
    token_info["perf_5m"] = perf_5m
    token_info["perf_1h"] = perf_1h
    token_info["perf_6h"] = perf_6h
    token_info["perf_24h"] = perf_24h
    token_info["liquidity_usd"] = liquidity_usd
    token_info["volume_5m"] = volume_5m
    token_info["volume_1h"] = volume_1h
    token_info["volume_6h"] = volume_6h
    token_info["volume_24h"] = volume_24h
    token_info["dex_url"] = dex_url

    status_emoji_map = {
        "approved": "✅",
        "processing": "⏳",
        "cancelled": "❌",
        "on-hold": "⏸️",
        "rejected": "🚫"
    }
    status_emoji = status_emoji_map.get(order_status.lower(), "❓")

    payment_time_str = "Unknown"
    if payment_timestamp:
        try:
            dt = datetime.fromtimestamp(payment_timestamp / 1000, tz=timezone.utc)
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

    category_labels = {
        'tokenBoost': 'BOOST',
        'communityTakeover': 'CTO',
        'tokenProfile': 'DEX PROFILE',
        'tokenAd': 'DEX AD',
    }
    category_line = ''
    metrics_data = {}
    validated_metrics_block = ''

    total_boosts = int(item.get('total_boosts') or 0)
    boost_amount = int(item.get('boost_amount') or 0)
    is_boost_signal = (order_type == 'tokenBoost' or signal_type == 'boost' or total_boosts > 0)
    if is_boost_signal and total_boosts == 0:
        try:
            r_ord = requests.get(f"https://api.dexscreener.com/orders/v1/solana/{ca}", timeout=4)
            if r_ord.status_code == 200:
                b_list = r_ord.json().get('boosts', [])
                if b_list:
                    total_boosts = sum(int(b.get('amount') or 0) for b in b_list)
                    boost_amount = int(b_list[-1].get('amount') or 0)
        except Exception:
            pass

    token_info["total_boosts"] = total_boosts
    token_info["boost_amount"] = boost_amount

    boost_line = ""
    if is_boost_signal or total_boosts > 0:
        if total_boosts > 0:
            if boost_amount > 0 and boost_amount != total_boosts:
                boost_line = f"┃ Boosts: 🚀 {total_boosts:,} (+{boost_amount:,} this order)\n"
            else:
                boost_line = f"┃ Boosts: 🚀 {total_boosts:,}\n"
        else:
            boost_line = f"┃ Boosts: 🚀 Pending\n"

    category_label = category_labels.get(order_type, str(signal_type or ('VALIDATED' if storage_table == 'dex_validated' else 'DEX PAID')).upper())
    category_line = f"┃ Category: {category_label}\n" if storage_table == 'dex_validated' else ""
    try:
        metrics_data = await enrich_dex_validated_token(ca, pairs)
        if metrics_data:
            metrics_data['total_boosts'] = total_boosts
            metrics_data['boost_amount'] = boost_amount
            metrics_data['indicators'] = compute_akoueflow_indicators(metrics_data, pairs, raw_mc, liquidity_usd, volume_24h)
        if not metrics_data or not metrics_data.get('indicators'):
            syn = synthesize_token_indicators({
                'perf_5m': perf_5m,
                'perf_1h': perf_1h,
                'perf_6h': perf_6h,
                'perf_24h': perf_24h,
                'liquidity_usd': liquidity_usd,
                'volume_5m': volume_5m,
                'volume_1h': volume_1h,
                'volume_6h': volume_6h,
                'volume_24h': volume_24h,
                'market_cap': mc_str
            })
            if syn:
                syn['total_boosts'] = total_boosts
                syn['boost_amount'] = boost_amount
                metrics_data = syn
        token_info["metrics_data"] = metrics_data
        _attach_launch_metrics(metrics_data, is_migrated)
        metrics_block_text = format_dex_validated_metrics_block(metrics_data)
        indicators_block_text = format_akoueflow_indicators_block(metrics_data.get('indicators') if metrics_data else None)
        if metrics_block_text:
            validated_metrics_block = f"\n{metrics_block_text}\n"
        if indicators_block_text:
            validated_metrics_block += f"{indicators_block_text}\n"
    except Exception as e:
        tag = "DEX VALIDATED" if storage_table == 'dex_validated' else "DEX PAID"
        print(f"[{tag} ENRICHMENT ERROR] {ca}: {e}", flush=True)
        metrics_data = synthesize_token_indicators({
            'perf_5m': perf_5m,
            'perf_1h': perf_1h,
            'perf_6h': perf_6h,
            'perf_24h': perf_24h,
            'liquidity_usd': liquidity_usd,
            'volume_5m': volume_5m,
            'volume_1h': volume_1h,
            'volume_6h': volume_6h,
            'volume_24h': volume_24h,
            'market_cap': mc_str
        })
        if metrics_data:
            metrics_data['total_boosts'] = total_boosts
            metrics_data['boost_amount'] = boost_amount
            token_info["metrics_data"] = metrics_data
            _attach_launch_metrics(metrics_data, is_migrated)
            metrics_block_text = format_dex_validated_metrics_block(metrics_data)
            indicators_block_text = format_akoueflow_indicators_block(metrics_data.get('indicators'))
            if metrics_block_text:
                validated_metrics_block = f"\n{metrics_block_text}\n"
            if indicators_block_text:
                validated_metrics_block += f"{indicators_block_text}\n"

    msg = (f"🚀 PROJECT: {str(project_name).upper()} 🚀\n"
           f"━━━━━━━━━━━\n"
           f"💰 Market Cap: {mc_str}\n"
           f"🌐 Platform: {chain_id}\n"
           f"🧬 CA: {ca}\n"
           f"━━━━━━━━━━━\n\n"
           f"⏱️ TOKEN TIMINGS\n"
           f"{category_line}"
           f"┃ Age (From creation): {age_creation_str}\n"
           f"┃ Age (After migration): {age_migration_str}\n"
           f"┃ Order: 📦 {type_display}\n"
           f"{boost_line}"
           f"┃ DEX Payment: ${payment_amount_usd:.2f} USD\n"
           f"┃ Migrated: {migration_detail}\n"
           f"┃ Current DEX: {current_location}\n"
           f"┃ Status: {status_emoji} {order_status.upper()}\n"
           f"┃ Order Placed: {payment_time_str}\n\n"
           f"{metrics_block}\n"
           f"{validated_metrics_block}"
           f"━━━━━━━━━━━\n"
           f"📈 Chart: {dex_url}")
    # Keep the full pre-filter message for result inspection even when a
    # workflow drops the signal. Passing workflows may replace this with their
    # transformed version below.
    token_info["formatted_message"] = msg

    passed_any = False
    token_dropped_reasons = []

    workflows_to_evaluate = []
    if workflow_id and str(workflow_id).strip() != "" and str(workflow_id) != "active_all":
        workflows = get_workflows()
        wf = next((w for w in workflows if str(w.get('id')) == str(workflow_id)), None)
        if wf:
            workflows_to_evaluate.append(wf)
        else:
            print(f"[REAL-TIME SCANNER] Warning: Specified workflow ID {workflow_id} not found in database. Falling back to all active workflows.", flush=True)
            workflows_to_evaluate = [w for w in workflows if w.get('is_active')]
    else:
        workflows_to_evaluate = [w for w in get_workflows() if w.get('is_active')]

    if workflows_to_evaluate:
        passed_channels = []
        attempted_channels = []
        send_failures = []
        preview_message = None
        for wf in workflows_to_evaluate:
            category_key_map = {
                'tokenBoost': 'boost',
                'communityTakeover': 'cto',
                'tokenProfile': 'dex_profile',
                'tokenAd': 'dex_ad',
            }
            sig_source = 'validated' if storage_table == 'dex_validated' else 'dex_paid'
            modified_text, dropped, reason = process_message_logic(msg, wf.get('rules', []), context={
                "payer_address": payer_address,
                "token_name": project_name,
                "category": category_key_map.get(order_type, ''),
                "signal_source": sig_source,
                "boost_count": total_boosts,
                "boost_amount": boost_amount,
                "metrics_data": metrics_data,
                "indicators": metrics_data.get('indicators') if metrics_data else {},
            })
            if dropped:
                token_dropped_reasons.append(f"[{wf.get('name') or 'Flow'}]: {reason}")
                continue

            passed_any = True
            send_msg = modified_text if modified_text else msg
            if preview_message is None:
                preview_message = send_msg

            wf_name = str(wf.get('name') or 'Flow').strip()
            if test_mode:
                passed_channels.append(wf_name)
                continue

            t_id = str(wf.get('target_channel_id') or '').strip()
            t_username = wf.get('target_channel') or ''
            wf_target = t_id if t_id else t_username
            final_target = wf_target

            if final_target:
                attempted_channels.append(wf_name or final_target)
                if state.tg_client:
                    try:
                        await safe_send_message(state.tg_client, final_target, send_msg)
                        passed_channels.append(wf_name or final_target)
                        debug_log(f"[REAL-TIME SCANNER] Token {ca} successfully forwarded to target: {final_target} via workflow {wf.get('name')}")
                    except Exception as e:
                        send_failures.append(f"{wf_name or final_target}: {e}")
                        print(f"[REAL-TIME SCANNER] Failed to send to {final_target}: {e}", flush=True)
                else:
                    send_failures.append(f"{wf_name or final_target}: Telegram client is not running/authorized")
                    print(f"[REAL-TIME SCANNER] WARNING: Token {ca} passed filters, but Telegram client is not running/authorized! Cannot send to {final_target}.", flush=True)
            else:
                print(f"[REAL-TIME SCANNER] Workflow '{wf.get('name')}' matched but has no target channel configured. Skipping.", flush=True)

        if passed_any:
            if passed_channels:
                token_info["status"] = "passed"
                token_info["reason"] = ", ".join(passed_channels)
                token_info["formatted_message"] = preview_message or msg
            elif attempted_channels:
                token_info["status"] = "send_failed"
                token_info["reason"] = f"Telegram send failed: {'; '.join(send_failures)}"
                token_info["formatted_message"] = msg
            else:
                token_info["status"] = "skipped"
                token_info["reason"] = "Skipped: Matching workflows had no target channels configured"
        else:
            token_info["status"] = "dropped"
            token_info["reason"] = "; ".join(token_dropped_reasons)
    else:
        token_info["status"] = "dropped"
        token_info["reason"] = "Dropped: No active workflows configured"

    if not test_mode or persist_db:
        result_status = token_info.get("status")
        if result_status == "passed":
            db_status = "detected" if test_mode else "forwarded"
        elif result_status == "dropped":
            db_status = "dropped"
        elif result_status == "send_failed":
            db_status = "send_failed"
        else:
            db_status = "skipped"
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            common_values = (
                ca, project_name, chain_id, mc_str, age_creation_str,
                perf_5m, perf_1h, perf_6h, perf_24h,
                liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                db_status, dex_url, migration_status_formatted, signal_type,
                payment_timestamp, order_status,
            )
            if storage_table == 'dex_validated':
                metrics_json_str = json.dumps(metrics_data) if metrics_data else None
                cursor.execute('''
                    INSERT INTO dex_validated (
                        ca, name, platform, market_cap, age,
                        perf_5m, perf_1h, perf_6h, perf_24h,
                        liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                        status, dex_url, migration_status, signal_type,
                        payment_timestamp, order_status, published_at, fetched_at,
                        detection_seconds, reason, formatted_message, metrics_data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ca, signal_type, payment_timestamp, order_status) DO UPDATE SET
                        name=excluded.name,
                        platform=excluded.platform,
                        market_cap=excluded.market_cap,
                        age=excluded.age,
                        perf_5m=excluded.perf_5m,
                        perf_1h=excluded.perf_1h,
                        perf_6h=excluded.perf_6h,
                        perf_24h=excluded.perf_24h,
                        liquidity_usd=excluded.liquidity_usd,
                        volume_5m=excluded.volume_5m,
                        volume_1h=excluded.volume_1h,
                        volume_6h=excluded.volume_6h,
                        volume_24h=excluded.volume_24h,
                        status=CASE WHEN dex_validated.status = 'forwarded' THEN 'forwarded' ELSE excluded.status END,
                        dex_url=excluded.dex_url,
                        migration_status=excluded.migration_status,
                        published_at=COALESCE(excluded.published_at, dex_validated.published_at),
                        fetched_at=excluded.fetched_at,
                        detection_seconds=COALESCE(excluded.detection_seconds, dex_validated.detection_seconds),
                        reason=excluded.reason,
                        formatted_message=excluded.formatted_message,
                        metrics_data=COALESCE(excluded.metrics_data, dex_validated.metrics_data)
                ''', common_values + (
                    published_at, fetched_at, token_info.get('detection_seconds'), token_info.get('reason'),
                    token_info.get('formatted_message') or msg, metrics_json_str
                ))
                conn.commit()
                # Query back the preserved status so we never downgrade 'forwarded' when pushing to Supabase
                persisted_dv_row = cursor.execute(
                    'SELECT status FROM dex_validated WHERE ca = ? AND signal_type = ? AND payment_timestamp = ? AND order_status = ?',
                    (ca, signal_type, payment_timestamp, order_status)
                ).fetchone()
                if persisted_dv_row and persisted_dv_row[0]:
                    db_status = persisted_dv_row[0]
            else:
                cursor.execute('''
                    INSERT INTO cto_signals (
                        ca, name, platform, market_cap, age,
                        perf_5m, perf_1h, perf_6h, perf_24h,
                        liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                        status, dex_url, migration_status, signal_type,
                        payment_timestamp, order_status, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', common_values + (token_info.get('reason'),))
                conn.commit()

            # Only record to wallet_payments if we have a real, confirmed project token name (not SOL/USDT/USDC/Unknown)
            is_valid_token_name = (
                project_name
                and str(project_name).strip() != ''
                and str(project_name).strip().upper() not in ('UNKNOWN', 'SOL', 'USDT', 'USDC', 'WSOL', 'WRAPPED SOL')
                and ca not in ('So11111111111111111111111111111111111111112', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d')
                and mc_str != 'Unknown'
                and age_creation_str != 'Unknown'
                and bool(dex_url)
            )

            sig = item.get("signature")
            payer = item.get("payer_address")
            send_text = token_info.get("formatted_message") or msg
            amt_val = item.get("amount") if item.get("amount") is not None else (item.get("amount_usd") or payment_amount_usd or 0.0)
            mint_val = item.get("mint") or "SOL"
            ts_val = (payment_timestamp // 1000) if payment_timestamp else int(time.time())
            tracked_val = item.get("tracked_address") or ""

            metrics_json_str = json.dumps(metrics_data) if metrics_data else None

            enrichment_payload = {
                'tracked_address': tracked_val,
                'sender_address': payer,
                'amount': amt_val,
                'mint': mint_val,
                'timestamp': ts_val,
                'token_name': project_name,
                'ca': ca,
                'platform': chain_id,
                'market_cap': mc_str,
                'age': age_creation_str,
                'migration_age': age_migration_str,
                'perf_5m': perf_5m,
                'perf_1h': perf_1h,
                'perf_6h': perf_6h,
                'perf_24h': perf_24h,
                'liquidity_usd': liquidity_usd,
                'volume_5m': volume_5m,
                'volume_1h': volume_1h,
                'volume_6h': volume_6h,
                'volume_24h': volume_24h,
                'dex_url': dex_url,
                'migration_status': migration_status_formatted,
                'status': db_status,
                'reason': token_info.get("reason"),
                'formatted_message': send_text,
                'metrics_data': metrics_json_str
            }

            if is_valid_token_name:
                if sig:
                    cursor.execute("""
                        INSERT INTO wallet_payments (
                            tracked_address, sender_address, amount, mint, signature, timestamp,
                            token_name, ca, platform, market_cap, age, migration_age,
                            perf_5m, perf_1h, perf_6h, perf_24h, dex_url, migration_status,
                            liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                            status, reason, formatted_message, metrics_data
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(signature) DO UPDATE SET
                            tracked_address=COALESCE(excluded.tracked_address, wallet_payments.tracked_address),
                            sender_address=COALESCE(excluded.sender_address, wallet_payments.sender_address),
                            amount=COALESCE(excluded.amount, wallet_payments.amount),
                            token_name=excluded.token_name,
                            ca=excluded.ca,
                            platform=excluded.platform,
                            market_cap=excluded.market_cap,
                            age=excluded.age,
                            migration_age=excluded.migration_age,
                            perf_5m=excluded.perf_5m,
                            perf_1h=excluded.perf_1h,
                            perf_6h=excluded.perf_6h,
                            perf_24h=excluded.perf_24h,
                            liquidity_usd=excluded.liquidity_usd,
                            volume_5m=excluded.volume_5m,
                            volume_1h=excluded.volume_1h,
                            volume_6h=excluded.volume_6h,
                            volume_24h=excluded.volume_24h,
                            dex_url=excluded.dex_url,
                            migration_status=excluded.migration_status,
                            status=excluded.status,
                            reason=excluded.reason,
                            formatted_message=excluded.formatted_message,
                            metrics_data=COALESCE(excluded.metrics_data, wallet_payments.metrics_data)
                    """, (
                        tracked_val, payer, amt_val, mint_val, sig, ts_val,
                        project_name, ca, chain_id, mc_str, age_creation_str, age_migration_str,
                        perf_5m, perf_1h, perf_6h, perf_24h, dex_url, migration_status_formatted,
                        liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                        db_status, token_info.get("reason"), send_text, metrics_json_str
                    ))
                elif payer:
                    existing = cursor.execute("SELECT id FROM wallet_payments WHERE sender_address=? AND (timestamp * 1000 BETWEEN ? AND ?)", (payer, payment_timestamp - 180000, payment_timestamp + 180000)).fetchone()
                    if existing:
                        cursor.execute("""
                            UPDATE wallet_payments
                            SET token_name=?, ca=?, platform=?, market_cap=?, age=?, migration_age=?, perf_5m=?, perf_1h=?, perf_6h=?, perf_24h=?, liquidity_usd=?, volume_5m=?, volume_1h=?, volume_6h=?, volume_24h=?, dex_url=?, migration_status=?, status=?, reason=?, formatted_message=?, metrics_data=?
                            WHERE id=?
                        """, (
                            project_name, ca, chain_id, mc_str, age_creation_str, age_migration_str, perf_5m, perf_1h, perf_6h, perf_24h, liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h, dex_url, migration_status_formatted, db_status, token_info.get("reason"), send_text, metrics_json_str, existing[0]
                        ))
                    else:
                        cursor.execute("""
                            INSERT INTO wallet_payments (
                                tracked_address, sender_address, amount, mint, signature, timestamp,
                                token_name, ca, platform, market_cap, age, migration_age,
                                perf_5m, perf_1h, perf_6h, perf_24h, dex_url, migration_status,
                                liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                                status, reason, formatted_message, metrics_data
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            tracked_val, payer, amt_val, mint_val, None, ts_val,
                            project_name, ca, chain_id, mc_str, age_creation_str, age_migration_str,
                            perf_5m, perf_1h, perf_6h, perf_24h, dex_url, migration_status_formatted,
                            liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                            db_status, token_info.get("reason"), send_text, metrics_json_str
                        ))
                else:
                    existing = cursor.execute("SELECT id FROM wallet_payments WHERE ca=?", (ca,)).fetchone()
                    if existing:
                        cursor.execute("""
                            UPDATE wallet_payments
                            SET token_name=?, platform=?, market_cap=?, age=?, migration_age=?, perf_5m=?, perf_1h=?, perf_6h=?, perf_24h=?, liquidity_usd=?, volume_5m=?, volume_1h=?, volume_6h=?, volume_24h=?, dex_url=?, migration_status=?, status=?, reason=?, formatted_message=?, metrics_data=?
                            WHERE id=?
                        """, (
                            project_name, chain_id, mc_str, age_creation_str, age_migration_str, perf_5m, perf_1h, perf_6h, perf_24h, liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h, dex_url, migration_status_formatted, db_status, token_info.get("reason"), send_text, metrics_json_str, existing[0]
                        ))
                    else:
                        cursor.execute("""
                            INSERT INTO wallet_payments (
                                tracked_address, sender_address, amount, mint, signature, timestamp,
                                token_name, ca, platform, market_cap, age, migration_age,
                                perf_5m, perf_1h, perf_6h, perf_24h, dex_url, migration_status,
                                liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                                status, reason, formatted_message, metrics_data
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            tracked_val, None, amt_val, mint_val, None, ts_val,
                            project_name, ca, chain_id, mc_str, age_creation_str, age_migration_str,
                            perf_5m, perf_1h, perf_6h, perf_24h, dex_url, migration_status_formatted,
                            liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                            db_status, token_info.get("reason"), send_text, metrics_json_str
                        ))

                conn.commit()

                if supabase_client:
                    sp_wallet_payload = dict(enrichment_payload)
                    sp_wallet_payload['metrics_data'] = metrics_data if metrics_data else None
                    if sig:
                        sp_wallet_payload['signature'] = sig
                        push_to_supabase_table('wallet_payments', sp_wallet_payload, on_conflict='signature')
                    elif payer:
                        def _update_supabase_wallet():
                            try:
                                result = supabase_client.table('wallet_payments').update(sp_wallet_payload).eq('sender_address', payer).gte('timestamp', (payment_timestamp - 180000)//1000).lte('timestamp', (payment_timestamp + 180000)//1000).execute()
                                if not result.data:
                                    supabase_client.table('wallet_payments').insert(sp_wallet_payload).execute()
                            except Exception as e:
                                print(f"[SUPABASE] wallet_payments update error: {e}", flush=True)
                        threading.Thread(target=_update_supabase_wallet, daemon=True).start()
                    else:
                        push_to_supabase_table('wallet_payments', sp_wallet_payload, on_conflict='ca')
            else:
                # If a row was previously created for this signature without a token name, delete it
                if sig:
                    cursor.execute("DELETE FROM wallet_payments WHERE signature=?", (sig,))
                    conn.commit()

            if supabase_client:
                signal_payload = {
                    'ca': ca,
                    'name': project_name,
                    'platform': chain_id,
                    'market_cap': mc_str,
                    'age': age_creation_str,
                    'perf_5m': perf_5m,
                    'perf_1h': perf_1h,
                    'perf_6h': perf_6h,
                    'perf_24h': perf_24h,
                    'liquidity_usd': liquidity_usd,
                    'volume_5m': volume_5m,
                    'volume_1h': volume_1h,
                    'volume_6h': volume_6h,
                    'volume_24h': volume_24h,
                    'status': db_status,
                    'dex_url': dex_url,
                    'migration_status': migration_status_formatted,
                    'signal_type': signal_type,
                    'payment_timestamp': payment_timestamp,
                    'order_status': order_status,
                    'reason': token_info.get("reason")
                }
                if storage_table == 'dex_validated':
                    signal_payload.update({
                        'published_at': published_at,
                        'fetched_at': fetched_at,
                        'detection_seconds': token_info.get('detection_seconds'),
                        'formatted_message': token_info.get('formatted_message') or msg,
                        'metrics_data': metrics_data if metrics_data else None,
                    })
                push_to_supabase_table(storage_table, signal_payload)

    return token_info


async def verify_and_forward_cto_token(ca, amount_usd=0.0, payer_address=None):
    try:
        settings = get_settings()
        target_channel = settings.get('cto_target_channel', '')
        workflow_id = settings.get('cto_workflow_id')
        test_mode = settings.get('cto_test_mode', 'false').lower() == 'true'
        fetch_approved = settings.get('cto_fetch_approved', 'true').lower() == 'true'
        fetch_pending = settings.get('cto_fetch_pending', 'true').lower() == 'true'

        orders_url = f"https://api.dexscreener.com/orders/v1/solana/{ca}"
        orders_res = await asyncio.to_thread(requests.get, orders_url, timeout=10)
        if orders_res.status_code == 200:
            orders_data = orders_res.json()
            orders_list = orders_data.get("orders", [])
            for o in orders_list:
                o_status = o.get("status", "").lower()
                o_type = o.get("type", "")
                o_pay_ts = o.get("paymentTimestamp")

                is_match = False
                # Accept all paid order types (profiles, ads, CTOs)
                if o_status == "approved" and fetch_approved:
                    is_match = True
                elif o_status == "processing" and fetch_pending:
                    is_match = True

                if is_match:
                    item = {
                        "tokenAddress": ca,
                        "order_type": o_type,
                        "order_status": o_status,
                        "payment_timestamp": o_pay_ts,
                        "amount_usd": amount_usd,
                        "payer_address": payer_address
                    }
                    debug_log(f"[REAL-TIME SCANNER] Verified matching order ({o_type}) for {ca} on DexScreener API.")
                    await process_single_cto_item(item, target_channel, workflow_id, test_mode)
    except Exception as e:
        print(f"[REAL-TIME SCANNER] Error in verify_and_forward_cto_token for {ca}: {e}", flush=True)
