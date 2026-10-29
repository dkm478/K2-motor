"""K2 Motor - site et reservations (Flask, Neon PostgreSQL ou SQLite local)."""

import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

DB_PATH = os.environ.get("DATABASE_PATH", str(ROOT / "data" / "garage.db"))
PARIS = ZoneInfo("Europe/Paris")

with (ROOT / "services.json").open(encoding="utf-8") as f:
    SERVICES = json.load(f)
CATALOGUE = {s["id"]: s for s in SERVICES}


class Database:
    def __init__(self, connection, postgres):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.connection.execute(sql, params)

    def lock_bookings(self):
        if self.postgres:
            self.connection.execute("SELECT pg_advisory_xact_lock(2026102901)")
        else:
            self.connection.execute("BEGIN IMMEDIATE")


@contextmanager
def database():
    url = os.environ.get("DATABASE_URL", "").strip()
    if os.environ.get("RENDER", "").lower() == "true" and not url:
        raise RuntimeError("DATABASE_URL est absente de Render > Environment")

    if url:
        import psycopg
        from psycopg.rows import dict_row
        connection = psycopg.connect(url, row_factory=dict_row, connect_timeout=15)
    else:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(DB_PATH, timeout=15)
        connection.row_factory = sqlite3.Row

    db = Database(connection, postgres=bool(url))
    try:
        if url:
            connection.execute("SELECT pg_advisory_xact_lock(2026102900)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS bookings ("
            "id TEXT PRIMARY KEY, token TEXT UNIQUE, "
            'start TEXT, "end" TEXT, '
            "name TEXT, email TEXT, phone TEXT, vehicle TEXT, "
            "services TEXT, price INTEGER, "
            "status TEXT DEFAULT 'confirmed')"
        )
        connection.commit()
        yield db
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        secret = os.environ.get("ADMIN_TOKEN", "")
        header = request.headers.get("Authorization", "")
        given = header[7:] if header.startswith("Bearer ") else ""
        if len(secret) < 24 or not hmac.compare_digest(secret, given):
            abort(401, "Acces garage refuse.")
        return fn(*args, **kwargs)
    return wrapper


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Formulaire invalide.")
    return data


def chosen_services(ids):
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(i, str) or i not in CATALOGUE for i in ids)
        or len(ids) != len(set(ids))
    ):
        abort(400, "Selection de prestations invalide.")
    return [CATALOGUE[i] for i in ids]


def checked_slot(value, minutes):
    try:
        start = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=PARIS)
    except (ValueError, TypeError):
        abort(400, "Date invalide.")

    end = start + timedelta(minutes=minutes)
    now = datetime.now(PARIS)
    if (
        start <= now + timedelta(hours=1)
        or start > now + timedelta(days=90)
        or start.weekday() == 6
        or start.minute not in (0, 30)
        or start.hour < 9
        or end.date() != start.date()
        or end.hour > 18
        or (end.hour == 18 and end.minute > 0)
    ):
        abort(400, "Creneau indisponible (lundi-samedi, 9h-18h).")

    return start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")


def occupied(db, start, end):
    return db.execute(
        "SELECT 1 FROM bookings WHERE status='confirmed' "
        'AND start < ? AND "end" > ?',
        (end, start),
    ).fetchone() is not None


@app.after_request
def secure_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    if request.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
def http_error(error):
    return jsonify(error=error.description), error.code


# Pages et fichiers du site : tous a la racine du depot GitHub.
@app.get("/")
def home():
    return send_from_directory(ROOT, "index.html")


@app.get("/styles.css")
def stylesheet():
    return send_from_directory(ROOT, "styles.css")


@app.get("/app.js")
def javascript():
    return send_from_directory(ROOT, "app.js")


@app.get("/assets/garage-hero.jpg")
@app.get("/garage-hero.jpg")
def hero_image():
    return send_from_directory(ROOT, "garage-hero.jpg")


# Controle Render : ne depend pas d'une connexion a Neon.
@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/api/services")
def services():
    return jsonify(SERVICES)


@app.get("/api/slots")
def available_slots():
    selected = chosen_services(request.args.get("services", "").split(","))
    duration = sum(s["minutes"] for s in selected)
    day = request.args.get("date", "")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        abort(400, "Date invalide.")

    result = []
    with database() as db:
        for minute in range(9 * 60, 18 * 60, 30):
            candidate = f"{day}T{minute // 60:02d}:{minute % 60:02d}"
            try:
                start, end = checked_slot(candidate, duration)
            except Exception as error:
                if getattr(error, "code", None) == 400:
                    continue
                raise
            if not occupied(db, start, end):
                result.append(candidate)
    return jsonify(result)


@app.post("/api/bookings")
def create_booking():
    data = json_body()
    selected = chosen_services(data.get("services"))
    for field in ("name", "email", "phone", "vehicle"):
        value = data.get(field)
        if not isinstance(value, str) or not 2 <= len(value.strip()) <= 150:
            abort(400, "Coordonnees incompletes ou trop longues.")
        data[field] = value.strip()

    if (
        not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", data["email"])
        or not re.fullmatch(r"[+\d ().-]{8,25}", data["phone"])
    ):
        abort(400, "Email ou telephone invalide.")

    start, end = checked_slot(
        data.get("start"), sum(s["minutes"] for s in selected)
    )
    ident = secrets.token_hex(5).upper()
    token = secrets.token_urlsafe(32)
    with database() as db:
        db.lock_bookings()
        if occupied(db, start, end):
            abort(409, "Ce creneau vient d'etre reserve. Choisissez-en un autre.")
        db.execute(
            "INSERT INTO bookings "
            '(id, token, start, "end", name, email, phone, vehicle, services, price) '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ident, token, start, end, data["name"], data["email"],
                data["phone"], data["vehicle"],
                json.dumps([s["id"] for s in selected]),
                sum(s["price"] for s in selected),
            ),
        )
    return jsonify(id=ident, token=token), 201


@app.post("/api/lookup")
def lookup_booking():
    token = json_body().get("token", "")
    if not isinstance(token, str):
        abort(400, "Code invalide.")
    with database() as db:
        row = db.execute(
            "SELECT * FROM bookings WHERE token=?", (token,)
        ).fetchone()
    if row is None:
        abort(404, "Reservation introuvable.")
    result = dict(row)
    result.pop("token", None)
    return jsonify(result)


@app.post("/api/cancel")
def cancel_booking():
    token = json_body().get("token", "")
    if not isinstance(token, str):
        abort(400, "Code invalide.")
    with database() as db:
        row = db.execute(
            "SELECT * FROM bookings WHERE token=?", (token,)
        ).fetchone()
        if row is None:
            abort(404, "Reservation introuvable.")
        if row["start"] <= datetime.now(PARIS).strftime("%Y-%m-%dT%H:%M"):
            abort(400, "Ce rendez-vous est deja passe.")
        db.execute(
            "UPDATE bookings SET status='cancelled' WHERE token=?",
            (token,),
        )
    return jsonify(ok=True)


@app.get("/api/admin/bookings")
@require_admin
def admin_bookings():
    with database() as db:
        rows = db.execute(
            'SELECT id, start, "end", name, email, phone, vehicle, '
            "services, price, status FROM bookings ORDER BY start DESC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/admin/status")
@require_admin
def admin_status():
    data = json_body()
    if (
        data.get("status") not in ("cancelled", "completed")
        or not isinstance(data.get("id"), str)
    ):
        abort(400, "Demande invalide.")
    with database() as db:
        updated = db.execute(
            "UPDATE bookings SET status=? WHERE id=?",
            (data["status"], data["id"]),
        )
        if updated.rowcount == 0:
            abort(404, "Rendez-vous introuvable.")
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
