# netops/app.py
from __future__ import annotations
from datetime import datetime
from flask import Flask
from flask_cors import CORS
from .config import config
from .db import init_db
from .routes.api import api
from .routes.pages import pages

app = Flask(__name__, static_folder="../static", template_folder="../templates")
app.config.update(SECRET_KEY=config.SECRET_KEY, ENV=config.ENV, DEBUG=config.DEBUG)

# Init DB (bootstrap for first run)
init_db()

# Rate limiter: use the limiter object defined in the api module and bind it here
from .routes import api as api_mod  # noqa: E402
api_mod.limiter.init_app(app)

# CORS (optional)
if config.ENABLE_CORS:
    CORS(app, resources={r"/api/*": {"origins": "*"}})

# Template globals (e.g., footer year)
@app.context_processor
def inject_globals():
    # UTC is fine; this is purely cosmetic
    return {"current_year": datetime.utcnow().year}

# Blueprints
app.register_blueprint(api)
app.register_blueprint(pages)

# Health
@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/readyz")
def readyz():
    return {"ok": True}
