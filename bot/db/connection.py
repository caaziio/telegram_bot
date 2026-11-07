"""SQLite connection/schema, Supabase client and settings access."""

import sqlite3
import os
import threading

from bot.config import SUPABASE_KEY, SUPABASE_URL

supabase_client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print(f"[SUPABASE] Initialized Supabase client for {SUPABASE_URL}", flush=True)
    except Exception as e:
        print(f"[SUPABASE] Warning: Failed to initialize Supabase client: {e}", flush=True)

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

db_lock = threading.RLock()
workflow_sync_lock = threading.RLock()
global_conn = None

def _init_db_schema(conn):
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
            liquidity_usd REAL,
            volume_5m REAL,
            volume_1h REAL,
            volume_6h REAL,
            volume_24h REAL,
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dex_validated (
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
            liquidity_usd REAL,
            volume_5m REAL,
            volume_1h REAL,
            volume_6h REAL,
            volume_24h REAL,
            status TEXT,
            dex_url TEXT,
            migration_status TEXT,
            workflow_id INTEGER,
            signal_type TEXT,
            payment_timestamp INTEGER,
            order_status TEXT,
            published_at INTEGER,
            fetched_at INTEGER,
            detection_seconds REAL,
            reason TEXT,
            formatted_message TEXT,
            metrics_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dex_validated_event
        ON dex_validated(ca, signal_type, payment_timestamp, order_status)
    ''')
    try:
        cursor.execute('ALTER TABLE dex_validated ADD COLUMN formatted_message TEXT')
    except Exception:
        pass
    try:
        cursor.execute('ALTER TABLE dex_validated ADD COLUMN metrics_data TEXT')
    except Exception:
        pass

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
            token_name TEXT,
            ca TEXT,
            platform TEXT,
            market_cap TEXT,
            age TEXT,
            perf_5m REAL,
            perf_1h REAL,
            perf_6h REAL,
            perf_24h REAL,
            liquidity_usd REAL,
            volume_5m REAL,
            volume_1h REAL,
            volume_6h REAL,
            volume_24h REAL,
            dex_url TEXT,
            migration_status TEXT,
            status TEXT,
            reason TEXT,
            formatted_message TEXT,
            metrics_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Add new columns to wallet_payments if they don't exist yet
    for col_def in [
        'token_name TEXT', 'ca TEXT', 'platform TEXT', 'market_cap TEXT', 'age TEXT', 'migration_age TEXT',
        'perf_5m REAL', 'perf_1h REAL', 'perf_6h REAL', 'perf_24h REAL',
        'liquidity_usd REAL', 'volume_5m REAL', 'volume_1h REAL', 'volume_6h REAL', 'volume_24h REAL',
        'dex_url TEXT', 'migration_status TEXT', 'status TEXT', 'reason TEXT', 'formatted_message TEXT',
        'metrics_data TEXT',
        'max_mc_1h REAL', 'peak_mult_1h REAL', 'min_mc_1h REAL', 'min_dd_1h_pct REAL',
        'pre_peak_low_mc_1h REAL', 'pre_peak_dd_1h_pct REAL', 'path_1h TEXT',
        'max_mc_4h REAL', 'peak_mult_4h REAL', 'min_mc_4h REAL', 'min_dd_4h_pct REAL',
        'pre_peak_low_mc_4h REAL', 'pre_peak_dd_4h_pct REAL', 'path_4h TEXT',
        'perf_checked_stage INTEGER DEFAULT 0'
    ]:
        try:
            cursor.execute(f'ALTER TABLE wallet_payments ADD COLUMN {col_def}')
        except Exception:
            pass

    for col_def in [
        'max_mc_1h REAL', 'peak_mult_1h REAL', 'min_mc_1h REAL', 'min_dd_1h_pct REAL',
        'pre_peak_low_mc_1h REAL', 'pre_peak_dd_1h_pct REAL', 'path_1h TEXT',
        'max_mc_4h REAL', 'peak_mult_4h REAL', 'min_mc_4h REAL', 'min_dd_4h_pct REAL',
        'pre_peak_low_mc_4h REAL', 'pre_peak_dd_4h_pct REAL', 'path_4h TEXT',
        'perf_checked_stage INTEGER DEFAULT 0'
    ]:
        try:
            cursor.execute(f'ALTER TABLE dex_validated ADD COLUMN {col_def}')
        except Exception:
            pass

    for col_def in [
        'liquidity_usd REAL', 'volume_5m REAL', 'volume_1h REAL',
        'volume_6h REAL', 'volume_24h REAL'
    ]:
        try:
            cursor.execute(f'ALTER TABLE cto_signals ADD COLUMN {col_def}')
        except Exception:
            pass

    # Add ON DELETE CASCADE support
    cursor.execute('PRAGMA foreign_keys = ON;')

    # Migrate invalid cto_workflow_id '1' setting to 'active_all'
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cto_workflow_id', 'active_all')")
    cursor.execute("UPDATE settings SET value = 'active_all' WHERE key = 'cto_workflow_id' AND value = '1'")

    # Set defaults for polling options to false as webhooks are preferred to save Helius credits (use IGNORE so user preferences are preserved)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('wallet_tracker_poll_enabled', 'false')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('helius_poll_enabled', 'false')")

    # Ensure only real tokens with confirmed names exist in wallet_payments
    try:
        cursor.execute("""
            DELETE FROM wallet_payments
            WHERE token_name IS NULL
               OR TRIM(token_name) = ''
               OR UPPER(TRIM(token_name)) IN ('SOL', 'USDT', 'USDC', 'WSOL', 'WRAPPED SOL', 'UNKNOWN')
               OR ca IS NULL OR TRIM(ca) = ''
               OR market_cap IS NULL OR TRIM(market_cap) = '' OR UPPER(TRIM(market_cap)) = 'UNKNOWN'
               OR age IS NULL OR TRIM(age) = '' OR UPPER(TRIM(age)) = 'UNKNOWN'
               OR dex_url IS NULL OR TRIM(dex_url) = ''
        """)
    except Exception:
        pass

    conn.commit()


def get_db():
    """Return an in-memory query index hydrated directly from Supabase (no local SQLite file on disk)."""
    global global_conn
    with db_lock:
        if global_conn is not None:
            return global_conn

        global_conn = sqlite3.connect(":memory:", timeout=30, check_same_thread=False)
        try:
            global_conn.execute("PRAGMA synchronous = OFF;")
            global_conn.execute("PRAGMA temp_store = MEMORY;")
        except Exception as e:
            print(f"[MEMORY INDEX] Warning setting pragmas: {e}", flush=True)

        global_conn.row_factory = custom_row_factory
        _init_db_schema(global_conn)
        return global_conn


def get_settings():
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        settings = {}
        try:
            for row in cursor.execute('SELECT key, value FROM settings').fetchall():
                settings[row[0]] = row[1]
        except Exception:
            pass

        # Fallback to environment variables if not set in DB settings
        for k in ('helius_api_key', 'helius_api_key_2', 'helius_api_key_3'):
            if k not in settings and os.environ.get(k.upper()):
                settings[k] = os.environ.get(k.upper())

        return settings
