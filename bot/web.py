"""Flask application instance. Route modules attach to ``app`` via ``@app.route``."""

import os
from flask import Flask

from bot.config import get_app_version

# templates/ and static/ live at the project root, one level above this package
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__,
            template_folder=os.path.join(current_dir, 'templates'),
            static_folder=os.path.join(current_dir, 'static'))
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

@app.context_processor
def inject_app_version():
    return {'app_version': get_app_version()}
