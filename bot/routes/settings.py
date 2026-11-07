"""Settings API."""

from flask import Flask, render_template, request, jsonify

from bot.db.connection import db_lock, get_db, supabase_client
from bot.db.supabase_sync import push_to_supabase_table
from bot.web import app

@app.route('/api/settings', methods=['POST'])
def save_settings():
    data = request.json
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        for key, value in data.items():
            cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
            if supabase_client:
                push_to_supabase_table('settings', {'key': key, 'value': str(value)})
        conn.commit()
    return jsonify({"success": True})
