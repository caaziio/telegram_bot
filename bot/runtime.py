"""Telegram client lifecycle, message handlers and Flask thread startup."""

import asyncio
import os
import threading
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from bot import state
from bot.async_bridge import MAX_CONCURRENT_BACKGROUND_JOBS
from bot.config import DEBUG_LOGGING, PERSISTENT_DATA_DIR, debug_log
from bot.db.connection import db_lock, get_db, get_settings
from bot.db.startup import init_db
from bot.db.supabase_sync import supabase_sync_loop
from bot.engine.rules import get_workflows, process_message_logic
from bot.services.dex_scanner import cto_auto_scanner_loop
from bot.services.helius_monitor import helius_polling_loop
from bot.services.performance import post_signal_perf_loop
from bot.services.telegram_utils import normalize_channel_id, safe_send_message
from bot.services.wallet_tracker import wallet_tracker_polling_loop
from bot.web import app

def run_flask_app():
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Web Dashboard on http://localhost:{port}")
    if not DEBUG_LOGGING:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port, use_reloader=False, threaded=True)


def register_handlers(client):
    @client.on(events.NewMessage)
    async def handler(event):
        workflows = get_workflows()
        chat = await event.get_chat()

        # Determine actual chat ID
        actual_chat_id = ""
        if chat:
            actual_chat_id = str(chat.id)
        else:
            actual_chat_id = str(event.chat_id)

        # Normalize for reliable matching
        normalized_actual_id = normalize_channel_id(actual_chat_id)

        chat_username = getattr(chat, 'username', '') or ''

        debug_log("\n--- [NEW MESSAGE] ---")
        debug_log(f"From Chat ID: '{actual_chat_id}' (normalized: '{normalized_actual_id}')")
        debug_log(f"From Username: '{chat_username}'")
        debug_log(f"Message Text: {repr(event.text[:200] if event.text else '')}")

        for wf in workflows:
            if not wf.get('is_active'):
                continue

            s_username = (wf.get('source_channel') or '').replace('@', '')
            s_id = str(wf.get('source_channel_id') or '').strip()
            normalized_s_id = normalize_channel_id(s_id) if s_id else ''

            debug_log(f"  Checking Workflow '{wf.get('name')}':")
            debug_log(f"    Expected Source ID: '{s_id}' (normalized: '{normalized_s_id}')")
            debug_log(f"    Expected Source Username: '{s_username}'")

            match = False
            # Match by normalized ID (handles -100 prefix mismatch)
            if normalized_s_id and normalized_actual_id == normalized_s_id:
                match = True
                debug_log("    -> Match by ID!")
            elif s_username and chat_username and chat_username.lower() == s_username.lower():
                match = True
                debug_log("    -> Match by Username!")

            # If the user saved a channel/chat NAME instead of username in the 'source_channel' field
            # We can also check if the name matches the chat title or user display name!
            chat_title = getattr(chat, 'title', '') or ''
            if not chat_title and chat:
                if hasattr(chat, 'first_name') and chat.first_name:
                    chat_title = chat.first_name
                    if hasattr(chat, 'last_name') and chat.last_name:
                        chat_title += f" {chat.last_name}"
                elif hasattr(chat, 'username') and chat.username:
                    chat_title = chat.username

            if not match and chat_title and s_username and chat_title.lower() == s_username.lower():
                match = True
                debug_log("    -> Match by Chat Title/Name!")

            if match:
                debug_log(f"    Proceeding to process message with {len(wf['rules'])} rules...")
                modified_text, dropped, reason = process_message_logic(event.text, wf['rules'])

                if dropped:
                    debug_log(f"    Message dropped due to filter rules. Reason: {reason}")
                elif modified_text:
                    t_username = wf.get('target_channel') or ''
                    t_id = str(wf.get('target_channel_id') or '').strip()
                    target = t_id if t_id else t_username

                    if target:
                        debug_log(f"    Sending to target: '{target}'")
                        try:
                            await safe_send_message(client, target, modified_text)
                            debug_log(f"    Successfully forwarded to {target}")
                        except Exception as e:
                            print(f"    Failed to forward message to {target}: {e}")
                else:
                    debug_log("    Message text was empty after processing.")


async def main():
    state.telethon_loop = asyncio.get_running_loop()
    state.background_job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_BACKGROUND_JOBS)

    # Start Flask dashboard in background immediately so health checks pass
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()

    print("Initializing Database...")
    init_db()

    print("Waiting for Telegram settings...")

    # Start auto-scanner loops
    asyncio.create_task(cto_auto_scanner_loop())
    asyncio.create_task(helius_polling_loop())
    asyncio.create_task(wallet_tracker_polling_loop())
    asyncio.create_task(supabase_sync_loop())

    # The 1h/4h OHLCV scanner is slow by design (GeckoTerminal rate limits) and was
    # being starved by the busy WebSocket/Helius work on this loop. Give it its own
    # thread, event loop and default thread pool so it always makes progress.
    threading.Thread(
        target=lambda: asyncio.run(post_signal_perf_loop()),
        name="perf-scanner",
        daemon=True,
    ).start()

    try:
        while True:
            try:
                settings = get_settings()
                api_id = str(settings.get('api_id', '')).strip()
                api_hash = str(settings.get('api_hash', '')).strip()

                session_path = 'userbot_session'
                if PERSISTENT_DATA_DIR:
                    session_path = os.path.join(PERSISTENT_DATA_DIR, 'userbot_session')

                # Handle Reset Request
                if state.reset_requested:
                    print("Reset requested! Clearing session...")
                    if state.tg_client:
                        await state.tg_client.disconnect()
                        state.tg_client = None
                    state.handlers_registered = False
                    session_file = session_path + '.session'
                    if os.path.exists(session_file):
                        os.remove(session_file)

                    with db_lock:
                        conn = get_db()
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM settings WHERE key IN ('phone_code_hash', 'telegram_string_session')")
                        conn.commit()

                    state.reset_requested = False
                    print("Reset complete.")
                    continue

                if not api_id or not api_hash:
                    await asyncio.sleep(2)
                    continue

                # If client exists but credentials changed, disconnect and recreate
                if state.tg_client:
                    if api_id != str(state.current_api_id) or api_hash != str(state.current_api_hash):
                        print("API Credentials changed! Restarting Telegram Client...")
                        await state.tg_client.disconnect()
                        state.tg_client = None
                        state.handlers_registered = False
                    elif await state.tg_client.is_user_authorized():
                        # Client is running and authorized, just wait
                        await asyncio.sleep(5)
                        continue

                if not state.tg_client:
                    print(f"Starting Telegram Client with API ID: {api_id}")
                    try:
                        state.current_api_id = api_id
                        state.current_api_hash = api_hash

                        # Retrieve saved string session if available
                        string_session_val = settings.get('telegram_string_session', '').strip()
                        if string_session_val:
                            print("Found saved Telegram session in database. Using StringSession...")
                            state.tg_client = TelegramClient(StringSession(string_session_val), int(api_id), api_hash)
                        else:
                            print("No saved session in database. Using file-based session...")
                            state.tg_client = TelegramClient(session_path, int(api_id), api_hash)

                        await state.tg_client.connect()
                    except Exception as e:
                        print(f"Failed to initialize Telegram Client: {e}")
                        state.tg_client = None
                        await asyncio.sleep(5)
                        continue

                if await state.tg_client.is_user_authorized():
                    print("Userbot is authorized and running!")

                    # Auto-persist session to database if not already saved
                    string_session_val = settings.get('telegram_string_session', '').strip()
                    if not string_session_val:
                        try:
                            new_session_str = state.tg_client.session.save()
                            if new_session_str:
                                with db_lock:
                                    conn = get_db()
                                    cursor = conn.cursor()
                                    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('telegram_string_session', new_session_str))
                                    conn.commit()
                                print("Successfully persisted Telegram session string to database settings!")
                        except Exception as e:
                            print(f"Error persisting session string: {e}")

                    if not state.handlers_registered:
                        register_handlers(state.tg_client)
                        state.handlers_registered = True
                        print("Event handlers registered.")
                    # This will run until disconnected or credentials change
                    while state.tg_client and await state.tg_client.is_user_authorized():
                        # Check for credential change or reset request every 5 seconds
                        if state.reset_requested:
                            break
                        settings = get_settings()
                        new_id = str(settings.get('api_id', '')).strip()
                        new_hash = str(settings.get('api_hash', '')).strip()
                        if new_id != str(state.current_api_id) or new_hash != str(state.current_api_hash):
                            break
                        await asyncio.sleep(5)
                else:
                    # Wait for auth via web dashboard
                    await asyncio.sleep(2)

            except Exception as e:
                print(f"Error in main loop: {e}")
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass # Normal shutdown
