"""Rule tester API."""

from flask import Flask, render_template, request, jsonify

from bot.db.connection import supabase_client
from bot.engine.rules import process_message_logic
from bot.web import app

@app.route('/api/tester/workflows', methods=['GET'])
def get_tester_workflows():
    """Return active workflows directly from Supabase for the flow tester."""
    if not supabase_client:
        return jsonify({
            "success": False,
            "error": "Supabase is not configured. The tester does not use local workflow data."
        }), 503

    try:
        result = (
            supabase_client.table('workflows')
            .select('id,name')
            .eq('is_active', True)
            .order('name')
            .execute()
        )
        workflows = result.data or []
        return jsonify({"success": True, "workflows": workflows})
    except Exception as exc:
        print(f"[TESTER] Supabase workflow list error: {exc}", flush=True)
        return jsonify({
            "success": False,
            "error": "Could not load active workflows from Supabase. Check the Supabase service status and quota."
        }), 503


@app.route('/api/tester', methods=['POST'])
def run_tester():
    data = request.get_json(silent=True) or {}
    wf_id = data.get('workflow_id')
    text = str(data.get('text') or '').strip()

    if not wf_id:
        return jsonify({"success": False, "error": "Please select a workflow."}), 400
    if not text:
        return jsonify({"success": False, "error": "Please paste a sample message."}), 400

    try:
        wf_id = int(wf_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid workflow selection."}), 400

    if not supabase_client:
        return jsonify({
            "success": False,
            "error": "Supabase is not configured. The tester does not use local workflow data."
        }), 503

    try:
        wf_result = (
            supabase_client.table('workflows')
            .select('*')
            .eq('id', wf_id)
            .eq('is_active', True)
            .limit(1)
            .execute()
        )
        wf = wf_result.data[0] if wf_result.data else None
        if not wf:
            return jsonify({"success": False, "error": "Active Supabase workflow not found."}), 404

        rules_result = (
            supabase_client.table('rules')
            .select('*')
            .eq('workflow_id', wf_id)
            .order('id')
            .execute()
        )
        wf['rules'] = rules_result.data or []
    except Exception as exc:
        print(f"[TESTER] Supabase workflow test error: {exc}", flush=True)
        return jsonify({
            "success": False,
            "error": "Could not load this workflow from Supabase. Check the Supabase service status and quota."
        }), 503

    context = {"payer_address": data.get('payer_address', '9xQeWvG816bUx9EPjFWdd5AufqSSqeM2qN1xzybapC8G')}
    result_text, dropped, reason = process_message_logic(text, wf['rules'], context=context)

    return jsonify({
        "success": True,
        "dropped": dropped,
        "reason": reason,
        "text": result_text,
        "workflow_name": wf.get('name') or 'Unnamed Flow',
        "rule_count": len(wf.get('rules') or [])
    })
