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
