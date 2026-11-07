"""Performance scanner API."""

import asyncio
import threading
from flask import Flask, render_template, request, jsonify

from bot.db.connection import db_lock, get_db, supabase_client
from bot.db.supabase_sync import push_to_supabase_table
from bot.services.performance import (
    _sync_perf_scanner_settings,
    perf_scanner_lock,
    perf_scanner_runtime,
    scan_post_signal_performance_batch,
)
from bot.web import app

@app.route('/api/performance/status', methods=['GET'])
def get_performance_scanner_status():
    _sync_perf_scanner_settings()
    with perf_scanner_lock:
        rt = dict(perf_scanner_runtime)
    try:
        with db_lock:
            conn = get_db()
            cur = conn.cursor()
            dv_done = cur.execute("SELECT COUNT(*) FROM dex_validated WHERE COALESCE(perf_checked_stage, 0) > 0 OR peak_mult_1h IS NOT NULL").fetchone()[0]
            wp_done = cur.execute("SELECT COUNT(*) FROM wallet_payments WHERE COALESCE(perf_checked_stage, 0) > 0 OR peak_mult_1h IS NOT NULL").fetchone()[0]
            dv_total = cur.execute("SELECT COUNT(*) FROM dex_validated").fetchone()[0]
            wp_total = cur.execute("SELECT COUNT(*) FROM wallet_payments").fetchone()[0]
        rt['dex_validated_checked'] = dv_done
        rt['wallet_payments_checked'] = wp_done
        rt['dex_validated_total'] = dv_total
        rt['wallet_payments_total'] = wp_total

        if supabase_client:
            try:
                cnt_done = supabase_client.table('wallet_payments').select('id', count='exact').neq('min_mc_1h', 0).not_.is_('min_mc_1h', 'null').limit(1).execute()
                cnt_total = supabase_client.table('wallet_payments').select('id', count='exact').limit(1).execute()
                if cnt_total.count:
                    rt['wallet_payments_total'] = cnt_total.count
                if cnt_done.count is not None:
                    rt['wallet_payments_checked'] = cnt_done.count
            except Exception:
                pass
    except Exception:
        pass
    return jsonify({'success': True, 'status': rt})


@app.route('/api/performance/toggle_backfill', methods=['POST'])
def toggle_performance_backfill():
    data = request.get_json(silent=True) or {}
    with perf_scanner_lock:
        if 'enabled' in data:
            new_state = bool(data['enabled'])
        else:
            new_state = not perf_scanner_runtime.get('backfill_wallet_payments', False)
        perf_scanner_runtime['backfill_wallet_payments'] = new_state
        perf_scanner_runtime['wake_requested'] = True

    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('perf_backfill_wp_enabled', str(new_state)))
        if supabase_client:
            push_to_supabase_table('settings', {'key': 'perf_backfill_wp_enabled', 'value': str(new_state)})
        conn.commit()

    return jsonify({
        'success': True,
        'backfill_wallet_payments': new_state,
        'message': f"Paid transactions backfill {'started' if new_state else 'stopped'}."
    })


@app.route('/api/performance/config', methods=['POST'])
def update_performance_config():
    data = request.get_json(silent=True) or {}
    interval = data.get('interval_minutes')
    if interval is None:
        return jsonify({'success': False, 'error': 'Missing interval_minutes'}), 400
    try:
        interval_val = max(0.0, float(interval))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid interval_minutes value'}), 400

    with perf_scanner_lock:
        perf_scanner_runtime['scan_interval_minutes'] = interval_val
        perf_scanner_runtime['wake_requested'] = True

    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('perf_scan_interval_minutes', str(interval_val)))
        if supabase_client:
            push_to_supabase_table('settings', {'key': 'perf_scan_interval_minutes', 'value': str(interval_val)})
        conn.commit()

    return jsonify({
        'success': True,
        'scan_interval_minutes': interval_val,
        'message': f"Outcome scan interval set to {interval_val} minutes."
    })


@app.route('/api/performance/scan', methods=['POST'])
def trigger_performance_scan_now():
    batch_size = int((request.json or {}).get('batch_size', 40)) if request.is_json else 40
    batch_size = min(max(10, batch_size), 120)
    with perf_scanner_lock:
        if perf_scanner_runtime['running']:
            return jsonify({'success': True, 'message': '1h/4h OHLCV Peak scan is already running in the background.', 'status': dict(perf_scanner_runtime)})
    # Always run on a dedicated thread/loop so the slow OHLCV pass never competes
    # with the Telegram/WebSocket work on the main event loop.
    threading.Thread(target=lambda: asyncio.run(scan_post_signal_performance_batch(max_rows_per_table=batch_size)), daemon=True).start()
    return jsonify({'success': True, 'message': f'Started 1h/4h OHLCV Peak & Trajectory scan (up to {batch_size} rows per table).'})
