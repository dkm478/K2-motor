import os
import json
import sqlite3
import secrets
import hmac
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps
from pathlib import Path
from contextlib import contextmanager

from flask import Flask, request, jsonify, send_from_directory, abort

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 16384
DB = os.environ.get('DATABASE_PATH', str(ROOT / 'data' / 'garage.db'))
TZ = ZoneInfo('Europe/Paris')
SERVICES = json.loads((ROOT / 'services.json').read_text(encoding='utf-8'))
CATALOG = {s['id']: s for s in SERVICES}


class Store:
    def __init__(self, connection, postgres=False):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        sql = sql.replace('"confirmed"', "'confirmed'")
        sql = sql.replace('"cancelled"', "'cancelled'")
        sql = re.sub(r'\bend\b', '"end"', sql)
        if self.postgres:
            if sql == 'BEGIN IMMEDIATE':
                return self.connection.execute(
                    'SELECT pg_advisory_xact_lock(2026102901)'
                )
            sql = sql.replace('?', '%s')
        return self.connection.execute(sql, params)


@contextmanager
def db():
    url = os.environ.get('DATABASE_URL', '').strip()
    if os.environ.get('RENDER') == 'true' and not url:
        raise RuntimeError('DATABASE_URL doit être configurée dans Render.')

    if url:
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=15)
    else:
        Path(DB).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB, timeout=15)
        conn.row_factory = sqlite3.Row

    store = Store(conn, bool(url))
    try:
        if url:
            conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            conn.execute('SELECT pg_advisory_xact_lock(2026102900)')
        store.execute(
            'CREATE TABLE IF NOT EXISTS bookings ('
            'id TEXT PRIMARY KEY, token TEXT UNIQUE, start TEXT, end TEXT, '
            'name TEXT, email TEXT, phone TEXT, vehicle TEXT, services TEXT, '
            'price INTEGER, status TEXT DEFAULT "confirmed")'
        )
        conn.commit()
        if url:
            conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        yield store
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def admin(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        key = os.environ.get('ADMIN_TOKEN', '')
        supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if len(key) < 24 or not hmac.compare_digest(key, supplied):
            abort(401, 'Accès garage refusé.')
        return fn(*args, **kwargs)
    return wrapped


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, 'Formulaire invalide.')
    return data
