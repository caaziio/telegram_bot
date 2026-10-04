import os
import sqlite3
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
direct_url = os.environ.get('DIRECT_URL') or os.environ.get('DATABASE_URL')
if not direct_url:
    print("Error: DIRECT_URL or DATABASE_URL not set in .env")
    exit(1)

print(f"Connecting to Supabase PostgreSQL at {direct_url.split('@')[1] if '@' in direct_url else '...'}")
pg_conn = psycopg2.connect(direct_url)
pg_cursor = pg_conn.cursor()

sqlite_conn = sqlite3.connect('db/bot.sqlite')
sqlite_cursor = sqlite_conn.cursor()

# 1. SETTINGS
settings = sqlite_cursor.execute("SELECT key, value FROM settings").fetchall()
print(f"\n[1/5] Uploading {len(settings)} Settings...")
psycopg2.extras.execute_values(
    pg_cursor,
    """
    INSERT INTO settings (key, value)
    VALUES %s
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """,
    settings
)
pg_conn.commit()
print(" -> Settings uploaded successfully.")

# 2. WORKFLOWS
workflows = sqlite_cursor.execute("""
    SELECT id, name, source_channel, source_channel_id, target_channel, target_channel_id, is_active 
    FROM workflows
""").fetchall()
print(f"\n[2/5] Uploading {len(workflows)} Workflows...")
wf_data = [(w[0], w[1], w[2], w[3], w[4], w[5], bool(w[6])) for w in workflows]
psycopg2.extras.execute_values(
    pg_cursor,
    """
    INSERT INTO workflows (id, name, source_channel, source_channel_id, target_channel, target_channel_id, is_active)
    VALUES %s
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        source_channel = EXCLUDED.source_channel,
        source_channel_id = EXCLUDED.source_channel_id,
        target_channel = EXCLUDED.target_channel,
        target_channel_id = EXCLUDED.target_channel_id,
        is_active = EXCLUDED.is_active
    """,
    wf_data
)
pg_cursor.execute("SELECT setval('workflows_id_seq', (SELECT COALESCE(MAX(id), 1) FROM workflows));")
pg_conn.commit()
print(" -> Workflows uploaded successfully.")

# 3. RULES
rules = sqlite_cursor.execute("""
    SELECT id, workflow_id, rule_type, search_text, replace_text, time_min, time_max 
    FROM rules
""").fetchall()
print(f"\n[3/5] Uploading {len(rules)} Rules...")
psycopg2.extras.execute_values(
    pg_cursor,
    """
    INSERT INTO rules (id, workflow_id, rule_type, search_text, replace_text, time_min, time_max)
    VALUES %s
    ON CONFLICT (id) DO UPDATE SET
        workflow_id = EXCLUDED.workflow_id,
        rule_type = EXCLUDED.rule_type,
        search_text = EXCLUDED.search_text,
        replace_text = EXCLUDED.replace_text,
        time_min = EXCLUDED.time_min,
        time_max = EXCLUDED.time_max
    """,
    rules
)
pg_cursor.execute("SELECT setval('rules_id_seq', (SELECT COALESCE(MAX(id), 1) FROM rules));")
pg_conn.commit()
print(" -> Rules uploaded successfully.")

# 4. CTO SIGNALS
signals = sqlite_cursor.execute("""
    SELECT id, ca, name, platform, market_cap, age, perf_5m, perf_1h, perf_6h, perf_24h, 
           status, dex_url, migration_status, workflow_id, signal_type, payment_timestamp, 
           order_status, reason, created_at
    FROM cto_signals ORDER BY id ASC
""").fetchall()
print(f"\n[4/5] Uploading {len(signals)} CTO Signals...")
# Upload in chunks of 500
chunk_size = 500
for i in range(0, len(signals), chunk_size):
    chunk = signals[i:i+chunk_size]
    psycopg2.extras.execute_values(
        pg_cursor,
        """
        INSERT INTO cto_signals (
            id, ca, name, platform, market_cap, age, perf_5m, perf_1h, perf_6h, perf_24h, 
            status, dex_url, migration_status, workflow_id, signal_type, payment_timestamp, 
            order_status, reason, created_at
        )
        VALUES %s
        ON CONFLICT (id) DO NOTHING
        """,
        chunk,
        page_size=500
    )
    pg_conn.commit()
    print(f"  -> Uploaded signals {min(i+chunk_size, len(signals))}/{len(signals)}")
pg_cursor.execute("SELECT setval('cto_signals_id_seq', (SELECT COALESCE(MAX(id), 1) FROM cto_signals));")
pg_conn.commit()
print(" -> All CTO Signals uploaded successfully.")

# 5. WALLET PAYMENTS
payments = sqlite_cursor.execute("""
    SELECT id, tracked_address, sender_address, amount, mint, signature, timestamp, created_at
    FROM wallet_payments ORDER BY id ASC
""").fetchall()
print(f"\n[5/5] Uploading {len(payments)} Wallet Payments...")
for i in range(0, len(payments), 1000):
    chunk = payments[i:i+1000]
    psycopg2.extras.execute_values(
        pg_cursor,
        """
        INSERT INTO wallet_payments (
            id, tracked_address, sender_address, amount, mint, signature, timestamp, created_at
        )
        VALUES %s
        ON CONFLICT (signature) DO NOTHING
        """,
        chunk,
        page_size=1000
    )
    pg_conn.commit()
    print(f"  -> Uploaded payments {min(i+1000, len(payments))}/{len(payments)}")
pg_cursor.execute("SELECT setval('wallet_payments_id_seq', (SELECT COALESCE(MAX(id), 1) FROM wallet_payments));")
pg_conn.commit()
print(" -> All Wallet Payments uploaded successfully.")

print("\n" + "=" * 60)
print("SUCCESS: ALL TURSO DATA TRANSFERRED TO SUPABASE!")
print("=" * 60)

# Final verification counts from Supabase
for tbl in ['settings', 'workflows', 'rules', 'cto_signals', 'wallet_payments']:
    pg_cursor.execute(f"SELECT count(*) FROM {tbl};")
    print(f" Supabase Table '{tbl}': {pg_cursor.fetchone()[0]} rows")

pg_conn.close()
sqlite_conn.close()
