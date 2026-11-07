"""Supabase <-> SQLite synchronisation and workflow persistence."""

import asyncio
import os
import threading
import json

from bot.config import debug_log
from bot.db.connection import db_lock, get_db, supabase_client, workflow_sync_lock
from bot.services.token_indicators import (
    hydrate_wallet_payments_indicators,
    synthesize_token_indicators,
)

def sync_from_supabase():
    """Pull data from Supabase REST API into local SQLite cache"""
    if not supabase_client:
        return
    try:
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()

            # Sync Settings
            try:
                res = supabase_client.table('settings').select('*').execute()
                if res.data:
                    for row in res.data:
                        k = row.get('key')
                        v = row.get('value')
                        if k is not None and v is not None:
                            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
            except Exception as e:
                print(f"[SUPABASE] Settings pull notice: {e}", flush=True)

            # Sync Workflows & Rules. Serialize this with cloud-first writes so a
            # periodic pull can never overwrite an in-flight workflow save.
            try:
                with workflow_sync_lock:
                    wf_res = supabase_client.table('workflows').select('*').execute()
                    rules_res = supabase_client.table('rules').select('*').execute()
                    if wf_res.data:
                        rules_by_wf = {}
                        if rules_res.data:
                            for r in rules_res.data:
                                w_id = r.get('workflow_id')
                                if w_id is not None:
                                    rules_by_wf.setdefault(w_id, []).append(r)

                        for wf in wf_res.data:
                            wf_id = wf.get('id')
                            is_active_val = 1 if wf.get('is_active') is not False else 0
                            cursor.execute("""
                                INSERT INTO workflows (id, name, source_channel, source_channel_id, target_channel, target_channel_id, is_active)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET
                                    name=excluded.name,
                                    source_channel=excluded.source_channel,
                                    source_channel_id=excluded.source_channel_id,
                                    target_channel=excluded.target_channel,
                                    target_channel_id=excluded.target_channel_id,
                                    is_active=excluded.is_active
                            """, (
                                wf_id,
                                wf.get('name'),
                                wf.get('source_channel'),
                                wf.get('source_channel_id'),
                                wf.get('target_channel'),
                                wf.get('target_channel_id'),
                                is_active_val
                            ))

                            cursor.execute("DELETE FROM rules WHERE workflow_id = ?", (wf_id,))
                            for r in rules_by_wf.get(wf_id, []):
                                cursor.execute("""
                                    INSERT INTO rules (workflow_id, rule_type, search_text, replace_text, time_min, time_max)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                """, (
                                    wf_id,
                                    r.get('rule_type'),
                                    r.get('search_text'),
                                    r.get('replace_text'),
                                    r.get('time_min'),
                                    r.get('time_max')
                                ))
            except Exception as e:
                print(f"[SUPABASE] Workflows/Rules pull notice: {e}", flush=True)

            conn.commit()

        # Sync DEX Validated (instant) then Wallet Payments directly from Supabase
        sync_dex_validated_from_supabase()
        sync_wallet_payments_from_supabase()
    except Exception as e:
        print(f"[SUPABASE] Sync error: {e}", flush=True)

def sync_wallet_payments_from_supabase(limit_records=1000, full_sync=False):
    """Pull new and updated wallet_payments from Supabase into local SQLite cache"""
    try:
        cols = [
            "id", "tracked_address", "sender_address", "amount", "mint", "signature", "timestamp",
            "token_name", "ca", "platform", "market_cap", "age", "perf_5m", "perf_1h", "perf_6h", "perf_24h",
            "liquidity_usd", "volume_5m", "volume_1h", "volume_6h", "volume_24h",
            "dex_url", "migration_status", "status", "reason", "formatted_message", "created_at", "migration_age",
            "max_mc_1h", "peak_mult_1h", "min_mc_1h", "min_dd_1h_pct", "pre_peak_low_mc_1h", "pre_peak_dd_1h_pct", "path_1h",
            "max_mc_4h", "peak_mult_4h", "min_mc_4h", "min_dd_4h_pct", "pre_peak_low_mc_4h", "pre_peak_dd_4h_pct", "path_4h",
            "perf_checked_stage", "metrics_data"
        ]
        col_str = ", ".join(cols)
        ph_str = ", ".join(["?" for _ in cols])
        perf_cols = {
            "max_mc_1h", "peak_mult_1h", "min_mc_1h", "min_dd_1h_pct", "pre_peak_low_mc_1h", "pre_peak_dd_1h_pct", "path_1h",
            "max_mc_4h", "peak_mult_4h", "min_mc_4h", "min_dd_4h_pct", "pre_peak_low_mc_4h", "pre_peak_dd_4h_pct", "path_4h",
            "perf_checked_stage", "metrics_data"
        }
        update_assignments = ", ".join([
            f"{c}=COALESCE(excluded.{c}, wallet_payments.{c})" if c in perf_cols else f"{c}=excluded.{c}"
            for c in cols if c != "id"
        ])
        upsert_sql = f"INSERT INTO wallet_payments ({col_str}) VALUES ({ph_str}) ON CONFLICT(id) DO UPDATE SET {update_assignments}"

        def is_fully_enriched(item):
            token_name = str(item.get('token_name') or '').strip()
            market_cap = str(item.get('market_cap') or '').strip()
            age = str(item.get('age') or '').strip()
            return (
                token_name.upper() not in ('', 'SOL', 'USDT', 'USDC', 'WSOL', 'WRAPPED SOL', 'UNKNOWN')
                and bool(str(item.get('ca') or '').strip())
                and market_cap.upper() not in ('', 'UNKNOWN')
                and age.upper() not in ('', 'UNKNOWN')
                and bool(str(item.get('dex_url') or '').strip())
            )

        direct_url = os.getenv('DIRECT_URL') or os.getenv('DATABASE_URL')
        if direct_url:
            try:
                import psycopg2
                with psycopg2.connect(direct_url, connect_timeout=8) as pg_conn:
                    with pg_conn.cursor() as pg_cur:
                        with db_lock:
                            sq_conn = get_db()
                            sq_cur = sq_conn.cursor()
                            max_local_id = sq_cur.execute("SELECT COALESCE(MAX(id), 0) FROM wallet_payments").fetchone()[0]
                            local_cnt = sq_cur.execute("SELECT COUNT(*) FROM wallet_payments").fetchone()[0]

                        # The local cache intentionally contains only enriched rows,
                        # so its count must not be compared with the unfiltered remote
                        # table count. That comparison caused a full-table reload every
                        # minute. Use a bounded overlap to pick up recent updates.
                        start_id = 0 if (full_sync or max_local_id == 0) else max(0, max_local_id - limit_records)

                        stream_cur = pg_conn.cursor()
                        stream_cur.execute("""
                            SELECT id, tracked_address, sender_address, amount, mint, signature, timestamp,
                                   token_name, ca, platform, market_cap, age, perf_5m, perf_1h, perf_6h, perf_24h,
                                   liquidity_usd, volume_5m, volume_1h, volume_6h, volume_24h,
                                   dex_url, migration_status, status, reason, formatted_message, created_at, migration_age,
                                   max_mc_1h, peak_mult_1h, min_mc_1h, min_dd_1h_pct, pre_peak_low_mc_1h, pre_peak_dd_1h_pct, path_1h,
                                   max_mc_4h, peak_mult_4h, min_mc_4h, min_dd_4h_pct, pre_peak_low_mc_4h, pre_peak_dd_4h_pct, path_4h,
                                   perf_checked_stage, metrics_data
                            FROM wallet_payments
                            WHERE id >= %s
                              AND token_name IS NOT NULL
                              AND TRIM(token_name) != ''
                              AND token_name NOT IN ('SOL', 'USDT', 'USDC', 'WSOL', 'Wrapped SOL', 'Unknown')
                              AND ca IS NOT NULL AND TRIM(ca) != ''
                              AND market_cap IS NOT NULL AND TRIM(market_cap) != '' AND UPPER(TRIM(market_cap)) != 'UNKNOWN'
                              AND age IS NOT NULL AND TRIM(age) != '' AND UPPER(TRIM(age)) != 'UNKNOWN'
                              AND dex_url IS NOT NULL AND TRIM(dex_url) != ''
                            ORDER BY id DESC;
                        """, (start_id,))
                        total_synced = 0
                        max_synced_id = start_id
                        while True:
                            rows = stream_cur.fetchmany(1000)
                            if not rows:
                                break
                            clean_rows = []
                            for r in rows:
                                if r[0] and r[0] > max_synced_id:
                                    max_synced_id = r[0]
                                rl = list(r)
                                # Normalize migration_status if it is raw DETECTED or missing
                                m_status = str(rl[22] or '')
                                fmt_msg = str(rl[25] or '')
                                if 'migrated: yes' in fmt_msg.lower() or 'status: ✅ migrated' in fmt_msg.lower():
                                    if not m_status.lower().startswith('migrated') and not m_status.lower().startswith('✅ migrated'):
                                        rl[22] = 'Migrated'
                                elif 'migrated: no' in fmt_msg.lower():
                                    if m_status != 'Not Migrated':
                                        rl[22] = 'Not Migrated'

                                for i, v in enumerate(rl):
                                    if hasattr(v, 'isoformat'):
                                        rl[i] = str(v)
                                    elif isinstance(v, (dict, list)):
                                        rl[i] = json.dumps(v)

                                m_idx = cols.index('metrics_data')
                                if not rl[m_idx] or rl[m_idx] == 'null':
                                    row_dict = {
                                        'perf_5m': rl[cols.index('perf_5m')],
                                        'perf_1h': rl[cols.index('perf_1h')],
                                        'perf_6h': rl[cols.index('perf_6h')],
                                        'perf_24h': rl[cols.index('perf_24h')],
                                        'liquidity_usd': rl[cols.index('liquidity_usd')],
                                        'volume_5m': rl[cols.index('volume_5m')],
                                        'volume_1h': rl[cols.index('volume_1h')],
                                        'volume_6h': rl[cols.index('volume_6h')],
                                        'volume_24h': rl[cols.index('volume_24h')],
                                        'market_cap': rl[cols.index('market_cap')],
                                        'formatted_message': rl[cols.index('formatted_message')]
                                    }
                                    syn = synthesize_token_indicators(row_dict)
                                    if syn:
                                        rl[m_idx] = json.dumps(syn)

                                clean_rows.append(tuple(rl))
                            if clean_rows:
                                with db_lock:
                                    sq_conn = get_db()
                                    sq_cur = sq_conn.cursor()
                                    sq_cur.executemany(upsert_sql, clean_rows)
                                    sq_conn.commit()
                                total_synced += len(clean_rows)
                        stream_cur.close()
                        pg_conn.commit()
                        hydrate_wallet_payments_indicators()
                        if total_synced:
                            debug_log(f"[SUPABASE] Synced {total_synced} wallet_payments from Postgres (from ID {start_id} up to ID {max_synced_id})")
                        return total_synced
            except Exception as pg_err:
                print(f"[SUPABASE] Direct Postgres wallet sync notice: {pg_err}", flush=True)

        if supabase_client:
            with db_lock:
                sq_conn = get_db()
                sq_cur = sq_conn.cursor()
                max_local_id = sq_cur.execute("SELECT COALESCE(MAX(id), 0) FROM wallet_payments").fetchone()[0]
                local_cnt = sq_cur.execute("SELECT COUNT(*) FROM wallet_payments").fetchone()[0]

            # Check Supabase total count
            total_sb_count = 0
            try:
                cnt_res = supabase_client.table('wallet_payments').select('id', count='exact').limit(1).execute()
                total_sb_count = cnt_res.count or 0
            except Exception:
                pass

            total_synced = 0

            # If production has missing data (e.g. only 1,000 rows vs 20,000+ in Supabase, or 0 rows on fresh deploy)
            if full_sync or max_local_id == 0:
                debug_log(f"[SUPABASE] Missing records detected (Local: {local_cnt}, Supabase: {total_sb_count}). Starting bulk REST pagination...")
                current_cursor_id = 0 if (full_sync or local_cnt == 0) else max(0, max_local_id - 50)
                while True:
                    res = supabase_client.table('wallet_payments').select('*').gt('id', current_cursor_id).order('id', desc=False).limit(1000).execute()
                    if not res.data:
                        break

                    clean_rows = []
                    batch_max_id = current_cursor_id
                    for item in res.data:
                        item_id = item.get('id')
                        if item_id and item_id > batch_max_id:
                            batch_max_id = item_id
                        if not is_fully_enriched(item):
                            continue
                        row_m = item.get('metrics_data')
                        if not row_m or row_m == 'null':
                            syn = synthesize_token_indicators(item)
                            row_m = json.dumps(syn) if syn else None
                        elif isinstance(row_m, (dict, list)):
                            row_m = json.dumps(row_m)
                        clean_rows.append(tuple(
                            row_m if c == 'metrics_data' else item.get(c)
                            for c in cols
                        ))

                    if clean_rows:
                        with db_lock:
                            sq_conn = get_db()
                            sq_cur = sq_conn.cursor()
                            sq_cur.executemany(upsert_sql, clean_rows)
                            sq_conn.commit()

                    total_synced += len(clean_rows)
                    if len(res.data) < 1000 or batch_max_id <= current_cursor_id:
                        break
                    current_cursor_id = batch_max_id

                hydrate_wallet_payments_indicators()
                debug_log(f"[SUPABASE] Bulk REST sync complete: synced {total_synced} records (up to ID {current_cursor_id})")
                return total_synced
            else:
                # Routine sync: sync recent updates and new payments
                start_id = max(0, max_local_id - limit_records)
                current_cursor_id = start_id
                while True:
                    res = supabase_client.table('wallet_payments').select('*').gte('id', current_cursor_id).order('id', desc=False).limit(1000).execute()
                    if not res.data:
                        break
                    clean_rows = []
                    batch_max_id = current_cursor_id
                    for item in res.data:
                        item_id = item.get('id')
                        if item_id and item_id > batch_max_id:
                            batch_max_id = item_id
                        if not is_fully_enriched(item):
                            continue
                        row_m = item.get('metrics_data')
                        if not row_m or row_m == 'null':
                            syn = synthesize_token_indicators(item)
                            row_m = json.dumps(syn) if syn else None
                        elif isinstance(row_m, (dict, list)):
                            row_m = json.dumps(row_m)
                        clean_rows.append(tuple(
                            row_m if c == 'metrics_data' else item.get(c)
                            for c in cols
                        ))
                    if clean_rows:
                        with db_lock:
                            sq_conn = get_db()
                            sq_cur = sq_conn.cursor()
                            sq_cur.executemany(upsert_sql, clean_rows)
                            sq_conn.commit()
                    total_synced += len(clean_rows)
                    if len(res.data) < 1000 or batch_max_id <= current_cursor_id:
                        break
                    current_cursor_id = batch_max_id + 1
                hydrate_wallet_payments_indicators()
                return total_synced
        return 0
    except Exception as e:
        print(f"[SUPABASE] wallet_payments sync error: {e}", flush=True)
        return 0


def sync_dex_validated_from_supabase(full_sync=False):
    """Pull dex_validated rows directly from Supabase into the in-memory query index."""
    if not supabase_client:
        return 0
    try:
        cols = [
            "id", "ca", "name", "platform", "market_cap", "age",
            "perf_5m", "perf_1h", "perf_6h", "perf_24h",
            "liquidity_usd", "volume_5m", "volume_1h", "volume_6h", "volume_24h",
            "status", "dex_url", "migration_status", "signal_type",
            "payment_timestamp", "order_status", "published_at", "fetched_at",
            "detection_seconds", "reason", "formatted_message", "created_at",
            "max_mc_1h", "peak_mult_1h", "min_mc_1h", "min_dd_1h_pct", "pre_peak_low_mc_1h", "pre_peak_dd_1h_pct", "path_1h",
            "max_mc_4h", "peak_mult_4h", "min_mc_4h", "min_dd_4h_pct", "pre_peak_low_mc_4h", "pre_peak_dd_4h_pct", "path_4h",
            "perf_checked_stage", "metrics_data"
        ]
        perf_cols = {
            "max_mc_1h", "peak_mult_1h", "min_mc_1h", "min_dd_1h_pct", "pre_peak_low_mc_1h", "pre_peak_dd_1h_pct", "path_1h",
            "max_mc_4h", "peak_mult_4h", "min_mc_4h", "min_dd_4h_pct", "pre_peak_low_mc_4h", "pre_peak_dd_4h_pct", "path_4h"
        }
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        update_assignments = []
        for c in cols:
            if c == "perf_checked_stage":
                update_assignments.append("perf_checked_stage=MAX(COALESCE(dex_validated.perf_checked_stage, 0), COALESCE(excluded.perf_checked_stage, 0))")
            elif c in perf_cols:
                update_assignments.append(f"{c}=COALESCE(excluded.{c}, dex_validated.{c})")
            elif c == "status":
                update_assignments.append("status=CASE WHEN dex_validated.status = 'forwarded' THEN 'forwarded' WHEN excluded.status = 'forwarded' THEN 'forwarded' ELSE excluded.status END")
            else:
                update_assignments.append(f"{c}=excluded.{c}")
        upsert_sql = (
            f"INSERT INTO dex_validated ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(ca, signal_type, payment_timestamp, order_status) DO UPDATE SET {', '.join(update_assignments)}"
        )

        total_synced = 0
        current_cursor_id = 0
        while True:
            res = (
                supabase_client.table('dex_validated')
                .select('*')
                .gte('id', current_cursor_id)
                .order('id', desc=False)
                .limit(1000)
                .execute()
            )
            if not res.data:
                break
            clean_rows = []
            batch_max_id = current_cursor_id
            for item in res.data:
                item_id = item.get('id') or 0
                if item_id > batch_max_id:
                    batch_max_id = item_id
                clean_rows.append(tuple(
                    json.dumps(item.get(c)) if c == 'metrics_data' and isinstance(item.get(c), (dict, list)) else item.get(c)
                    for c in cols
                ))
            if clean_rows:
                with db_lock:
                    sq_conn = get_db()
                    sq_cur = sq_conn.cursor()
                    sq_cur.executemany(upsert_sql, clean_rows)
                    sq_conn.commit()
            total_synced += len(clean_rows)
            if len(res.data) < 1000 or batch_max_id <= current_cursor_id:
                break
            current_cursor_id = batch_max_id + 1
        return total_synced
    except Exception as e:
        print(f"[SUPABASE] dex_validated sync error: {e}", flush=True)
        return 0


def push_to_supabase_table(table_name, record, on_conflict=None):
    """Helper to push a single record or upsert into Supabase"""
    if not supabase_client:
        return
    def _do_push():
        try:
            if on_conflict:
                supabase_client.table(table_name).upsert(record, on_conflict=on_conflict).execute()
            elif table_name == 'wallet_payments' and 'signature' in record and record.get('signature'):
                supabase_client.table(table_name).upsert(record, on_conflict='signature').execute()
            elif table_name == 'dex_validated':
                # Never overwrite an existing 'forwarded' status with 'detected' or 'skipped' in Supabase
                status_to_push = record.get('status')
                if status_to_push and status_to_push != 'forwarded':
                    try:
                        existing = supabase_client.table('dex_validated')\
                            .select('status')\
                            .eq('ca', record.get('ca'))\
                            .eq('signal_type', record.get('signal_type'))\
                            .eq('payment_timestamp', record.get('payment_timestamp'))\
                            .eq('order_status', record.get('order_status'))\
                            .limit(1)\
                            .execute()
                        if existing and existing.data and str(existing.data[0].get('status')).lower() == 'forwarded':
                            record['status'] = 'forwarded'
                    except Exception:
                        pass
                supabase_client.table(table_name).upsert(record, on_conflict='ca,signal_type,payment_timestamp,order_status').execute()
            else:
                supabase_client.table(table_name).upsert(record).execute()
        except Exception as e:
            print(f"[SUPABASE] Push error on {table_name}: {e}", flush=True)
    threading.Thread(target=_do_push, daemon=True).start()

def delete_from_supabase_table(table_name, key_col, key_val):
    """Helper to delete records from Supabase"""
    if not supabase_client:
        return
    def _do_delete():
        try:
            supabase_client.table(table_name).delete().eq(key_col, key_val).execute()
        except Exception as e:
            print(f"[SUPABASE] Delete error on {table_name}: {e}", flush=True)
    threading.Thread(target=_do_delete, daemon=True).start()

def _workflow_rules_payload(wf_id, rules_list):
    return [{
        'workflow_id': wf_id,
        'rule_type': rule.get('rule_type'),
        'search_text': rule.get('search_text'),
        'replace_text': rule.get('replace_text'),
        'time_min': rule.get('time_min'),
        'time_max': rule.get('time_max')
    } for rule in (rules_list or [])]


def save_workflow_to_supabase(wf_payload, rules_list, wf_id=None):
    """Persist a workflow to Supabase synchronously and return its cloud ID.

    The caller must not update SQLite or report success until this returns.
    Existing cloud data is restored on a failed update where possible.
    """
    if not supabase_client:
        raise RuntimeError("Supabase is not configured; workflow was not saved.")

    with workflow_sync_lock:
        if wf_id is None:
            # Older imports used explicit IDs without advancing Postgres's identity
            # sequence. Allocate from the actual cloud maximum and retry if another
            # writer claims the same ID concurrently.
            latest = supabase_client.table('workflows').select('id').order('id', desc=True).limit(1).execute()
            candidate_id = (latest.data[0]['id'] if latest.data else 0) + 1
            created = None
            for _ in range(5):
                try:
                    created = supabase_client.table('workflows').insert({
                        **wf_payload,
                        'id': candidate_id
                    }).execute()
                    break
                except Exception as exc:
                    if '23505' not in str(exc) and 'duplicate key' not in str(exc).lower():
                        raise
                    candidate_id += 1
            if not created or not created.data or created.data[0].get('id') is None:
                raise RuntimeError("Supabase could not allocate an ID for the new workflow.")
            wf_id = created.data[0]['id']
            try:
                cloud_rules = _workflow_rules_payload(wf_id, rules_list)
                if cloud_rules:
                    supabase_client.table('rules').insert(cloud_rules).execute()
            except Exception:
                supabase_client.table('workflows').delete().eq('id', wf_id).execute()
                raise
            return wf_id

        old_wf_res = supabase_client.table('workflows').select('*').eq('id', wf_id).execute()
        if not old_wf_res.data:
            raise RuntimeError(f"Workflow {wf_id} does not exist in Supabase.")
        old_wf = old_wf_res.data[0]
        old_rules = supabase_client.table('rules').select('*').eq('workflow_id', wf_id).execute().data or []

        try:
            supabase_client.table('workflows').update(wf_payload).eq('id', wf_id).execute()
            supabase_client.table('rules').delete().eq('workflow_id', wf_id).execute()
            cloud_rules = _workflow_rules_payload(wf_id, rules_list)
            if cloud_rules:
                supabase_client.table('rules').insert(cloud_rules).execute()
        except Exception:
            restore_wf = {key: value for key, value in old_wf.items() if key != 'id'}
            supabase_client.table('workflows').update(restore_wf).eq('id', wf_id).execute()
            supabase_client.table('rules').delete().eq('workflow_id', wf_id).execute()
            restore_rules = [
                {key: value for key, value in rule.items() if key != 'id'}
                for rule in old_rules
            ]
            if restore_rules:
                supabase_client.table('rules').insert(restore_rules).execute()
            raise
        return wf_id


def mirror_workflow_to_sqlite(wf_id, wf_payload, rules_list):
    """Refresh the local runtime cache only after Supabase confirms the save."""
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO workflows (id, name, source_channel, source_channel_id, target_channel, target_channel_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                source_channel=excluded.source_channel,
                source_channel_id=excluded.source_channel_id,
                target_channel=excluded.target_channel,
                target_channel_id=excluded.target_channel_id,
                is_active=excluded.is_active
        """, (
            wf_id,
            wf_payload.get('name'),
            wf_payload.get('source_channel'),
            wf_payload.get('source_channel_id'),
            wf_payload.get('target_channel'),
            wf_payload.get('target_channel_id'),
            1 if wf_payload.get('is_active', True) else 0
        ))
        cursor.execute('DELETE FROM rules WHERE workflow_id=?', (wf_id,))
        for rule in rules_list or []:
            cursor.execute('''
                INSERT INTO rules (workflow_id, rule_type, search_text, replace_text, time_min, time_max)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (wf_id, rule.get('rule_type'), rule.get('search_text'), rule.get('replace_text'), rule.get('time_min'), rule.get('time_max')))
        conn.commit()

async def supabase_sync_loop():
    if not supabase_client:
        return
    print("[SUPABASE] Started background Supabase synchronization loop.", flush=True)
    await asyncio.sleep(2)
    try:
        await asyncio.to_thread(sync_from_supabase)
        print("[SUPABASE] Initial sync from Supabase complete.", flush=True)
    except Exception as e:
        print(f"[SUPABASE] Initial sync error: {e}", flush=True)

    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(sync_from_supabase)
        except Exception as e:
            print(f"[SUPABASE] Periodic sync error: {e}", flush=True)
