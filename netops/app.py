# netops/app.py
from __future__ import annotations
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import HTTPException
from .config import config
from . import db
from .migrate import check_database
from .routes.api import api
from .routes.pages import pages

config.validate()
app = Flask(__name__, static_folder="../static", template_folder="../templates")
app.config.update(
    SECRET_KEY=config.SECRET_KEY, ENV=config.ENV, DEBUG=config.DEBUG,
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)

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

@app.teardown_appcontext
def close_session(_error=None):
    db.SessionLocal.remove()

@app.errorhandler(ValidationError)
def invalid_input(error):
    fields = [{"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
              for item in error.errors()]
    return jsonify({"error": "validation_error", "fields": fields}), 422

@app.errorhandler(HTTPException)
def http_error(error):
    if not request.path.startswith("/api/"):
        return error
    response = error.get_response()
    response.data = app.json.dumps({"error": error.name, "message": error.description})
    response.content_type = "application/json"
    return response

@app.errorhandler(IntegrityError)
def conflicting_data(_error):
    return jsonify({"error": "conflict", "message": "Data conflicts with an existing record"}), 409

@app.errorhandler(SQLAlchemyError)
def unavailable_database(error):
    app.logger.error("Database operation failed: %s", type(error).__name__)
    return jsonify({"error": "database_unavailable", "message": "Database is unavailable; retry later"}), 503

# Health
@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/readyz")
def readyz():
    try:
        with db.engine.connect() as connection:
            check_database(connection)
    except (SQLAlchemyError, RuntimeError) as error:
        app.logger.warning("Readiness failed: %s", type(error).__name__)
        return {"ok": False, "error": "database_unavailable"}, 503
    return {"ok": True}
