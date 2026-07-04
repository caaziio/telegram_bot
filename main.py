import asyncio
import sqlite3
import os
import threading
import re
from datetime import datetime, timezone
import time
import requests
from telethon import TelegramClient, events
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import turso

load_dotenv()

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

# Cache to enforce rate-limiting for Dexscreener orders checks
# Key: (chainId, tokenAddress), Value: timestamp (float)
orders_check_cache = {}

# Cache to avoid duplicate network requests for token symbol/name
token_metadata_cache = {}

# Cache to avoid duplicate network requests for token USD price
# Key: mint, Value: (price_usd, cache_timestamp)
token_price_cache = {}

def get_token_price_usd(mint):
    if not mint:
        return 0.0
        
    search_mint = mint
    if mint == "SOL":
        search_mint = "So11111111111111111111111111111111111111112"
        
    # Stablecoins USDC/USDT are $1.0
    if search_mint in ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d'):
        return 1.0
        
    now = time.time()
    if search_mint in token_price_cache:
        price, ts = token_price_cache[search_mint]
        if now - ts < 60:
            return price
            
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{search_mint}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            pairs = data.get("pairs")
            if pairs and isinstance(pairs, list):
                # Filter by Solana chain only to avoid matching native tokens on other chains (e.g. Wrapped Fogo on Fogo chain using same address)
                solana_pairs = [p for p in pairs if p.get("chainId", "").lower() == "solana"]
                if solana_pairs:
                    # 1. Look for a pair where search_mint is baseToken and quote is USDC/USDT
                    for p in solana_pairs:
                        base_addr = p.get("baseToken", {}).get("address")
                        quote_sym = p.get("quoteToken", {}).get("symbol", "").upper()
                        if base_addr == search_mint and quote_sym in ("USDC", "USDT"):
                            price_str = p.get("priceUsd")
                            if price_str:
                                price = float(price_str)
                                token_price_cache[search_mint] = (price, now)
                                return price
                    
                    # 2. Look for any pair where search_mint is baseToken
                    for p in solana_pairs:
                        base_addr = p.get("baseToken", {}).get("address")
                        if base_addr == search_mint:
                            price_str = p.get("priceUsd")
                            if price_str:
                                price = float(price_str)
                                token_price_cache[search_mint] = (price, now)
                                return price
                                
                    # 3. Fallback to first Solana pair priceUsd
                    for p in solana_pairs:
                        price_str = p.get("priceUsd")
                        if price_str:
                            price = float(price_str)
                            token_price_cache[search_mint] = (price, now)
                            return price
    except Exception as e:
        print(f"[PRICE FETCH] Error fetching price for {search_mint}: {e}", flush=True)
        
    # Fallback to expired cache
    if search_mint in token_price_cache:
        return token_price_cache[search_mint][0]
        
    # If SOL price fetch failed entirely, fallback to a sensible baseline
    if search_mint == "So11111111111111111111111111111111111111112":
        return 140.0
        
    return 0.0

def get_token_metadata(mint):
    if not mint:
        return ""
    if mint in token_metadata_cache:
        return token_metadata_cache[mint]
    
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            pairs = data.get("pairs")
            if pairs and isinstance(pairs, list) and len(pairs) > 0:
                base_token = pairs[0].get("baseToken", {})
                symbol = base_token.get("symbol", "")
                name = base_token.get("name", "")
                if symbol:
                    display_name = f"{symbol} ({name})" if name else symbol
                    token_metadata_cache[mint] = display_name
                    return display_name
    except Exception as e:
        print(f"[METADATA FETCH] Error fetching token metadata for {mint}: {e}", flush=True)
        
    return ""

DB_PATH = os.path.join(current_dir, 'db/bot.sqlite')
TURSO_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

class CustomRow(dict):
    def __init__(self, cursor, row):
        for idx, col in enumerate(cursor.description):
            self[col[0]] = row[idx]
        self._row = row

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._row[key]
        return super().__getitem__(key)

def custom_row_factory(cursor, row):
    return CustomRow(cursor, row)

DB_PATH = os.path.join(current_dir, 'db/bot.sqlite')
TURSO_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

db_lock = threading.RLock()
global_conn = None
last_pull_time = 0

def pull_database_if_needed(conn):
    global last_pull_time
    if TURSO_URL and (TURSO_URL.startswith("libsql://") or TURSO_URL.startswith("https://")):
        now = time.time()
        if now - last_pull_time >= 10:
            with db_lock:
                try:
                    conn.pull()
                    last_pull_time = now
                except Exception as e:
                    print(f"[TURSO] Warning: Failed to pull remote updates: {e}", flush=True)
    
def get_db():
    global global_conn, last_pull_time
    
    # We must lock the initialization to prevent race conditions
    with db_lock:
        if global_conn is not None:
            return global_conn
            
        # Ensure the parent directory for the local database file exists
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        
        if TURSO_URL and (TURSO_URL.startswith("libsql://") or TURSO_URL.startswith("https://")):
            import turso.sync
            # turso.sync only allows ONE connection per file, so we make it global
            global_conn = turso.sync.connect(DB_PATH, remote_url=TURSO_URL, auth_token=TURSO_TOKEN)
            
            # Pull latest changes from remote Turso database on startup
            try:
                global_conn.pull()
                last_pull_time = time.time()
                print("[TURSO] Successfully pulled latest database state from Turso cloud on startup.", flush=True)
            except Exception as e:
                print(f"[TURSO] Warning: Failed to pull from Turso on startup: {e}", flush=True)
            
            # Override commit to automatically push changes to the cloud safely
            original_commit = global_conn.commit
            def auto_push_commit():
                with db_lock:
                    original_commit()
                    try:
                        global_conn.push()
                    except Exception as e:
                        print(f"Warning: Failed to push to Turso: {e}")
            global_conn.commit = auto_push_commit
        else:
            global_conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
        
        global_conn.row_factory = custom_row_factory
        
        # In a shared connection setup, we can't let individual threads close the DB!
        return global_conn

def init_db():
    with db_lock:
        conn = get_db()
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cto_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ca TEXT,
                name TEXT,
                platform TEXT,
                market_cap TEXT,
                age TEXT,
                perf_5m REAL,
                perf_1h REAL,
                perf_6h REAL,
                perf_24h REAL,
                status TEXT,
                dex_url TEXT,
                migration_status TEXT,
                workflow_id INTEGER,
                signal_type TEXT DEFAULT 'approved',
                payment_timestamp INTEGER,
                order_status TEXT,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Add columns if they were missing from an older schema version
        try:
            cursor.execute('ALTER TABLE rules ADD COLUMN time_min TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE rules ADD COLUMN time_max TEXT')
        except Exception:
            pass
            
        try:
            cursor.execute('ALTER TABLE workflows ADD COLUMN name TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE workflows ADD COLUMN source_channel_id TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE workflows ADD COLUMN target_channel_id TEXT')
        except Exception:
            pass
    
        # Add new cto_signals columns if they are missing from an older schema version
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN name TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN platform TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN market_cap TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN dex_url TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN migration_status TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN workflow_id INTEGER')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN signal_type TEXT DEFAULT "approved"')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN payment_timestamp INTEGER')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN order_status TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE cto_signals ADD COLUMN reason TEXT')
        except Exception:
            pass
    
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wallet_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracked_address TEXT,
                sender_address TEXT,
                amount REAL,
                mint TEXT,
                signature TEXT UNIQUE,
                timestamp INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    
        # Add ON DELETE CASCADE support
        cursor.execute('PRAGMA foreign_keys = ON;')
        conn.commit()

def get_settings():
    with db_lock:
        conn = get_db()
        pull_database_if_needed(conn)
        cursor = conn.cursor()
        settings = {}
        for row in cursor.execute('SELECT key, value FROM settings').fetchall():
            settings[row[0]] = row[1]
            
        # Fallback to environment variables if not set in DB settings
        if 'helius_api_key' not in settings and os.environ.get('HELIUS_API_KEY'):
            settings['helius_api_key'] = os.environ.get('HELIUS_API_KEY')
            
        return settings

def get_workflows():
    with db_lock:
        conn = get_db()
        pull_database_if_needed(conn)
        conn.row_factory = custom_row_factory
        cursor = conn.cursor()
        
        workflows = []
        for wf in cursor.execute('SELECT * FROM workflows').fetchall():
            wf_dict = dict(wf)
            rules = cursor.execute('SELECT * FROM rules WHERE workflow_id = ?', (wf['id'],)).fetchall()
            wf_dict['rules'] = [dict(r) for r in rules]
            workflows.append(wf_dict)
            
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
                max_age = float(max_val) if max_val and str(max_val).strip() != "" else 5256000
            except (ValueError, TypeError):
                min_age = 0
                max_age = 5256000
            
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
            
            # Normalize timeframe to match message output format (e.g. 1hr/1h -> 1h, 6hr/6h -> 6h, 24hr/24h -> 24h)
            timeframe_clean = timeframe.lower().strip()
            if timeframe_clean in ['1hr', '1h']:
                timeframe_clean = '1h'
            elif timeframe_clean in ['6hr', '6h']:
                timeframe_clean = '6h'
            elif timeframe_clean in ['24hr', '24h']:
                timeframe_clean = '24h'
                
            min_str = rule.get('time_min')
            max_str = rule.get('time_max')
            try:
                min_perf = float(min_str) if min_str and str(min_str).strip() != "" else float('-inf')
                max_perf = float(max_str) if max_str and str(max_str).strip() != "" else float('inf')
                
                # Look for e.g., '5m: +67%' or '1h: -27%'
                pattern = rf'{timeframe_clean}:\s*([+\-]?\d+(?:\.\d+)?)%'
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    actual_performance = float(match.group(1))
                    if not (min_perf <= actual_performance <= max_perf):
                        return None, True, f"Dropped by Performance Filter ({timeframe_clean}: {actual_performance}% not between {min_perf}% and {max_perf}%)"
                    else:
                        print(f"      [PERF] PASSED: {timeframe_clean} = {actual_performance}%")
                else:
                    print(f"      [PERF] PASSED: No '{timeframe_clean}:' pattern found in text (filter skipped)")
            except ValueError:
                pass

        # DEX PAYMENT FILTER LOGIC
        elif rule_type == 'dex_payment':
            try:
                min_p_str = rule.get('time_min')
                max_p_str = rule.get('time_max')
                min_p = float(min_p_str) if min_p_str and str(min_p_str).strip() != "" else 0.0
                max_p = float(max_p_str) if max_p_str and str(max_p_str).strip() != "" else float('inf')
                
                # Extract payment from string: "DEX Payment: $12.34 USD"
                match = re.search(r'DEX Payment:\s*\$?([\d\.]+)\s*USD', text, re.IGNORECASE)
                if match:
                    val = float(match.group(1))
                    if not (min_p <= val <= max_p):
                        print(f"      [DEX PAYMENT] DROPPING: {min_p} <= {val} <= {max_p} is FALSE", flush=True)
                        return None, True, f"Dropped by DEX Payment Filter (Allowed: ${min_p}-${max_p} USD, Found: ${val} USD)"
                    else:
                        print(f"      [DEX PAYMENT] PASSED: {min_p} <= {val} <= {max_p}", flush=True)
                        # Override Order Type display name if a custom label is specified
                        label = (rule.get('search_text') or '').strip()
                        if label:
                            processed_text = re.sub(r'(Order:\s*📦\s*)(.*)', rf'\1{label}', processed_text)
                else:
                    if min_p > 0:
                        return None, True, "Dropped by DEX Payment Filter (No payment amount found in text)"
            except Exception as e:
                print(f"      [DEX PAYMENT] Error evaluating filter: {e}", flush=True)

        # MIGRATION FILTER LOGIC
        elif rule_type == 'migrated':
            try:
                expected = (rule.get('search_text') or 'yes').strip().lower()
                match = re.search(r'Migrated:\s*(Yes|No)', text, re.IGNORECASE)
                if match:
                    actual = match.group(1).lower()
                    if actual != expected:
                        print(f"      [MIGRATION] DROPPING: expected {expected}, found {actual}", flush=True)
                        return None, True, f"Dropped by Migration Filter (Expected: {expected}, Found: {actual})"
                    else:
                        print(f"      [MIGRATION] PASSED: {actual} == {expected}", flush=True)
                else:
                    if expected == 'yes':
                        return None, True, "Dropped by Migration Filter (No migration info found in text)"
            except Exception as e:
                print(f"      [MIGRATION] Error evaluating filter: {e}", flush=True)

        # MARKET CAP FILTER LOGIC
        elif rule_type == 'market_cap':
            try:
                min_mc_str = rule.get('time_min')
                max_mc_str = rule.get('time_max')
                min_mc = float(min_mc_str) if min_mc_str and str(min_mc_str).strip() != "" else 0
                max_mc = float(max_mc_str) if max_mc_str and str(max_mc_str).strip() != "" else float('inf')
                
                # Extract MC from string: "💰 Market Cap: $1.20M" or "$500.5K" or "$500"
                mc_match = re.search(r'Market Cap:\s*\$?([\d\.]+)([KM]?)', text, re.IGNORECASE)
                if mc_match:
                    val = float(mc_match.group(1))
                    suffix = mc_match.group(2).upper()
                    if suffix == 'K':
                        val *= 1_000
                    elif suffix == 'M':
                        val *= 1_000_000
                    
                    if not (min_mc <= val <= max_mc):
                        return None, True, f"Dropped by Market Cap Filter (Allowed: ${min_mc}-${max_mc}, Found: ${val})"
            except Exception as e:
                pass

        # EXCLUDE PLATFORM LOGIC
        elif rule_type == 'exclude_platform':
            platform_to_exclude = (rule.get('search_text') or '').strip().lower()
            if platform_to_exclude:
                # Look for "Status: ✅ Migrated (on pump.fun)" or "Platform: Solana"
                if platform_to_exclude in text.lower():
                    return None, True, f"Dropped by Platform Exclusion (Found '{platform_to_exclude}')"

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
    data = None
    try:
        data = request.json
        print(f"DEBUG: create_workflow payload: {data}")
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO workflows (name, source_channel, source_channel_id, target_channel, target_channel_id, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (data.get('name'), data.get('source_channel'), data.get('source_channel_id'), data.get('target_channel'), data.get('target_channel_id'), 1))
            # Commit the workflow immediately to assign a permanent ID and bypass any driver transaction buffering
            conn.commit()
            
            # Retrieve the newly inserted workflow ID safely (thread-safe under global db_lock)
            cursor.execute('SELECT max(id) FROM workflows')
            row = cursor.fetchone()
            wf_id = row[0] if row else None
            
            if not wf_id:
                raise Exception("Failed to retrieve the new workflow ID from the database.")
            
            for rule in data.get('rules', []):
                cursor.execute('''
                    INSERT INTO rules (workflow_id, rule_type, search_text, replace_text, time_min, time_max)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (wf_id, rule.get('rule_type'), rule.get('search_text'), rule.get('replace_text'), rule.get('time_min'), rule.get('time_max')))
                
            conn.commit()
            
        return jsonify({"success": True, "id": wf_id})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"DEBUG ERROR in create_workflow: {tb}")
        try:
            with open("debug_error.log", "a") as f:
                f.write(f"--- ERROR IN create_workflow ---\nPayload: {data}\nError: {e}\nTraceback:\n{tb}\n\n")
        except:
            pass
        return jsonify({"success": False, "error": str(e), "trace": tb}), 500

@app.route('/api/workflows/<int:id>', methods=['PUT'])
def update_workflow(id):
    try:
        data = request.json
        with db_lock:
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
            
        return jsonify({"success": True})
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/workflows/<int:id>/toggle', methods=['POST'])
def toggle_workflow(id):
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        wf = cursor.execute('SELECT is_active FROM workflows WHERE id=?', (id,)).fetchone()
        if not wf:
            return jsonify({"success": False})
        
        # Try accessing by column name first for dict/CustomRow, fallback to index for tuple safety
        is_active_val = wf.get('is_active') if isinstance(wf, dict) else wf[0]
        new_status = 0 if is_active_val else 1
        
        cursor.execute('UPDATE workflows SET is_active=? WHERE id=?', (new_status, id))
        conn.commit()
    return jsonify({"success": True, "is_active": bool(new_status)})

@app.route('/api/workflows/<int:id>', methods=['DELETE'])
def delete_workflow(id):
    try:
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            # Explicitly delete rules first just in case
            cursor.execute('DELETE FROM rules WHERE workflow_id=?', (id,))
            cursor.execute('DELETE FROM workflows WHERE id=?', (id,))
            conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/settings', methods=['POST'])
def save_settings():
    data = request.json
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        for key, value in data.items():
            cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
        conn.commit()
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

async def process_single_cto_item(item, target_channel, workflow_id, test_mode):
    ca = item.get("tokenAddress")
    order_type = item.get("order_type")
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
        "status": "passed",
        "reason": "Passed and Sent!",
        "formatted_message": "",
        "dex_url": "",
        "signal_type": order_type,
        "order_status": order_status,
        "payment_timestamp": payment_timestamp
    }
    
    # Skip if we already sent this exact signal (same status and timestamp, unless in test mode)
    is_duplicate = False
    if not test_mode:
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            existing = cursor.execute('''
                SELECT id FROM cto_signals 
                WHERE ca = ? AND signal_type = ? AND payment_timestamp = ? AND order_status = ?
            ''', (ca, order_type, payment_timestamp, order_status)).fetchone()
            if existing:
                is_duplicate = True
                
    if is_duplicate:
        token_info["status"] = "duplicate"
        token_info["reason"] = f"Skipped: Already processed ({order_type} - {order_status})"
        return token_info
        
    token_url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    try:
        token_response = await asyncio.to_thread(requests.get, token_url, timeout=10)
        if token_response.status_code != 200:
            token_info["status"] = "error"
            token_info["reason"] = f"Dexscreener token API returned {token_response.status_code}"
            return token_info
    except Exception as e:
        token_info["status"] = "error"
        token_info["reason"] = f"Failed to fetch token details: {e}"
        return token_info
        
    token_data = token_response.json()
    pairs = token_data.get("pairs", [])
    
    if not pairs:
        token_info["status"] = "error"
        token_info["reason"] = "No active trading pairs found on Dexscreener"
        return token_info
        
    primary_pair = pairs[0]
    raw_chain = primary_pair.get("chainId", "unknown").lower()
    if raw_chain != "solana":
        token_info["status"] = "skipped"
        token_info["reason"] = f"Skipped: Chain is {raw_chain} (not Solana)"
        return token_info
        
    type_display_map = {
        "tokenProfile": "Token Profile",
        "communityTakeover": "Community Takeover",
        "tokenAd": "Token Ad",
        "trendingBarAd": "Trending Bar Ad"
    }
    type_display = type_display_map.get(order_type, order_type)
    migration_status_formatted = f"{type_display} ({order_status.upper()})"
    
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
    
    raw_mc = primary_pair.get("marketCap") or primary_pair.get("fdv")
    if isinstance(raw_mc, (int, float)):
        if raw_mc >= 1_000_000:
            mc_str = f"${raw_mc/1_000_000:.2f}M"
        elif raw_mc >= 1_000:
            mc_str = f"${raw_mc/1_000:.2f}K"
        else:
            mc_str = f"${raw_mc:.2f}"
    else:
        mc_str = "Unknown"
    
    pair_created_at = primary_pair.get("pairCreatedAt")
    age_string = "Unknown"
    if pair_created_at:
        current_time_ms = int(time.time() * 1000)
        age_ms = current_time_ms - pair_created_at
        age_minutes = int(age_ms / (1000 * 60))
        if age_minutes < 60:
            age_string = f"{age_minutes}m"
        else:
            age_string = f"{int(age_minutes / 60)}h {age_minutes % 60}m"
            
    price_change = primary_pair.get("priceChange", {})
    perf_5m = float(price_change.get("m5", 0))
    perf_1h = float(price_change.get("h1", 0))
    perf_6h = float(price_change.get("h6", 0))
    perf_24h = float(price_change.get("h24", 0))
    
    # Detect if token has migrated
    is_migrated = False
    for p in pairs:
        dex_id = p.get("dexId", "").lower()
        if dex_id in ["raydium", "meteora", "orca"]:
            is_migrated = True
            break

    # Fetch DEX payment amount in USD
    payment_amount_usd = item.get("amount_usd")
    if payment_amount_usd is None or payment_amount_usd == 0.0:
        settings = get_settings()
        api_key = settings.get('helius_api_key')
        payment_amount_usd = await asyncio.to_thread(get_dex_payment_amount_usd, ca, payment_timestamp, api_key)

    dex_url = f"https://dexscreener.com/{raw_chain}/{ca}"
    
    token_info["name"] = project_name
    token_info["platform"] = chain_id
    token_info["migration_status"] = migration_status_formatted
    token_info["market_cap"] = mc_str
    token_info["age"] = age_string
    token_info["perf_5m"] = perf_5m
    token_info["perf_1h"] = perf_1h
    token_info["perf_6h"] = perf_6h
    token_info["perf_24h"] = perf_24h
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

    msg = (f"🚀 PROJECT: {str(project_name).upper()} 🚀\n"
           f"━━━━━━━━━━━\n"
           f"💰 Market Cap: {mc_str}\n"
           f"🌐 Platform: {chain_id}\n"
           f"🧬 CA: {ca}\n"
           f"━━━━━━━━━━━\n\n"
           f"⏱️ TOKEN TIMINGS\n"
           f"┃ Age (Since Migration): {age_string}\n"
           f"┃ Order: 📦 {type_display}\n"
           f"┃ DEX Payment: ${payment_amount_usd:.2f} USD\n"
           f"┃ Migrated: {'Yes' if is_migrated else 'No'}\n"
           f"┃ Status: {status_emoji} {order_status.upper()}\n"
           f"┃ Order Placed: {payment_time_str}\n\n"
           f"📊 PRICE PERFORMANCE\n"
           f"┃ 🟩 5m: {perf_5m:+.2f}%\n"
           f"┃ ⚡ 1h: {perf_1h:+.2f}%\n"
           f"┃ 📉 6h: {perf_6h:+.2f}%\n"
           f"┃ 🟥 24h: {perf_24h:+.2f}%\n"
           f"━━━━━━━━━━━\n"
           f"📈 Chart: {dex_url}")
    
    passed_any = False
    token_dropped_reasons = []
    
    workflows_to_evaluate = []
    if workflow_id and str(workflow_id).strip() != "" and str(workflow_id) != "active_all":
        workflows = get_workflows()
        wf = next((w for w in workflows if str(w.get('id')) == str(workflow_id)), None)
        if wf:
            workflows_to_evaluate.append(wf)
    else:
        workflows_to_evaluate = [w for w in get_workflows() if w.get('is_active')]
        
    if workflows_to_evaluate:
        passed_channels = []
        for wf in workflows_to_evaluate:
            modified_text, dropped, reason = process_message_logic(msg, wf.get('rules', []))
            if dropped:
                token_dropped_reasons.append(f"[{wf.get('name') or 'Flow'}]: {reason}")
                continue
            
            passed_any = True
            send_msg = modified_text if modified_text else msg
            
            t_id = str(wf.get('target_channel_id') or '').strip()
            t_username = wf.get('target_channel') or ''
            wf_target = t_id if t_id else t_username
            final_target = wf_target
            
            if final_target:
                passed_channels.append(final_target)
                if tg_client:
                    target_entity = int(final_target) if str(final_target).lstrip('-').isdigit() else final_target
                    try:
                        await tg_client.send_message(target_entity, send_msg)
                        print(f"[REAL-TIME SCANNER] Token {ca} successfully forwarded to target: {final_target} via workflow {wf.get('name')}", flush=True)
                    except Exception as e:
                        print(f"[REAL-TIME SCANNER] Failed to send to {final_target}: {e}", flush=True)
                else:
                    print(f"[REAL-TIME SCANNER] WARNING: Token {ca} passed filters, but Telegram client is not running/authorized! Cannot send to {final_target}.", flush=True)
            else:
                print(f"[REAL-TIME SCANNER] Workflow '{wf.get('name')}' matched but has no target channel configured. Skipping.", flush=True)
        
        if passed_any:
            if passed_channels:
                token_info["status"] = "passed"
                token_info["reason"] = f"Sent via workflows to: {', '.join(passed_channels)}"
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
    
    if not test_mode:
        db_status = "forwarded" if token_info.get("status") == "passed" else "skipped"
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO cto_signals (
                    ca, name, platform, market_cap, age, 
                    perf_5m, perf_1h, perf_6h, perf_24h, 
                    status, dex_url, migration_status, signal_type,
                    payment_timestamp, order_status, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ca, project_name, chain_id, mc_str, age_string, 
                perf_5m, perf_1h, perf_6h, perf_24h, 
                db_status, dex_url, migration_status_formatted, order_type,
                payment_timestamp, order_status, token_info.get("reason")
            ))
            conn.commit()
            
    return token_info

def run_async_coroutine(coro):
    global telethon_loop
    if telethon_loop and telethon_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, telethon_loop)
    else:
        # Fallback for testing or when loop is not running yet
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

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
                print(f"[HELIUS DISCOVERY] {msg}")
                return None, msg
            
            txs = r.json()
            if not txs or not isinstance(txs, list):
                msg = f"No transactions returned on page {page+1}."
                print(f"[HELIUS DISCOVERY] {msg}")
                log_messages.append(msg)
                break
                
            # Scan transactions on current page
            for tx in txs:
                tx_ts = tx.get("timestamp")
                if not tx_ts:
                    continue
                
                # Check if within 90s window of payment
                if abs(tx_ts - ts_seconds) <= 90:
                    print(f"[HELIUS DISCOVERY] Found matching transaction {tx.get('signature')} at {tx_ts} (target: {ts_seconds})")
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
                print(f"[HELIUS DISCOVERY] {msg}")
                log_messages.append(msg)
                break
                
            last_sig = txs[-1].get("signature")
            if not last_sig:
                break
        except Exception as e:
            msg = f"Error querying page {page+1}: {e}"
            print(f"[HELIUS DISCOVERY] {msg}")
            return None, msg
            
    summary = "; ".join(log_messages) if log_messages else "No matching payment transaction found in analyzed history pages."
    return None, summary

def get_dex_payment_amount_usd(token_address, payment_timestamp, api_key):
    if not api_key or not token_address or not payment_timestamp:
        return 0.0
    
    # Convert payment_timestamp (in ms) to seconds
    ts_seconds = int(payment_timestamp / 1000)
    
    settings = get_settings()
    monitored_addresses = []
    
    # Get monitored addresses (cto_dex_payment_address and tracked_wallet_address)
    cto_addr = settings.get('cto_dex_payment_address')
    if cto_addr and len(cto_addr.strip()) >= 32:
        monitored_addresses.append(cto_addr.strip())
        
    tracked_addr_str = settings.get('tracked_wallet_address')
    if tracked_addr_str:
        for a in tracked_addr_str.split(','):
            a_clean = a.strip()
            if len(a_clean) >= 32 and a_clean not in monitored_addresses:
                monitored_addresses.append(a_clean)
                
    # We will try scanning the transaction history of the monitored addresses first,
    # as they are the most likely recipients of the payment transaction.
    # If none are configured, fallback to scanning the token address.
    addresses_to_scan = monitored_addresses if monitored_addresses else [token_address]
    
    try:
        sol_price = get_token_price_usd("SOL")
        
        for scan_addr in addresses_to_scan:
            url = f"https://api.helius.xyz/v0/addresses/{scan_addr}/transactions?api-key={api_key}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
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
                            
                            # Check token transfers to the scan_addr
                            for transfer in tx.get("tokenTransfers", []):
                                if transfer.get("toUserAccount") == scan_addr:
                                    mint = transfer.get("mint", "")
                                    amount = transfer.get("tokenAmount", 0)
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
                                    
                            if total_usd > 0:
                                print(f"[DEX PAYMENT FILTER] Found payment of ${total_usd:.2f} USD to {scan_addr}", flush=True)
                                return total_usd
                                
        # Final fallback: scan the token address itself if the monitored addresses scan didn't find it
        if addresses_to_scan != [token_address]:
            url = f"https://api.helius.xyz/v0/addresses/{token_address}/transactions?api-key={api_key}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                txs = r.json()
                if txs and isinstance(txs, list):
                    for tx in txs:
                        tx_ts = tx.get("timestamp")
                        if tx_ts and abs(tx_ts - ts_seconds) <= 120:
                            total_usd = 0.0
                            
                            for transfer in tx.get("tokenTransfers", []):
                                mint = transfer.get("mint", "")
                                amount = transfer.get("tokenAmount", 0)
                                if mint in ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaypmko8L7jV8YW156HCfaNuDY8jNH5KB3C1d'):
                                    total_usd += amount
                                else:
                                    t_price = get_token_price_usd(mint)
                                    total_usd += amount * t_price if t_price else 0.0
                                    
                            for transfer in tx.get("nativeTransfers", []):
                                amount_sol = transfer.get("amount", 0) / 1e9
                                total_usd += amount_sol * sol_price
                                
                            if total_usd > 0:
                                print(f"[DEX PAYMENT FILTER] Fallback token scan found payment of ${total_usd:.2f} USD", flush=True)
                                return total_usd
                                
    except Exception as e:
        print(f"[DEX PAYMENT FILTER] Error fetching payment amount: {e}", flush=True)
        
    return 0.0

async def find_token_from_payer_history(payer_address, payment_timestamp, api_key, depth=0):
    if not payer_address or not api_key:
        return None
    if depth > 2:
        return None
    
    url = f"https://api.helius.xyz/v0/addresses/{payer_address}/transactions?api-key={api_key}"
    try:
        print(f"[PAYER HISTORY] Scanning history for payer {payer_address} around payment time {payment_timestamp} (depth={depth})...", flush=True)
        r = await asyncio.to_thread(requests.get, url, timeout=10)
        if r.status_code != 200:
            print(f"[PAYER HISTORY] Helius API HTTP {r.status_code} error: {r.text.strip()}", flush=True)
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
        
        # 1. Primary pass: Look for positive balance changes or token transfer inflows (Buy/Swap)
        for tx in txs_sorted:
            tx_ts = tx.get("timestamp", 0)
            if tx_ts > payment_timestamp + 30:
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
                print(f"[PAYER HISTORY] Found token {mint} from positive balance/transfer in tx {tx.get('signature')} at {tx_ts} (payment ts: {payment_timestamp})", flush=True)
                return mint
                
        # 2. Secondary fallback: Look for any custom token transaction activity
        for tx in txs_sorted:
            tx_ts = tx.get("timestamp", 0)
            if tx_ts > payment_timestamp + 30:
                continue
                
            for transfer in tx.get("tokenTransfers", []):
                mint = transfer.get("mint")
                if mint and mint not in excluded_mints:
                    print(f"[PAYER HISTORY] Fallback found token {mint} in transfers in tx {tx.get('signature')} at {tx_ts} (payment ts: {payment_timestamp})", flush=True)
                    return mint
                    
            for change in tx.get("tokenBalanceChanges", []):
                mint = change.get("mint")
                if mint and mint not in excluded_mints:
                    print(f"[PAYER HISTORY] Fallback found token {mint} in balance changes in tx {tx.get('signature')} at {tx_ts} (payment ts: {payment_timestamp})", flush=True)
                    return mint

        # 3. Funder trace: if no token found, check if this wallet was funded by another address
        if depth < 2:
            funders = []
            for tx in txs_sorted:
                tx_ts = tx.get("timestamp", 0)
                if tx_ts > payment_timestamp + 30:
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
                print(f"[PAYER HISTORY] Wallet {payer_address} had no custom token history. Tracing {len(funders)} funders...", flush=True)
                for funder_addr, funding_ts in funders:
                    token = await find_token_from_payer_history(funder_addr, funding_ts, api_key, depth=depth + 1)
                    if token:
                        print(f"[PAYER HISTORY] Successfully identified token {token} from funding address {funder_addr}", flush=True)
                        return token

        print(f"[PAYER HISTORY] Could not find any custom token activity for payer {payer_address} near payment timestamp {payment_timestamp}.", flush=True)
    except Exception as e:
        print(f"[PAYER HISTORY] Error looking up history for {payer_address}: {e}", flush=True)
        
    return None

async def process_payment_payer_history(payer_address, payment_timestamp, api_key, amount_usd=0.0):
    token = await find_token_from_payer_history(payer_address, payment_timestamp, api_key)
    if token:
        print(f"[HELIUS DISCOVERY] Successfully isolated token from payer history: {token}. Proceeding to verify.", flush=True)
        await verify_and_forward_cto_token(token, amount_usd)
    else:
        print(f"[HELIUS DISCOVERY] No token isolated from payer history for {payer_address}.", flush=True)

async def wallet_tracker_polling_loop():
    processed_signatures = set()
    initialized_addresses = set()
    print("[WALLET TRACKER POLLING] Started background payment monitoring loop.", flush=True)
    
    # Helper to trace and process payment sender history in background
    async def process_payment_and_forward(payer_address, tx_timestamp, amount_usd=0.0):
        try:
            print(f"[WALLET TRACKER] Tracing sender history for {payer_address}...", flush=True)
            loop_settings = get_settings()
            loop_api_key = loop_settings.get('helius_api_key')
            if not loop_api_key:
                print("[WALLET TRACKER] Helius API key not found in settings. Skipping history trace.", flush=True)
                return
                
            token = await find_token_from_payer_history(payer_address, tx_timestamp, loop_api_key)
            if token:
                print(f"[WALLET TRACKER] Discovered token {token} from sender {payer_address}. Forwarding to filters...", flush=True)
                target = loop_settings.get('cto_target_channel', '')
                wf_id = loop_settings.get('cto_workflow_id')
                t_mode = loop_settings.get('cto_test_mode', 'false').lower() == 'true'
                
                item = {
                    "tokenAddress": token,
                    "order_type": "Tracked Wallet Payment",
                    "order_status": "detected",
                    "payment_timestamp": tx_timestamp * 1000,
                    "amount_usd": amount_usd
                }
                await process_single_cto_item(item, target, wf_id, t_mode)
            else:
                print(f"[WALLET TRACKER] No token discovered for sender {payer_address}.", flush=True)
        except Exception as ex:
            print(f"[WALLET TRACKER] Error processing payment sender history: {ex}", flush=True)
            
    while True:
        try:
            settings = get_settings()
            api_key = settings.get('helius_api_key')
            tracked_address_str = settings.get('tracked_wallet_address')
            
            if api_key and tracked_address_str:
                addresses = [a.strip() for a in tracked_address_str.split(',') if len(a.strip()) >= 32]
                
                for tracked_address in addresses:
                    url = f"https://api.helius.xyz/v0/addresses/{tracked_address}/transactions?api-key={api_key}"
                    r = await asyncio.to_thread(requests.get, url, timeout=10)
                    if r.status_code == 200:
                        txs = r.json()
                        if isinstance(txs, list):
                            # Initialize set with first batch on startup to avoid processing old history for this address
                            if tracked_address not in initialized_addresses:
                                for t in txs:
                                    if t.get("signature"):
                                        processed_signatures.add(t.get("signature"))
                                initialized_addresses.add(tracked_address)
                                continue
                            
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
                                        with db_lock:
                                            conn = get_db()
                                            cursor = conn.cursor()
                                            try:
                                                cursor.execute('''
                                                    INSERT INTO wallet_payments (tracked_address, sender_address, amount, mint, signature, timestamp)
                                                    VALUES (?, ?, ?, ?, ?, ?)
                                                ''', (tracked_address, from_addr, amount_sol, "SOL", sig, timestamp))
                                                conn.commit()
                                                print(f"[WALLET TRACKER] Logged real-time native payment: {amount_sol} SOL from {from_addr} to {tracked_address}", flush=True)
                                                # Trigger background history tracing and filtering
                                                asyncio.create_task(process_payment_and_forward(from_addr, timestamp, amount_usd))
                                            except sqlite3.IntegrityError:
                                                pass
                                                
                                # Process token transfers
                                for transfer in tx.get('tokenTransfers', []):
                                    to_addr = transfer.get('toUserAccount')
                                    from_addr = transfer.get('fromUserAccount')
                                    token_amount = transfer.get('tokenAmount', 0)
                                    mint = transfer.get('mint', '')
                                    if to_addr == tracked_address and from_addr != tracked_address:
                                        token_price = get_token_price_usd(mint)
                                        amount_usd = token_amount * token_price if token_price else 0.0
                                        with db_lock:
                                            conn = get_db()
                                            cursor = conn.cursor()
                                            try:
                                                cursor.execute('''
                                                    INSERT INTO wallet_payments (tracked_address, sender_address, amount, mint, signature, timestamp)
                                                    VALUES (?, ?, ?, ?, ?, ?)
                                                ''', (tracked_address, from_addr, token_amount, mint, sig, timestamp))
                                                conn.commit()
                                                print(f"[WALLET TRACKER] Logged real-time token payment: {token_amount} {mint[:8]}... from {from_addr} to {tracked_address}", flush=True)
                                                # Trigger background history tracing and filtering
                                                asyncio.create_task(process_payment_and_forward(from_addr, timestamp, amount_usd))
                                            except sqlite3.IntegrityError:
                                                pass
        except Exception as e:
            print(f"[WALLET TRACKER POLLING] Loop error: {e}", flush=True)
            
        await asyncio.sleep(10)

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
                    break
                
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
                break
            
    sorted_senders = list(senders_map.values())
    sorted_senders.sort(key=lambda x: x["latest_timestamp"], reverse=True)
    
    # Decorate senders with last bought token before returning
    if api_key and sorted_senders:
        async def decorate_senders_with_tokens(senders):
            global tg_client
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
                            age_string = "Unknown"
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
                                    primary_pair = pairs[0]
                                    raw_chain = primary_pair.get("chainId", "unknown").lower()
                                    project_name = primary_pair.get("baseToken", {}).get("name", "Unknown")
                                    
                                    # mc
                                    raw_mc = primary_pair.get("marketCap") or primary_pair.get("fdv")
                                    if isinstance(raw_mc, (int, float)):
                                        if raw_mc >= 1_000_000:
                                            mc_str = f"${raw_mc/1_000_000:.2f}M"
                                        elif raw_mc >= 1_000:
                                            mc_str = f"${raw_mc/1_000:.2f}K"
                                        else:
                                            mc_str = f"${raw_mc:.2f}"
                                            
                                    # age
                                    pair_created_at = primary_pair.get("pairCreatedAt")
                                    if pair_created_at:
                                        current_time_ms = int(time.time() * 1000)
                                        age_ms = current_time_ms - pair_created_at
                                        age_minutes = int(age_ms / (1000 * 60))
                                        if age_minutes < 60:
                                            age_string = f"{age_minutes}m"
                                        else:
                                            age_string = f"{int(age_minutes / 60)}h {age_minutes % 60}m"
                                            
                                    # perf
                                    price_change = primary_pair.get("priceChange", {})
                                    perf_5m = float(price_change.get("m5", 0))
                                    perf_1h = float(price_change.get("h1", 0))
                                    perf_6h = float(price_change.get("h6", 0))
                                    perf_24h = float(price_change.get("h24", 0))
                                    
                                    # migration
                                    for p in pairs:
                                        dex_id = p.get("dexId", "").lower()
                                        if dex_id in ["raydium", "meteora", "orca"]:
                                            is_migrated = True
                                            break
                            
                            payment_amount_usd = s["total_sent_usd"]
                            
                            payment_time_str = "Unknown"
                            if s.get("latest_timestamp"):
                                try:
                                    dt = datetime.fromtimestamp(s["latest_timestamp"], tz=timezone.utc)
                                    payment_time_str = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                                except Exception:
                                    pass
                            
                            msg = (f"🚀 PROJECT: {str(project_name).upper()} 🚀\n"
                                   f"━━━━━━━━━━━\n"
                                   f"💰 Market Cap: {mc_str}\n"
                                   f"🌐 Platform: {chain_id}\n"
                                   f"🧬 CA: {token}\n"
                                   f"━━━━━━━━━━━\n\n"
                                   f"⏱️ TOKEN TIMINGS\n"
                                   f"┃ Age (Since Migration): {age_string}\n"
                                   f"┃ Order: 📦 {type_display}\n"
                                   f"┃ DEX Payment: ${payment_amount_usd:.2f} USD\n"
                                   f"┃ Migrated: {'Yes' if is_migrated else 'No'}\n"
                                   f"┃ Status: ✅ APPROVED\n"
                                   f"┃ Order Placed: {payment_time_str}\n\n"
                                   f"📊 PRICE PERFORMANCE\n"
                                   f"┃ 🟩 5m: {perf_5m:+.2f}%\n"
                                   f"┃ ⚡ 1h: {perf_1h:+.2f}%\n"
                                   f"┃ 📉 6h: {perf_6h:+.2f}%\n"
                                   f"┃ 🟥 24h: {perf_24h:+.2f}%\n"
                                   f"━━━━━━━━━━━\n"
                                   f"📈 Chart: https://dexscreener.com/solana/{token}")
                            
                            _, dropped, reason = process_message_logic(msg, workflow_rules)
                            if dropped:
                                s["test_status"] = "dropped"
                                s["test_reason"] = reason
                            else:
                                s["test_status"] = "passed"
                                s["test_reason"] = "Passed"
                                
                                # Actually send to Telegram target group
                                if tg_client:
                                    t_id = str(wf_target_channel_id or '').strip()
                                    t_username = wf_target_channel or ''
                                    final_target = t_id if t_id else t_username
                                    if final_target:
                                        target_entity = int(final_target) if str(final_target).lstrip('-').isdigit() else final_target
                                        try:
                                            send_text = _ if _ else msg
                                            await tg_client.send_message(target_entity, send_text)
                                            print(f"[TEST RUNNER] Successfully forwarded passing test token {token} to {final_target}", flush=True)
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

        global telethon_loop
        if telethon_loop and telethon_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(decorate_senders_with_tokens(sorted_senders), telethon_loop)
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
                SELECT id, tracked_address, sender_address, amount, mint, signature, timestamp, created_at
                FROM wallet_payments
                ORDER BY id DESC LIMIT 50
            ''').fetchall()
            # Convert rows to plain dicts to use outside the lock
            rows_list = [dict(r) if isinstance(r, dict) else {
                "id": r[0],
                "tracked_address": r[1],
                "sender_address": r[2],
                "amount": r[3],
                "mint": r[4],
                "signature": r[5],
                "timestamp": r[6],
                "created_at": r[7]
            } for r in rows]
            
        results = []
        for r in rows_list:
            amount = r['amount']
            mint = r['mint']
            price = get_token_price_usd(mint)
            amount_usd = amount * price if price else 0.0
            
            results.append({
                "id": r['id'],
                "tracked_address": r['tracked_address'],
                "sender_address": r['sender_address'],
                "amount": amount,
                "mint": mint,
                "amount_usd": amount_usd,
                "token_name": "SOL" if mint == "SOL" else get_token_metadata(mint),
                "signature": r['signature'],
                "timestamp": r['timestamp'],
                "created_at": r['created_at']
            })
        return jsonify({"success": True, "payments": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/cto/discover_payment_address', methods=['POST'])
def discover_payment_address_route():
    try:
        settings = get_settings()
        api_key = settings.get('helius_api_key')
        if not api_key:
            return jsonify({"success": False, "error": "Helius API Key not configured."})
            
        # 1. Fetch active Solana tokens from DexScreener profiles & boosts to find fresh CTO orders
        fresh_candidates = []
        try:
            print("[HELIUS DISCOVERY] Querying DexScreener profiles and boosts feeds for live tokens...")
            p_res = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=5)
            p_data = p_res.json() if p_res.status_code == 200 else []
            b_res = requests.get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=5)
            b_data = b_res.json() if b_res.status_code == 200 else []
            
            seen_cas = set()
            feed_tokens = []
            for item in p_data + b_data:
                ca = item.get("tokenAddress")
                chain = item.get("chainId")
                if ca and chain and chain.lower() == "solana" and ca not in seen_cas:
                    seen_cas.add(ca)
                    feed_tokens.append(ca)
            
            # Check orders for up to 15 latest tokens from the feed to find a recent payment
            for ca in feed_tokens[:15]:
                try:
                    orders_res = requests.get(f"https://api.dexscreener.com/orders/v1/solana/{ca}", timeout=5)
                    if orders_res.status_code == 200:
                        orders_data = orders_res.json()
                        for o in orders_data.get("orders", []):
                            if o.get("type") == "communityTakeover" and o.get("paymentTimestamp"):
                                fresh_candidates.append({
                                    "ca": ca,
                                    "payment_timestamp": int(o.get("paymentTimestamp"))
                                })
                except Exception as ex:
                    print(f"[HELIUS DISCOVERY] Error checking feed token orders for {ca}: {ex}")
        except Exception as ex:
            print(f"[HELIUS DISCOVERY] Error fetching DexScreener profiles/boosts: {ex}")
            
        # Sort fresh candidates by paymentTimestamp descending (newest first)
        fresh_candidates.sort(key=lambda x: x["payment_timestamp"], reverse=True)
        print(f"[HELIUS DISCOVERY] Discovered {len(fresh_candidates)} candidate CTO orders in live feed.")

        # 2. Get fallback tokens from database
        db_candidates = []
        try:
            with db_lock:
                conn = get_db()
                cursor = conn.cursor()
                rows = cursor.execute('''
                    SELECT ca, payment_timestamp FROM cto_signals
                    WHERE payment_timestamp IS NOT NULL AND payment_timestamp > 0
                    ORDER BY id DESC LIMIT 10
                ''').fetchall()
            for r in rows:
                db_candidates.append({
                    "ca": r['ca'] if isinstance(r, dict) else r[0],
                    "payment_timestamp": r['payment_timestamp'] if isinstance(r, dict) else r[1]
                })
        except Exception as ex:
            print(f"[HELIUS DISCOVERY] Error fetching database candidates: {ex}")
            
        # 3. Combine list, prioritize live feed candidates (skipping duplicates)
        candidates_to_try = []
        seen_ca_try = set()
        for c in fresh_candidates:
            if c["ca"] not in seen_ca_try:
                seen_ca_try.add(c["ca"])
                candidates_to_try.append(c)
                
        for c in db_candidates:
            if c["ca"] not in seen_ca_try:
                seen_ca_try.add(c["ca"])
                candidates_to_try.append(c)
                
        if not candidates_to_try:
            return jsonify({"success": False, "error": "No candidate CTO orders found in live feed or database."})
            
        # Try finding the address from the candidates
        attempts = []
        for c in candidates_to_try:
            ca = c["ca"]
            payment_ts = c["payment_timestamp"]
            
            print(f"[HELIUS DISCOVERY] Attempting to discover DexScreener payment address using token {ca} at timestamp {payment_ts}...")
            discovered_addr, reason = discover_payment_wallet_logic(ca, payment_ts, api_key)
            if discovered_addr:
                print(f"[HELIUS DISCOVERY] SUCCESS! Discovered payment address: {discovered_addr}")
                return jsonify({"success": True, "address": discovered_addr})
            else:
                attempts.append(f"Token {ca[:8]}: {reason}")
                
        error_msg = f"Could not identify payment address from candidates. Attempts logs: " + " | ".join(attempts)
        print(f"[HELIUS DISCOVERY] FAILURE: {error_msg}")
        return jsonify({"success": False, "error": error_msg})
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})

async def verify_and_forward_cto_token(ca, amount_usd=0.0):
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
                        "amount_usd": amount_usd
                    }
                    print(f"[REAL-TIME SCANNER] Verified matching order ({o_type}) for {ca} on DexScreener API.", flush=True)
                    await process_single_cto_item(item, target_channel, workflow_id, test_mode)
    except Exception as e:
        print(f"[REAL-TIME SCANNER] Error in verify_and_forward_cto_token for {ca}: {e}", flush=True)

@app.route('/api/helius/webhook', methods=['POST'])
def helius_webhook():
    try:
        transactions = request.json
        if not isinstance(transactions, list):
            return jsonify({"status": "ignored"}), 400
            
        settings = get_settings()
        monitored_address = settings.get('cto_dex_payment_address')
        if not monitored_address:
            return jsonify({"status": "ignored", "reason": "cto_dex_payment_address is not configured"}), 200
            
        for tx in transactions:
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
                
                print(f"[HELIUS WEBHOOK] DexScreener payment transaction detected: {tx.get('signature')} | Discovered tokens: {base_tokens}", flush=True)
                
                if base_tokens:
                    # Instantly check each base token in background Telethon loop
                    for ca in base_tokens:
                        run_async_coroutine(verify_and_forward_cto_token(ca))
                elif payer_address:
                    api_key = settings.get('helius_api_key')
                    tx_ts = tx.get("timestamp", int(time.time()))
                    print(f"[HELIUS WEBHOOK] No base tokens in payment tx. Triggering payer history search for {payer_address}...", flush=True)
                    run_async_coroutine(process_payment_payer_history(payer_address, tx_ts, api_key))
                    
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"[HELIUS WEBHOOK] Error handling webhook: {e}", flush=True)
        return jsonify({"status": "error", "error": str(e)}), 500

async def helius_polling_loop():
    processed_signatures = set()
    print("[HELIUS POLLING] Started fallback transaction scanning loop.", flush=True)
    
    while True:
        try:
            settings = get_settings()
            api_key = settings.get('helius_api_key')
            payment_address = settings.get('cto_dex_payment_address')
            is_auto = str(settings.get('cto_auto_scan', 'false')).lower() == 'true'
            
            if api_key and payment_address and is_auto:
                url = f"https://api.helius.xyz/v0/addresses/{payment_address}/transactions?api-key={api_key}"
                r = await asyncio.to_thread(requests.get, url, timeout=10)
                if r.status_code == 200:
                    txs = r.json()
                    if isinstance(txs, list):
                        new_tokens_to_check = set()
                        for tx in txs:
                            sig = tx.get("signature")
                            if not sig:
                                continue
                            
                            # Initialize set with first batch on startup to avoid processing old history
                            if not processed_signatures:
                                for t in txs:
                                    if t.get("signature"):
                                        processed_signatures.add(t.get("signature"))
                                break
                                
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
                                print(f"[HELIUS POLLING] No base tokens in payment tx. Triggering payer history search for {payer_address}...", flush=True)
                                asyncio.create_task(process_payment_payer_history(payer_address, tx_ts, api_key, amount_usd))
                                    
                        for token in new_tokens_to_check:
                            print(f"[HELIUS POLLING] Instantly verifying token {token}...", flush=True)
                            await verify_and_forward_cto_token(token, amount_usd)
        except Exception as e:
            print(f"[HELIUS POLLING] Loop error: {e}", flush=True)
            
        await asyncio.sleep(10)

async def perform_cto_scan(target_channel, workflow_id=None, test_mode=False):
    try:
        settings = get_settings()
        fetch_approved = settings.get('cto_fetch_approved', 'true').lower() == 'true'
        fetch_pending = settings.get('cto_fetch_pending', 'true').lower() == 'true'
        
        # Fallback for old databases
        if 'cto_fetch_approved' not in settings and 'cto_fetch_pending' not in settings:
            scan_mode = settings.get('cto_scan_mode', 'both')
            fetch_approved = scan_mode in ('approved', 'both')
            fetch_pending = scan_mode in ('pending', 'both')
        
        tokens_to_process = []
        
        # Fetch candidate tokens from profiles & boosts
        try:
            # Fetch token profiles
            profiles_res = await asyncio.to_thread(requests.get, "https://api.dexscreener.com/token-profiles/latest/v1")
            profiles = profiles_res.json() if profiles_res.status_code == 200 else []
            
            # Fetch token boosts
            boosts_res = await asyncio.to_thread(requests.get, "https://api.dexscreener.com/token-boosts/latest/v1")
            boosts = boosts_res.json() if boosts_res.status_code == 200 else []
            
            # Extract candidate tokens (Solana only)
            candidates = {}
            for p in profiles:
                addr = p.get("tokenAddress")
                chain = p.get("chainId")
                if addr and chain and chain.lower() == "solana":
                    candidates[addr] = chain
            for b in boosts:
                addr = b.get("tokenAddress")
                chain = b.get("chainId")
                if addr and chain and chain.lower() == "solana":
                    candidates[addr] = chain
                    
            # Check orders for each candidate token
            now = time.time()
            for addr, chain in candidates.items():
                cache_key = (chain, addr)
                if not test_mode and now - orders_check_cache.get(cache_key, 0) < 120:
                    continue
                
                orders_check_cache[cache_key] = now
                orders_url = f"https://api.dexscreener.com/orders/v1/{chain}/{addr}"
                orders_res = await asyncio.to_thread(requests.get, orders_url)
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
                            tokens_to_process.append({
                                "tokenAddress": addr,
                                "chainId": chain,
                                "order_type": o_type,
                                "order_status": o_status,
                                "payment_timestamp": o_pay_ts
                            })
        except Exception as e:
            print(f"Error fetching pending community takeovers/orders: {e}", flush=True)

        results = []
        
        # Limit processing to avoid timeouts
        seen_in_batch = set()
        for item in tokens_to_process[:20]:
            ca = item.get("tokenAddress")
            order_type = item.get("order_type")
            order_status = item.get("order_status")
            payment_timestamp = item.get("payment_timestamp")
            if not ca:
                continue
                
            batch_key = (ca, order_type, payment_timestamp, order_status)
            if batch_key in seen_in_batch:
                continue
            seen_in_batch.add(batch_key)
            
            token_info = await process_single_cto_item(item, target_channel, workflow_id, test_mode)
            if token_info:
                results.append(token_info)
        
        # Flushed realtime summary for production console logging
        scanned_count = len(results)
        passed_count = sum(1 for r in results if r.get("status") == "passed")
        duplicate_count = sum(1 for r in results if r.get("status") == "duplicate")
        dropped_count = sum(1 for r in results if r.get("status") == "dropped")
        print(f"[CTO SCANNER] Done. Scanned: {scanned_count} | Forwarded: {passed_count} | Duplicates: {duplicate_count} | Dropped: {dropped_count}", flush=True)
        
        db_results = []
        try:
            allowed_statuses = []
            if fetch_approved:
                allowed_statuses.append('approved')
            if fetch_pending:
                allowed_statuses.append('processing')
            
            if allowed_statuses:
                placeholders = ','.join('?' for _ in allowed_statuses)
                query = f'''
                    SELECT ca, name, platform, market_cap, age, 
                           perf_5m, perf_1h, perf_6h, perf_24h, 
                           status, dex_url, migration_status, signal_type,
                           payment_timestamp, order_status, reason
                    FROM cto_signals 
                    WHERE signal_type = 'communityTakeover' AND order_status IN ({placeholders})
                    ORDER BY id DESC LIMIT 50
                '''
                conn = get_db()
                cursor = conn.cursor()
                db_signals = cursor.execute(query, allowed_statuses).fetchall()
            else:
                db_signals = []
            for row in db_signals:
                db_results.append({
                    "ca": row[0],
                    "name": row[1],
                    "platform": row[2],
                    "market_cap": row[3],
                    "age": row[4],
                    "perf_5m": row[5],
                    "perf_1h": row[6],
                    "perf_6h": row[7],
                    "perf_24h": row[8],
                    "status": "passed" if row[9] == "forwarded" else "dropped",
                    "reason": row[15] if row[15] else ("Passed and Sent!" if row[9] == "forwarded" else "Dropped by Filter Rules"),
                    "dex_url": row[10],
                    "migration_status": row[11],
                    "signal_type": row[12],
                    "payment_timestamp": row[13],
                    "order_status": row[14],
                    "formatted_message": ""
                })
        except Exception as e:
            print(f"Error loading recent signals from DB: {e}", flush=True)

        # Merge new scan results and database results
        merged_results = list(results)
        merged_keys = {(r["ca"], r["signal_type"], r.get("payment_timestamp"), r.get("order_status")) for r in results}
        
        for r in db_results:
            key = (r["ca"], r["signal_type"], r.get("payment_timestamp"), r.get("order_status"))
            if key not in merged_keys:
                merged_results.append(r)
                merged_keys.add(key)
                
        return merged_results
        
    except Exception as e:
        import traceback
        print(f"Auto scan error: {traceback.format_exc()}")
        return []

@app.route('/api/cto/scan', methods=['POST'])
def scan_cto():
    data = request.json
    target_channel = data.get('target_channel')
    workflow_id = data.get('workflow_id')
    test_mode = data.get('test_mode', False)
    
    if not tg_client or not telethon_loop:
        return jsonify({"success": False, "error": "Telegram client not ready"})

    try:
        results = asyncio.run_coroutine_threadsafe(perform_cto_scan(target_channel, workflow_id, test_mode), telethon_loop).result(timeout=60)
        return jsonify({"success": True, "results": results})
        
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})

async def cto_auto_scanner_loop():
    while True:
        interval = 60
        try:
            settings = get_settings()
            is_auto = str(settings.get('cto_auto_scan', 'false')).lower() == 'true'
            target = settings.get('cto_target_channel', '')
            workflow_id = settings.get('cto_workflow_id')
            
            try:
                interval = int(settings.get('cto_scan_interval', 60))
                if interval < 2:  # Safety boundary
                    interval = 2
            except (ValueError, TypeError):
                interval = 60
                
            if is_auto and tg_client and await tg_client.is_user_authorized():
                print(f"Running auto CTO scan (Interval: {interval}s)...", flush=True)
                await perform_cto_scan(target, workflow_id)
        except Exception as e:
            print(f"Error in cto_auto_scanner_loop: {e}", flush=True)
            
        await asyncio.sleep(interval)

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
                print("    -> Match by Chat Title/Name!")
            
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
    
    # Start auto-scanner loops
    # asyncio.create_task(cto_auto_scanner_loop())
    asyncio.create_task(helius_polling_loop())
    asyncio.create_task(wallet_tracker_polling_loop())
    
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
                    
                    with db_lock:
                        conn = get_db()
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM settings WHERE key IN ('phone_code_hash')")
                        conn.commit()
                    
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
