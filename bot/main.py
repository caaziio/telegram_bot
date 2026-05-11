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
phone_code_hash = None

DB_PATH = os.path.join(current_dir, '../db/rules.sqlite')

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
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA foreign_keys = ON;')
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
        
        # TOKEN AGE FILTER LOGIC
        if rule_type == 'token_age':
            min_age = float(rule.get('time_min') or 0)
            max_age = float(rule.get('time_max') or 999999)
            
            # Robust regex to handle 'Token Age: 1h 46m', 'Age: 1hr 46min', '1 day 2 hrs', etc.
            age_match = re.search(r'(?:Token )?Age\s*[:\-]?\s*(?:(\d+)\s*(?:d|day|days))?\s*(?:(\d+)\s*(?:h|hr|hrs))?\s*(?:(\d+)\s*(?:m|min|mins))?', text, re.IGNORECASE)
            
            if age_match:
                d = int(age_match.group(1) or 0)
                h = int(age_match.group(2) or 0)
                m = int(age_match.group(3) or 0)
                token_minutes = (d * 1440) + (h * 60) + m
                
                if not (min_age <= token_minutes <= max_age):
                    return None, True, f"Dropped by Token Age Filter (Allowed: {min_age}-{max_age}m, Found: {token_minutes}m)"
            else:
                # If no token age is found at all, drop if the min_age is > 0
                if min_age > 0:
                    return None, True, f"Dropped by Token Age Filter (No Age found in text, but min allowed is {min_age}m)"
        
        # EXTRACT CA LOGIC (Now acts purely as a filter)
        elif rule_type == 'extract_ca':
            ca_regex = r'(?:CA|Contract|ca)[\s:]*([1-9A-HJ-NP-Za-km-z]{32,44}|0x[a-fA-F0-9]{40})'
            match = re.search(ca_regex, text, re.IGNORECASE)
            if not match or not match.group(1):
                return None, True, "Dropped by Extract CA (No Contract Address found in text)"

        # PERFORMANCE FILTER LOGIC
        elif rule_type == 'performance':
            timeframe = rule.get('search_text') or '5m'
            threshold_str = rule.get('time_max')
            if threshold_str:
                try:
                    threshold = float(threshold_str)
                    # Look for e.g., '5m: +67%' or '5m: -27%'
                    pattern = rf'{timeframe}:\s*([\+\-]?\d+(?:\.\d+)?)%'
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        actual_performance = float(match.group(1))
                        # Drop if performance is greater than or equal to threshold
                        if actual_performance >= threshold:
                            return None, True, f"Dropped by Performance Filter ({timeframe}: {actual_performance}% >= threshold {threshold}%)"
                except ValueError:
                    pass

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
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM workflows WHERE id=?', (id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

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
        global phone_code_hash
        if not tg_client.is_connected():
            await tg_client.connect()
        result = await tg_client.send_code_request(phone)
        phone_code_hash = result.phone_code_hash
        return True

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
        await tg_client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        return True

    try:
        asyncio.run_coroutine_threadsafe(_verify(), telethon_loop).result(timeout=15)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

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
        if not await tg_client.is_user_authorized():
            return []
        dialogs = await tg_client.get_dialogs()
        # Filter for channels and groups
        channels = [
            {"id": str(d.id), "name": d.name, "username": getattr(d.entity, 'username', '')}
            for d in dialogs if d.is_channel or d.is_group
        ]
        return channels
        
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
            
        chat_username = getattr(chat, 'username', '') or ''
        
        print(f"\n--- [NEW MESSAGE] ---")
        print(f"From Chat ID: '{actual_chat_id}'")
        print(f"From Username: '{chat_username}'")
        print(f"Message Text: {repr(event.text)}")
        
        for wf in workflows:
            if not wf.get('is_active'):
                continue
                
            s_username = (wf.get('source_channel') or '').replace('@', '')
            s_id = str(wf.get('source_channel_id') or '').strip()
            
            print(f"  Checking Workflow '{wf.get('name')}':")
            print(f"    Expected Source ID: '{s_id}'")
            print(f"    Expected Source Username: '{s_username}'")
            
            match = False
            if s_id and actual_chat_id == s_id:
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
    global telethon_loop, tg_client
    telethon_loop = asyncio.get_running_loop()
    
    print("Initializing Database...")
    init_db()
    
    # Start Flask dashboard in background
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    
    api_id = None
    api_hash = None
    phone = None
    
    print("Waiting for Telegram settings...")
    
    while True:
        settings = get_settings()
        api_id = settings.get('api_id', os.environ.get('TG_API_ID'))
        api_hash = settings.get('api_hash', os.environ.get('TG_API_HASH'))
        phone = settings.get('phone', '')
        
        if api_id and api_hash:
            break
        await asyncio.sleep(2)

    print("Settings found! Starting Telegram Userbot...")
    try:
        api_id = int(api_id)
    except:
        print("API ID must be a number! Check Dashboard settings.")
        while True:
            await asyncio.sleep(1)

    tg_client = TelegramClient('userbot_session', api_id, api_hash)
    await tg_client.connect()
    
    if await tg_client.is_user_authorized():
        print("Userbot is already authorized and running!")
        register_handlers(tg_client)
        await tg_client.run_until_disconnected()
    else:
        print("Userbot is waiting for authorization via Web Dashboard...")
        # Keep loop alive so the web dashboard can trigger sign-in
        while True:
            await asyncio.sleep(1)
            if await tg_client.is_user_authorized():
                print("Authorization complete! Listening for messages.")
                register_handlers(tg_client)
                await tg_client.run_until_disconnected()
                break

if __name__ == '__main__':
    asyncio.run(main())
