# netops/app.py
from __future__ import annotations
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
