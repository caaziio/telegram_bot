"""Workflow CRUD and workflow performance API."""

from flask import Flask, render_template, request, jsonify

from bot.config import debug_log
from bot.db.connection import db_lock, get_db, supabase_client, workflow_sync_lock
from bot.db.supabase_sync import mirror_workflow_to_sqlite, save_workflow_to_supabase
from bot.services.outcomes import get_historical_candidate_tokens, token_matches_workflow_rules
from bot.web import app

@app.route('/api/workflows', methods=['POST'])
def create_workflow():
    data = None
    try:
        data = request.json
        debug_log(f"DEBUG: create_workflow payload: {data}")
        wf_payload = {
            'name': data.get('name'),
            'source_channel': data.get('source_channel'),
            'source_channel_id': data.get('source_channel_id'),
            'target_channel': data.get('target_channel'),
            'target_channel_id': data.get('target_channel_id'),
            'is_active': True
        }
        rules = data.get('rules', [])
        wf_id = save_workflow_to_supabase(wf_payload, rules)
        mirror_workflow_to_sqlite(wf_id, wf_payload, rules)

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

            # Fetch current is_active so we preserve it unless explicitly provided
            curr_wf = cursor.execute('SELECT is_active FROM workflows WHERE id=?', (id,)).fetchone()
            curr_is_active = 1
            if curr_wf:
                curr_is_active = curr_wf.get('is_active') if isinstance(curr_wf, dict) else curr_wf[0]

            new_is_active = 1 if data.get('is_active', curr_is_active) else 0

        wf_payload = {
            'name': data.get('name'),
            'source_channel': data.get('source_channel'),
            'source_channel_id': data.get('source_channel_id'),
            'target_channel': data.get('target_channel'),
            'target_channel_id': data.get('target_channel_id'),
            'is_active': bool(new_is_active)
        }
        rules = data.get('rules', [])
        save_workflow_to_supabase(wf_payload, rules, wf_id=id)
        mirror_workflow_to_sqlite(id, wf_payload, rules)

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

    if not supabase_client:
        return jsonify({"success": False, "error": "Supabase is not configured."}), 503
    try:
        with workflow_sync_lock:
            result = supabase_client.table('workflows').update({'is_active': bool(new_status)}).eq('id', id).execute()
            if not result.data:
                raise RuntimeError(f"Workflow {id} does not exist in Supabase.")
        with db_lock:
            conn = get_db()
            conn.execute('UPDATE workflows SET is_active=? WHERE id=?', (new_status, id))
            conn.commit()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502
    return jsonify({"success": True, "is_active": bool(new_status)})

@app.route('/api/workflows/<int:id>', methods=['DELETE'])
def delete_workflow(id):
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase is not configured."}), 503
        with workflow_sync_lock:
            result = supabase_client.table('workflows').delete().eq('id', id).execute()
            if not result.data:
                raise RuntimeError(f"Workflow {id} does not exist in Supabase.")

        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM rules WHERE workflow_id=?', (id,))
            cursor.execute('DELETE FROM workflows WHERE id=?', (id,))
            conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/workflows/performance', methods=['GET'])
def get_workflows_performance():
    system = request.args.get('system', 'validated').lower().strip()
    timeframe = request.args.get('timeframe', 'day').lower().strip()
    date_from = request.args.get('date_from') or request.args.get('from_date') or request.args.get('from')
    date_to = request.args.get('date_to') or request.args.get('to_date') or request.args.get('to')
    sort_by = request.args.get('sort_by', 'win_rate_2x').lower().strip()

    # 1. Fetch active workflows & rules directly from Supabase
    wf_rows = []
    rule_rows = []
    if supabase_client:
        try:
            wf_rows = supabase_client.table('workflows').select('id,name,is_active').eq('is_active', True).execute().data or []
            rule_rows = supabase_client.table('rules').select('id,workflow_id,rule_type,search_text,replace_text,time_min,time_max').execute().data or []
        except Exception as e:
            debug_log(f"[WF PERF] Error pulling workflows/rules from Supabase: {e}")

    wf_map = {}
    for w in wf_rows:
        w_id = w['id']
        w_name = w['name']
        wf_map[w_id] = {'id': w_id, 'name': w_name, 'rules': []}

    for r in rule_rows:
        wid = r['workflow_id']
        if wid in wf_map:
            wf_map[wid]['rules'].append(r)

    # 2. Fetch candidate tokens
    candidate_tokens = get_historical_candidate_tokens(system, timeframe, date_from=date_from, date_to=date_to)

    # 3. Evaluate each workflow
    leaderboard = []
    for wf_id, wf in wf_map.items():
        matched = [t for t in candidate_tokens if token_matches_workflow_rules(t, wf['rules'])]
        if not matched:
            continue

        peaks_1h = [float(x.get('peak_mult_1h') or 1.0) for x in matched if x.get('peak_mult_1h') is not None]
        peaks_4h = [float(x.get('peak_mult_4h') or x.get('peak_mult_1h') or 1.0) for x in matched if (x.get('peak_mult_4h') is not None or x.get('peak_mult_1h') is not None)]
        drawdowns = [float(x.get('pre_peak_dd_1h_pct') or 0.0) for x in matched if x.get('pre_peak_dd_1h_pct') is not None]

        wins_2x = len([p for p in peaks_1h if p >= 2.0])
        wins_1_5x = len([p for p in peaks_1h if p >= 1.5])
        wins_5x = len([p for p in peaks_1h if p >= 5.0])

        avg_1h = sum(peaks_1h) / len(peaks_1h) if peaks_1h else 1.0
        avg_4h = sum(peaks_4h) / len(peaks_4h) if peaks_4h else 1.0
        avg_dd = sum(drawdowns) / len(drawdowns) if drawdowns else 0.0

        # Performance Tier Distribution (<1x, 1x-1.5x, 1.5x-2x, 2x-3x, 3x-5x, 5x+)
        t_lt_1x = sum(1 for p in peaks_1h if p < 1.05)
        t_1x_1_5x = sum(1 for p in peaks_1h if 1.05 <= p < 1.5)
        t_1_5x_2x = sum(1 for p in peaks_1h if 1.5 <= p < 2.0)
        t_2x_3x = sum(1 for p in peaks_1h if 2.0 <= p < 3.0)
        t_3x_5x = sum(1 for p in peaks_1h if 3.0 <= p < 5.0)
        t_5x_plus = sum(1 for p in peaks_1h if p >= 5.0)

        tot_peaks = len(peaks_1h) or 1
        tier_stats = {
            'lt_1x': t_lt_1x,
            'lt_1x_pct': round((t_lt_1x / tot_peaks) * 100.0, 1),
            '1x_1_5x': t_1x_1_5x,
            '1x_1_5x_pct': round((t_1x_1_5x / tot_peaks) * 100.0, 1),
            '1_5x_2x': t_1_5x_2x,
            '1_5x_2x_pct': round((t_1_5x_2x / tot_peaks) * 100.0, 1),
            '2x_3x': t_2x_3x,
            '2x_3x_pct': round((t_2x_3x / tot_peaks) * 100.0, 1),
            '3x_5x': t_3x_5x,
            '3x_5x_pct': round((t_3x_5x / tot_peaks) * 100.0, 1),
            '5x_plus': t_5x_plus,
            '5x_plus_pct': round((t_5x_plus / tot_peaks) * 100.0, 1),
        }

        # Trajectory Breakdown
        paths = [str(x.get('path_1h') or 'UNKNOWN').upper() for x in matched]
        tot_paths = len(paths) or 1
        trajectories = {
            'direct_pump': paths.count('DIRECT_PUMP'),
            'direct_pump_pct': round((paths.count('DIRECT_PUMP') / tot_paths) * 100.0, 1),
            'dip_then_pump': paths.count('DIP_THEN_PUMP'),
            'dip_then_pump_pct': round((paths.count('DIP_THEN_PUMP') / tot_paths) * 100.0, 1),
            'straight_dump': paths.count('STRAIGHT_DUMP'),
            'straight_dump_pct': round((paths.count('STRAIGHT_DUMP') / tot_paths) * 100.0, 1),
            'pump_then_dump': paths.count('PUMP_THEN_DUMP'),
            'pump_then_dump_pct': round((paths.count('PUMP_THEN_DUMP') / tot_paths) * 100.0, 1),
        }

        # Top 5 Token Roster
        sorted_matched = sorted(matched, key=lambda x: float(x.get('peak_mult_1h') or 1.0), reverse=True)
        top_tokens = []
        for t in sorted_matched[:5]:
            top_tokens.append({
                'name': t.get('name') or 'Unknown',
                'ca': t.get('ca') or '',
                'peak_1h': round(float(t.get('peak_mult_1h') or 1.0), 2),
                'peak_4h': round(float(t.get('peak_mult_4h') or t.get('peak_mult_1h') or 1.0), 2),
                'pre_peak_dd': round(float(t.get('pre_peak_dd_1h_pct') or 0.0), 1),
                'path': t.get('path_1h') or '',
                'dex_url': t.get('dex_url') or (f"https://dexscreener.com/solana/{t.get('ca')}" if t.get('ca') else ''),
                'market_cap': t.get('market_cap') or ''
            })

        best_token = sorted_matched[0] if sorted_matched else {}
        best_peak = round(float(best_token.get('peak_mult_1h') or 1.0), 2)

        leaderboard.append({
            'workflow_id': wf_id,
            'name': wf['name'],
            'rules_count': len(wf['rules']),
            'matched_count': len(matched),
            'win_rate_2x': round((wins_2x / len(matched)) * 100.0, 1),
            'win_rate_1_5x': round((wins_1_5x / len(matched)) * 100.0, 1),
            'win_rate_5x': round((wins_5x / len(matched)) * 100.0, 1),
            'avg_peak_1h': round(avg_1h, 2),
            'avg_peak_4h': round(avg_4h, 2),
            'avg_pre_peak_dd_pct': round(avg_dd, 1),
            'best_token_name': best_token.get('name') or 'Unknown',
            'best_token_ca': best_token.get('ca') or '',
            'best_token_peak': best_peak,
            'tier_stats': tier_stats,
            'trajectories': trajectories,
            'top_tokens': top_tokens,
        })

    # Sort leaderboard
    if sort_by == 'avg_peak_1h':
        leaderboard.sort(key=lambda x: (x['avg_peak_1h'], x['win_rate_2x'], x['matched_count']), reverse=True)
    elif sort_by == 'avg_peak_4h':
        leaderboard.sort(key=lambda x: (x['avg_peak_4h'], x['win_rate_2x'], x['matched_count']), reverse=True)
    elif sort_by == 'matched':
        leaderboard.sort(key=lambda x: (x['matched_count'], x['win_rate_2x']), reverse=True)
    elif sort_by == 'best_peak':
        leaderboard.sort(key=lambda x: (x['best_token_peak'], x['win_rate_2x']), reverse=True)
    else:  # win_rate_2x default
        leaderboard.sort(key=lambda x: (x['win_rate_2x'], x['avg_peak_1h'], x['matched_count']), reverse=True)

    best_workflow = leaderboard[0] if leaderboard else None

    tf_labels = {
        'day': 'Today (Past 24 Hours)',
        'week': 'This Week (Past 7 Days)',
        'month': 'This Month (Past 30 Days)',
        'all': 'All Time',
        'custom': f"Custom ({date_from or 'Start'} - {date_to or 'Present'})"
    }

    return jsonify({
        'success': True,
        'system': system,
        'timeframe': timeframe,
        'timeframe_label': tf_labels.get(timeframe, f"Custom ({date_from or 'Start'} - {date_to or 'Present'})" if timeframe == 'custom' else 'Custom Timeframe'),
        'date_from': date_from,
        'date_to': date_to,
        'total_signals_evaluated': len(candidate_tokens),
        'best_workflow': best_workflow,
        'leaderboard': leaderboard,
        'total_active_workflows': len(wf_map),
        'workflows_with_matches': len(leaderboard)
    })
