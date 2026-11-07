"""Database initialisation and startup hydration from Supabase."""

from bot.db.connection import _init_db_schema, db_lock, get_db, supabase_client
from bot.db.supabase_sync import sync_from_supabase
from bot.services.token_indicators import hydrate_wallet_payments_indicators

def init_db():
    with db_lock:
        conn = get_db()
        _init_db_schema(conn)

    # Immediately hydrate the in-memory query index from Supabase on startup (zero local disk DB)
    if supabase_client:
        try:
            sync_from_supabase()
            hydrate_wallet_payments_indicators()
            print("[SUPABASE] Hydrated in-memory index directly from Supabase.", flush=True)
        except Exception as e:
            print(f"[SUPABASE] Startup hydration notice: {e}", flush=True)
