"""HTML page routes."""

from flask import Flask, render_template, request, jsonify

from bot.config import get_app_version
from bot.db.connection import get_settings
from bot.engine.rules import get_workflows
from bot.web import app

@app.route('/')
@app.route('/dashboard')
@app.route('/workflows')
@app.route('/outcomes')
@app.route('/wallet-tracker')
@app.route('/token-data')
@app.route('/dex-validated')
@app.route('/dex-validated-data')
@app.route('/settings')
def index():
    workflows = get_workflows()
    settings = get_settings()
    return render_template('index.html', workflows=workflows, settings=settings, app_version=get_app_version())
