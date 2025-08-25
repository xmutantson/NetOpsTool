# netops/routes/pages.py
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, session
from ..config import config

pages = Blueprint("pages", __name__)

@pages.get("/")
def index():
    return render_template("index.html")

@pages.get("/stations")
def stations():
    return render_template("stations.html")

@pages.get("/stations/<name>")
def station_detail(name: str):
    return render_template("station_detail.html", name=name)

@pages.get("/login")
def ui_login_form():
    # Optional simple UI gate
    return render_template("ui_login.html")

@pages.post("/login")
def ui_login():
    pw = request.form.get("password","")
    if config.ADMIN_PASSWORD and pw == config.ADMIN_PASSWORD:
        session["ui_admin"] = True
        return redirect(url_for("pages.index"))
    return redirect(url_for("pages.ui_login_form"))
