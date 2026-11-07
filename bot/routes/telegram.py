"""Telegram login/session API."""

import asyncio
from flask import Flask, render_template, request, jsonify

from bot import state
from bot.db.connection import db_lock, get_db, get_settings
from bot.web import app

@app.route('/api/telegram/status', methods=['GET'])
def tg_status():
    if not state.tg_client or not state.telethon_loop:
        return jsonify({"authorized": False})
    try:
        auth = asyncio.run_coroutine_threadsafe(state.tg_client.is_user_authorized(), state.telethon_loop).result(timeout=5)
        return jsonify({"authorized": auth})
    except:
        return jsonify({"authorized": False})

@app.route('/api/telegram/send_code', methods=['POST'])
def tg_send_code():
    phone = request.json.get('phone')
    if not state.tg_client or not state.telethon_loop:
        return jsonify({"success": False, "error": "Bot is initializing. Please wait a few seconds and try again."})

    async def _send():
        if not state.tg_client.is_connected():
            await state.tg_client.connect()

        try:
            result = await state.tg_client.send_code_request(phone)
            # Save hash to DB so it survives restart
            with db_lock:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('phone_code_hash', result.phone_code_hash))
                conn.commit()
            return True
        except Exception as e:
            err_str = str(e)
            if "all available options" in err_str or "ResendCodeRequest" in err_str:
                raise Exception("Telegram restricted code requests for this number. Please check your Telegram app on another device for the code, or wait 24 hours.")
            raise e

    try:
        asyncio.run_coroutine_threadsafe(_send(), state.telethon_loop).result(timeout=15)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/verify_code', methods=['POST'])
def tg_verify_code():
    code = request.json.get('code')
    phone = request.json.get('phone')
    if not state.tg_client or not state.telethon_loop:
        return jsonify({"success": False, "error": "Client not ready"})

    async def _verify():
        # Retrieve hash from DB
        settings = get_settings()
        phone_code_hash = settings.get('phone_code_hash')
        if not phone_code_hash:
            raise Exception("Code hash missing. Please request the code again.")

        await state.tg_client.sign_in(phone, code, phone_code_hash=phone_code_hash)

        # Save session string immediately upon successful verification
        try:
            new_session_str = state.tg_client.session.save()
            if new_session_str:
                with db_lock:
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('telegram_string_session', new_session_str))
                    conn.commit()
                print("[AUTH] Successfully saved Telegram StringSession to database after verification.")
        except Exception as e:
            print(f"[AUTH] Error saving StringSession after verification: {e}")

        return True

    try:
        asyncio.run_coroutine_threadsafe(_verify(), state.telethon_loop).result(timeout=15)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/reset', methods=['POST'])
def tg_reset():
    state.reset_requested = True
    return jsonify({"success": True})

@app.route('/api/telegram/dialogs', methods=['GET'])
def tg_dialogs():
    if not state.tg_client or not state.telethon_loop:
        return jsonify({"success": False, "error": "Client not ready"})

    async def _get_dialogs():
        if not await state.tg_client.is_user_authorized():
            return []
        dialogs = await state.tg_client.get_dialogs(limit=20)
        return [{"id": str(d.id), "name": d.name} for d in dialogs]

    try:
        dialogs = asyncio.run_coroutine_threadsafe(_get_dialogs(), state.telethon_loop).result(timeout=15)
        return jsonify({"success": True, "dialogs": dialogs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/channels', methods=['GET'])
def tg_channels():
    if not state.tg_client or not state.telethon_loop:
        return jsonify({"success": False, "error": "Client not ready"})

    async def _get_channels():
        try:
            if not await state.tg_client.is_user_authorized():
                return []

            # Fetch dialogs with a limit to avoid timeouts
            dialogs = await state.tg_client.get_dialogs(limit=100)

            channels = []
            for d in dialogs:
                if d.is_channel or d.is_group or d.is_user:
                    # Safely get username
                    uname = ""
                    if hasattr(d.entity, 'username') and d.entity.username:
                        uname = d.entity.username

                    chat_type = "Channel"
                    if d.is_group:
                        chat_type = "Group"
                    elif d.is_user:
                        chat_type = "User"

                    channels.append({
                        "id": str(d.id),
                        "name": d.name or "Unnamed",
                        "username": uname,
                        "type": chat_type
                    })
            return channels
        except Exception as e:
            print(f"Error in _get_channels: {e}")
            return []

    try:
        channels = asyncio.run_coroutine_threadsafe(_get_channels(), state.telethon_loop).result(timeout=25)
        return jsonify({"success": True, "channels": channels})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/logout', methods=['POST'])
def tg_logout():
    if not state.tg_client or not state.telethon_loop:
        return jsonify({"success": False, "error": "Client not ready"})

    async def _logout():
        try:
            await state.tg_client.log_out()
        except Exception:
            pass
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM settings WHERE key = 'telegram_string_session'")
            conn.commit()
        return True

    try:
        asyncio.run_coroutine_threadsafe(_logout(), state.telethon_loop).result(timeout=15)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
