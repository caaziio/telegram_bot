import asyncio
import sqlite3
import os
import threading
import re
from datetime import datetime
from telethon import TelegramClient, events
from flask import Flask, render_template, request, jsonify

# Set Flask to use the current dir's templates and static folders
current_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, 
            template_folder=os.path.join(current_dir, 'templates'),
            static_folder=os.path.join(current_dir, 'static'))

# Globals for async bridge
telethon_loop = None
tg_client = None
current_api_id = None
current_api_hash = None
reset_requested = False
handlers_registered = False

DB_PATH = os.path.join(current_dir, 'db/rules.sqlite')

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            source_channel TEXT,
            source_channel_id TEXT,
            target_channel TEXT,
            target_channel_id TEXT,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id INTEGER,
            rule_type TEXT,
            search_text TEXT,
            replace_text TEXT,
            time_min TEXT,
            time_max TEXT,
            FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
        )
    ''')
    
    # Add columns if they were missing from an older schema version
    try:
        cursor.execute('ALTER TABLE rules ADD COLUMN time_min TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE rules ADD COLUMN time_max TEXT')
    except sqlite3.OperationalError:
        pass
        
    try:
        cursor.execute('ALTER TABLE workflows ADD COLUMN name TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE workflows ADD COLUMN source_channel_id TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE workflows ADD COLUMN target_channel_id TEXT')
    except sqlite3.OperationalError:
        pass

    # Add ON DELETE CASCADE support
    cursor.execute('PRAGMA foreign_keys = ON;')
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys for ON DELETE CASCADE to work
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def get_settings():
    conn = get_db()
    cursor = conn.cursor()
    settings = {}
    for row in cursor.execute('SELECT key, value FROM settings').fetchall():
        settings[row[0]] = row[1]
    conn.close()
    return settings

def get_workflows():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    workflows = []
    for wf in cursor.execute('SELECT * FROM workflows').fetchall():
        wf_dict = dict(wf)
        rules = cursor.execute('SELECT * FROM rules WHERE workflow_id = ?', (wf['id'],)).fetchall()
        wf_dict['rules'] = [dict(r) for r in rules]
        workflows.append(wf_dict)
        
    conn.close()
    return workflows

def process_message_logic(text, rules):
    if not text:
        return text, False, ""
        
    processed_text = text
    
    for rule in rules:
        rule_type = rule.get('rule_type')
        print(f"      [RULE] type={rule_type}, search_text={rule.get('search_text')}, time_min={rule.get('time_min')}, time_max={rule.get('time_max')}")
        
        # TOKEN AGE FILTER LOGIC
        if rule_type == 'token_age':
            try:
                min_val = rule.get('time_min')
                max_val = rule.get('time_max')
                min_age = float(min_val) if min_val and str(min_val).strip() != "" else 0
                max_age = float(max_val) if max_val and str(max_val).strip() != "" else 999999
            except (ValueError, TypeError):
                min_age = 0
                max_age = 999999
            
            print(f"      [TOKEN AGE] min_age={min_age}, max_age={max_age}")
            
            # Check for "Just now" or "New" which imply 0 minutes
            if re.search(r'Age\s*[:\-]?\s*(?:Just now|New)', text, re.IGNORECASE):
                token_minutes = 0
            else:
                # Robust regex to handle '1 day, 2 hours, 45 minutes', '1d 4h', '1hr 46min', etc.
                # We look for a pattern following "Age"
                age_body_match = re.search(r'(?:Token )?Age\s*[:\-]?\s*(.*?)(?:\n|$)', text, re.IGNORECASE)
                if age_body_match:
                    age_text = age_body_match.group(1)
                    print(f"      [TOKEN AGE] Extracted age text: '{age_text}'")
                    d = int(re.search(r'(\d+)\s*(?:d|day)', age_text, re.IGNORECASE).group(1) if re.search(r'(\d+)\s*(?:d|day)', age_text, re.IGNORECASE) else 0)
                    h = int(re.search(r'(\d+)\s*(?:h|hr|hour)', age_text, re.IGNORECASE).group(1) if re.search(r'(\d+)\s*(?:h|hr|hour)', age_text, re.IGNORECASE) else 0)
                    m = int(re.search(r'(\d+)\s*(?:m|min|minute)', age_text, re.IGNORECASE).group(1) if re.search(r'(\d+)\s*(?:m|min|minute)', age_text, re.IGNORECASE) else 0)
                    token_minutes = (d * 1440) + (h * 60) + m
                    print(f"      [TOKEN AGE] Parsed: d={d}, h={h}, m={m} → {token_minutes} minutes")
                else:
                    # If no "Age" label is found, try to find a standalone time pattern that looks like an age
                    standalone_match = re.search(r'(?:(\d+)\s*(?:d|day)s?)?\s*,?\s*(?:(\d+)\s*(?:h|hr|hour)s?)?\s*,?\s*(?:(\d+)\s*(?:m|min|minute)s?)', text, re.IGNORECASE)
                    if standalone_match and (standalone_match.group(1) or standalone_match.group(2) or standalone_match.group(3)):
                        d = int(standalone_match.group(1) or 0)
                        h = int(standalone_match.group(2) or 0)
                        m = int(standalone_match.group(3) or 0)
                        token_minutes = (d * 1440) + (h * 60) + m
                    else:
                        # If no token age is found at all, drop if the min_age is > 0
                        if min_age > 0:
                            return None, True, f"Dropped by Token Age Filter (No Age found in text, but min allowed is {min_age}m)"
                        else:
                            token_minutes = None # Allow it to pass if no rules are violated

            if token_minutes is not None:
                if not (min_age <= token_minutes <= max_age):
                    print(f"      [TOKEN AGE] DROPPING: {min_age} <= {token_minutes} <= {max_age} is FALSE")
                    return None, True, f"Dropped by Token Age Filter (Allowed: {min_age}-{max_age}m, Found: {token_minutes}m)"
                else:
                    print(f"      [TOKEN AGE] PASSED: {min_age} <= {token_minutes} <= {max_age}")
        
        # EXTRACT CA LOGIC (Now acts purely as a filter)
        elif rule_type == 'extract_ca':
            ca_regex = r'(?:CA|Contract|ca)[\s:]*([1-9A-HJ-NP-Za-km-z]{32,44}|0x[a-fA-F0-9]{40})'
            match = re.search(ca_regex, text, re.IGNORECASE)
            if not match or not match.group(1):
                return None, True, "Dropped by Extract CA (No Contract Address found in text)"
            else:
                print(f"      [EXTRACT CA] PASSED: Found CA={match.group(1)[:20]}...")

        # PERFORMANCE FILTER LOGIC
        elif rule_type == 'performance':
            timeframe = rule.get('search_text') or '5m'
            threshold_str = rule.get('time_max')
            if threshold_str:
                try:
                    threshold = float(threshold_str)
                    # Look for e.g., '5m: +67%' or '5m: -27%'
                    pattern = rf'{timeframe}:\s*([+\-]?\d+(?:\.\d+)?)%'
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        actual_performance = float(match.group(1))
                        # Drop if performance is greater than or equal to threshold
                        if actual_performance >= threshold:
                            return None, True, f"Dropped by Performance Filter ({timeframe}: {actual_performance}% >= threshold {threshold}%)"
                        else:
                            print(f"      [PERF] PASSED: {timeframe} = {actual_performance}% < {threshold}%")
                    else:
                        print(f"      [PERF] PASSED: No '{timeframe}:' pattern found in text (filter skipped)")
                except ValueError:
                    pass
            else:
                print(f"      [PERF] SKIPPED: No threshold set")

        # WORD FILTER LOGIC
        elif rule_type == 'filter':
            search = rule.get('search_text', '')
            if search and search.lower() in text.lower():
                return None, True, f"Dropped by Word Filter (Found forbidden word: '{search}')"
                
        # REPLACE LOGIC
        elif rule_type == 'replace':
            search = rule.get('search_text', '')
            replace = rule.get('replace_text', '')
            if search:
                processed_text = processed_text.replace(search, replace)
                
        # APPEND LOGIC
        elif rule_type == 'append':
            replace = rule.get('replace_text', '')
            if replace:
                processed_text += f"\n\n{replace}"
                
    print(f"      [RESULT] All rules passed! Message will be forwarded.")
    return processed_text, False, ""

def process_message(text, rules, message_date):
    result_text, dropped, reason = process_message_logic(text, rules)
    return result_text

# ================= FLASK ROUTES =================

@app.route('/')
def index():
    workflows = get_workflows()
    settings = get_settings()
    return render_template('index.html', workflows=workflows, settings=settings)

@app.route('/api/workflows', methods=['POST'])
def create_workflow():
    try:
        data = request.json
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO workflows (name, source_channel, source_channel_id, target_channel, target_channel_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (data.get('name'), data.get('source_channel'), data.get('source_channel_id'), data.get('target_channel'), data.get('target_channel_id'), 1))
        
        wf_id = cursor.lastrowid
        
        for rule in data.get('rules', []):
            cursor.execute('''
                INSERT INTO rules (workflow_id, rule_type, search_text, replace_text, time_min, time_max)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (wf_id, rule.get('rule_type'), rule.get('search_text'), rule.get('replace_text'), rule.get('time_min'), rule.get('time_max')))
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "id": wf_id})
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/workflows/<int:id>', methods=['PUT'])
def update_workflow(id):
    try:
        data = request.json
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE workflows 
            SET name=?, source_channel=?, source_channel_id=?, target_channel=?, target_channel_id=?
            WHERE id=?
        ''', (data.get('name'), data.get('source_channel'), data.get('source_channel_id'), data.get('target_channel'), data.get('target_channel_id'), id))
        
        cursor.execute('DELETE FROM rules WHERE workflow_id=?', (id,))
        
        for rule in data.get('rules', []):
            cursor.execute('''
                INSERT INTO rules (workflow_id, rule_type, search_text, replace_text, time_min, time_max)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (id, rule.get('rule_type'), rule.get('search_text'), rule.get('replace_text'), rule.get('time_min'), rule.get('time_max')))
            
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/workflows/<int:id>/toggle', methods=['POST'])
def toggle_workflow(id):
    conn = get_db()
    cursor = conn.cursor()
    wf = cursor.execute('SELECT is_active FROM workflows WHERE id=?', (id,)).fetchone()
    if not wf:
        return jsonify({"success": False})
    
    new_status = 0 if wf[0] else 1
    cursor.execute('UPDATE workflows SET is_active=? WHERE id=?', (new_status, id))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "is_active": bool(new_status)})

@app.route('/api/workflows/<int:id>', methods=['DELETE'])
def delete_workflow(id):
    try:
        conn = get_db()
        cursor = conn.cursor()
        # Explicitly delete rules first just in case
        cursor.execute('DELETE FROM rules WHERE workflow_id=?', (id,))
        cursor.execute('DELETE FROM workflows WHERE id=?', (id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/settings', methods=['POST'])
def save_settings():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    for key, value in data.items():
        cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/tester', methods=['POST'])
def run_tester():
    data = request.json
    wf_id = data.get('workflow_id')
    text = data.get('text')
    
    workflows = get_workflows()
    wf = next((w for w in workflows if w['id'] == int(wf_id)), None)
    
    if not wf:
        return jsonify({"dropped": True, "reason": "Workflow not found"})
        
    result_text, dropped, reason = process_message_logic(text, wf['rules'])
    
    return jsonify({
        "dropped": dropped,
        "reason": reason,
        "text": result_text
    })

@app.route('/api/telegram/status', methods=['GET'])
def tg_status():
    if not tg_client or not telethon_loop:
        return jsonify({"authorized": False})
    try:
        auth = asyncio.run_coroutine_threadsafe(tg_client.is_user_authorized(), telethon_loop).result(timeout=5)
        return jsonify({"authorized": auth})
    except:
        return jsonify({"authorized": False})

@app.route('/api/telegram/send_code', methods=['POST'])
def tg_send_code():
    phone = request.json.get('phone')
    if not tg_client or not telethon_loop:
        return jsonify({"success": False, "error": "Bot is initializing. Please wait a few seconds and try again."})
        
    async def _send():
        if not tg_client.is_connected():
            await tg_client.connect()
        
        try:
            result = await tg_client.send_code_request(phone)
            # Save hash to DB so it survives restart
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('phone_code_hash', result.phone_code_hash))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            err_str = str(e)
            if "all available options" in err_str or "ResendCodeRequest" in err_str:
                raise Exception("Telegram restricted code requests for this number. Please check your Telegram app on another device for the code, or wait 24 hours.")
            raise e

    try:
        asyncio.run_coroutine_threadsafe(_send(), telethon_loop).result(timeout=15)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/verify_code', methods=['POST'])
def tg_verify_code():
    code = request.json.get('code')
    phone = request.json.get('phone')
    if not tg_client or not telethon_loop:
        return jsonify({"success": False, "error": "Client not ready"})
        
    async def _verify():
        # Retrieve hash from DB
        settings = get_settings()
        phone_code_hash = settings.get('phone_code_hash')
        if not phone_code_hash:
            raise Exception("Code hash missing. Please request the code again.")
            
        await tg_client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        return True

    try:
        asyncio.run_coroutine_threadsafe(_verify(), telethon_loop).result(timeout=15)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/reset', methods=['POST'])
def tg_reset():
    global reset_requested
    reset_requested = True
    return jsonify({"success": True})

@app.route('/api/telegram/dialogs', methods=['GET'])
def tg_dialogs():
    if not tg_client or not telethon_loop:
        return jsonify({"success": False, "error": "Client not ready"})
        
    async def _get_dialogs():
        if not await tg_client.is_user_authorized():
            return []
        dialogs = await tg_client.get_dialogs(limit=20)
        return [{"id": str(d.id), "name": d.name} for d in dialogs]
        
    try:
        dialogs = asyncio.run_coroutine_threadsafe(_get_dialogs(), telethon_loop).result(timeout=15)
        return jsonify({"success": True, "dialogs": dialogs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/channels', methods=['GET'])
def tg_channels():
    if not tg_client or not telethon_loop:
        return jsonify({"success": False, "error": "Client not ready"})
        
    async def _get_channels():
        try:
            if not await tg_client.is_user_authorized():
                return []
            
            # Fetch dialogs with a limit to avoid timeouts
            dialogs = await tg_client.get_dialogs(limit=100)
            
            channels = []
            for d in dialogs:
                if d.is_channel or d.is_group:
                    # Safely get username
                    uname = ""
                    if hasattr(d.entity, 'username') and d.entity.username:
                        uname = d.entity.username
                    
                    channels.append({
                        "id": str(d.id),
                        "name": d.name or "Unnamed",
                        "username": uname
                    })
            return channels
        except Exception as e:
            print(f"Error in _get_channels: {e}")
            return []
        
    try:
        channels = asyncio.run_coroutine_threadsafe(_get_channels(), telethon_loop).result(timeout=25)
        return jsonify({"success": True, "channels": channels})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/logout', methods=['POST'])
def tg_logout():
    if not tg_client or not telethon_loop:
        return jsonify({"success": False, "error": "Client not ready"})
        
    async def _logout():
        await tg_client.log_out()
        return True

    try:
        asyncio.run_coroutine_threadsafe(_logout(), telethon_loop).result(timeout=15)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

def run_flask_app():
    print("Starting Web Dashboard on http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, use_reloader=False)

def normalize_channel_id(raw_id):
    """Strip the Telegram -100 prefix to get the bare channel ID for comparison.
    Telethon's chat.id returns bare IDs, but the dashboard may store the full -100... form.
    We normalize both sides so comparison always works."""
    s = str(raw_id).strip()
    # Remove leading minus
    if s.startswith('-'):
        s = s[1:]
    # Remove the '100' prefix that Telegram uses for channels/supergroups
    if s.startswith('100') and len(s) > 10:
        s = s[3:]
    return s

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
        
        print(f"\n--- [NEW MESSAGE] ---")
        print(f"From Chat ID: '{actual_chat_id}' (normalized: '{normalized_actual_id}')")
        print(f"From Username: '{chat_username}'")
        print(f"Message Text: {repr(event.text[:200] if event.text else '')}")
        
        for wf in workflows:
            if not wf.get('is_active'):
                continue
                
            s_username = (wf.get('source_channel') or '').replace('@', '')
            s_id = str(wf.get('source_channel_id') or '').strip()
            normalized_s_id = normalize_channel_id(s_id) if s_id else ''
            
            print(f"  Checking Workflow '{wf.get('name')}':")
            print(f"    Expected Source ID: '{s_id}' (normalized: '{normalized_s_id}')")
            print(f"    Expected Source Username: '{s_username}'")
            
            match = False
            # Match by normalized ID (handles -100 prefix mismatch)
            if normalized_s_id and normalized_actual_id == normalized_s_id:
                match = True
                print("    -> Match by ID!")
            elif s_username and chat_username and chat_username.lower() == s_username.lower():
                match = True
                print("    -> Match by Username!")
                
            # If the user saved a channel NAME instead of username in the 'source_channel' field
            # We can also check if the name matches the chat title!
            chat_title = getattr(chat, 'title', '') or ''
            if not match and chat_title and s_username and chat_title.lower() == s_username.lower():
                match = True
                print("    -> Match by Chat Title (Name)!")
            
            if match:
                print(f"    Proceeding to process message with {len(wf['rules'])} rules...")
                modified_text, dropped, reason = process_message_logic(event.text, wf['rules'])
                
                if dropped:
                    print(f"    Message dropped due to filter rules. Reason: {reason}")
                elif modified_text:
                    t_username = wf.get('target_channel') or ''
                    t_id = str(wf.get('target_channel_id') or '').strip()
                    target = t_id if t_id else t_username
                    
                    if target:
                        print(f"    Sending to target: '{target}'")
                        try:
                            # If target is string representation of int (e.g. "-100...")
                            if target.lstrip('-').isdigit():
                                target_entity = int(target)
                            else:
                                target_entity = target
                            
                            await client.send_message(target_entity, modified_text)
                            print(f"    Successfully forwarded to {target}")
                        except Exception as e:
                            print(f"    Failed to forward message to {target}: {e}")
                else:
                    print("    Message text was empty after processing.")


async def main():
    global telethon_loop, tg_client, current_api_id, current_api_hash, handlers_registered
    telethon_loop = asyncio.get_running_loop()
    
    print("Initializing Database...")
    init_db()
    
    # Start Flask dashboard in background
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    
    print("Waiting for Telegram settings...")
    
    try:
        while True:
            try:
                global reset_requested
                settings = get_settings()
                api_id = str(settings.get('api_id', '')).strip()
                api_hash = str(settings.get('api_hash', '')).strip()
                
                # Handle Reset Request
                if reset_requested:
                    print("Reset requested! Clearing session...")
                    if tg_client:
                        await tg_client.disconnect()
                        tg_client = None
                    handlers_registered = False
                    if os.path.exists('userbot_session.session'):
                        os.remove('userbot_session.session')
                    
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM settings WHERE key IN ('phone_code_hash')")
                    conn.commit()
                    conn.close()
                    
                    reset_requested = False
                    print("Reset complete.")
                    continue

                if not api_id or not api_hash:
                    await asyncio.sleep(2)
                    continue

                # If client exists but credentials changed, disconnect and recreate
                if tg_client:
                    if api_id != str(current_api_id) or api_hash != str(current_api_hash):
                        print("API Credentials changed! Restarting Telegram Client...")
                        await tg_client.disconnect()
                        tg_client = None
                        handlers_registered = False
                    elif await tg_client.is_user_authorized():
                        # Client is running and authorized, just wait
                        await asyncio.sleep(5)
                        continue

                if not tg_client:
                    print(f"Starting Telegram Client with API ID: {api_id}")
                    try:
                        current_api_id = api_id
                        current_api_hash = api_hash
                        tg_client = TelegramClient('userbot_session', int(api_id), api_hash)
                        await tg_client.connect()
                    except Exception as e:
                        print(f"Failed to initialize Telegram Client: {e}")
                        tg_client = None
                        await asyncio.sleep(5)
                        continue

                if await tg_client.is_user_authorized():
                    print("Userbot is authorized and running!")
                    if not handlers_registered:
                        register_handlers(tg_client)
                        handlers_registered = True
                        print("Event handlers registered.")
                    # This will run until disconnected or credentials change
                    while tg_client and await tg_client.is_user_authorized():
                        # Check for credential change or reset request every 5 seconds
                        if reset_requested:
                            break
                        settings = get_settings()
                        new_id = str(settings.get('api_id', '')).strip()
                        new_hash = str(settings.get('api_hash', '')).strip()
                        if new_id != str(current_api_id) or new_hash != str(current_api_hash):
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

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping bot gracefully...")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
