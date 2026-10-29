import os, json, sqlite3, secrets, hmac, re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, abort

ROOT = Path(__file__).parent
app = Flask(__name__, static_folder='public', static_url_path='')
app.config['MAX_CONTENT_LENGTH'] = 16384
DB = os.environ.get('DATABASE_PATH', str(ROOT / 'data' / 'garage.db'))
TZ = ZoneInfo('Europe/Paris')
SERVICES = json.loads((ROOT / 'services.json').read_text())
CATALOG = {s['id']: s for s in SERVICES}

# SQLite is retained only for local development. Render requires Neon.
from contextlib import contextmanager

class Store:
    def __init__(self, connection, postgres=False):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        # Keep the public API shape while using portable SQL literals.
        sql = sql.replace('"confirmed"', "'confirmed'").replace('"cancelled"', "'cancelled'")
        sql = re.sub(r'\bend\b', '"end"', sql)
        if self.postgres:
            if sql == 'BEGIN IMMEDIATE':
                # Transaction-scoped lock works with Neon transaction pooling.
                # The next SELECT obtains a fresh READ COMMITTED snapshot.
                return self.connection.execute('SELECT pg_advisory_xact_lock(2026102901)')
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
        store.execute('CREATE TABLE IF NOT EXISTS bookings (id TEXT PRIMARY KEY, token TEXT UNIQUE, start TEXT, end TEXT, name TEXT, email TEXT, phone TEXT, vehicle TEXT, services TEXT, price INTEGER, status TEXT DEFAULT "confirmed")')
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
    if not isinstance(data, dict): abort(400, 'Formulaire invalide.')
    return data

def selected(ids):
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in CATALOG for i in ids) or len(set(ids)) != len(ids):
        abort(400, 'Sélection de prestations invalide.')
    return [CATALOG[i] for i in ids]

def slot(start, duration):
    try:
        dt = datetime.strptime(start, '%Y-%m-%dT%H:%M').replace(tzinfo=TZ)
    except (ValueError, TypeError):
        abort(400, 'Date invalide.')
    end = dt + timedelta(minutes=duration)
    now = datetime.now(TZ)
    if dt <= now + timedelta(hours=1) or dt > now + timedelta(days=90) or dt.weekday() == 6 or dt.minute not in (0,30) or dt.hour < 9 or end.date() != dt.date() or end.hour > 18 or (end.hour == 18 and end.minute > 0):
        abort(400, 'Créneau hors horaires ou trop proche. Réservation entre 1 heure et 90 jours à l’avance.')
    return dt.strftime('%Y-%m-%dT%H:%M'), end.strftime('%Y-%m-%dT%H:%M')

def occupied(conn, start, end):
    return conn.execute('SELECT 1 FROM bookings WHERE status="confirmed" AND start < ? AND end > ?', (end,start)).fetchone() is not None

@app.after_request
def headers(response):
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.path.startswith('/api'): response.headers['Cache-Control']='no-store'
    return response

@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
def error(err):
    return jsonify(error=str(err.description)), err.code

@app.get('/')
def index(): return send_from_directory(app.static_folder, 'index.html')

@app.get('/api/services')
def services(): return jsonify(SERVICES)

@app.get('/api/slots')
def slots():
    items=selected(request.args.get('services','').split(','))
    duration=sum(s['minutes'] for s in items)
    day=request.args.get('date','')
    result=[]
    with db() as conn:
        for minute in range(9*60,18*60,30):
            start=f'{day}T{minute//60:02d}:{minute%60:02d}'
            try: a,b=slot(start,duration)
            except Exception as e:
                if getattr(e,'code',None)==400: continue
                raise
            if not occupied(conn,a,b): result.append(start)
    return jsonify(result)

@app.post('/api/bookings')
def book():
    data=request.get_json(silent=True)
    if not isinstance(data,dict): abort(400,'Formulaire invalide.')
    items=selected(data.get('services'))
    for field in ('name','email','phone','vehicle'):
        if not isinstance(data.get(field),str) or not 2 <= len(data[field].strip()) <= 150: abort(400,'Coordonnées incomplètes ou trop longues.')
        data[field]=data[field].strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',data['email']) or not re.fullmatch(r'[+\d ().-]{8,25}',data['phone']): abort(400,'Email ou téléphone invalide.')
    a,b=slot(data.get('start'),sum(s['minutes'] for s in items))
    ident=secrets.token_hex(5).upper(); token=secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if occupied(conn,a,b): abort(409,'Ce créneau vient d’être réservé. Choisissez-en un autre.')
        conn.execute('INSERT INTO bookings (id,token,start,end,name,email,phone,vehicle,services,price) VALUES (?,?,?,?,?,?,?,?,?,?)',(ident,token,a,b,data['name'],data['email'],data['phone'],data['vehicle'],json.dumps([s['id'] for s in items]),sum(s['price'] for s in items)))
    return jsonify(id=ident,token=token),201

@app.post('/api/lookup')
def lookup():
    token=body().get('token','')
    if not isinstance(token,str): abort(400)
    with db() as conn: row=conn.execute('SELECT * FROM bookings WHERE token=?',(token,)).fetchone()
    if row is None: abort(404,'Code de réservation introuvable.')
    result=dict(row); result.pop('token'); return jsonify(result)

@app.post('/api/cancel')
def cancel():
    data=body()
    token=data.get('token','')
    if not isinstance(token,str): abort(400)
    with db() as conn:
        row=conn.execute('SELECT * FROM bookings WHERE token=?',(token,)).fetchone()
        if row is None: abort(404,'Réservation introuvable.')
        if row['start'] <= datetime.now(TZ).strftime('%Y-%m-%dT%H:%M'): abort(400,'Ce rendez-vous est déjà passé.')
        conn.execute('UPDATE bookings SET status="cancelled" WHERE token=?',(token,))
    return jsonify(ok=True)

@app.get('/api/admin/bookings')
@admin
def bookings():
    with db() as conn:
        rows=conn.execute('SELECT id,start,end,name,email,phone,vehicle,services,price,status FROM bookings ORDER BY start DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.post('/api/admin/status')
@admin
def status():
    data=body()
    if data.get('status') not in ('cancelled','completed') or not isinstance(data.get('id'),str): abort(400)
    with db() as conn:
        cur=conn.execute('UPDATE bookings SET status=? WHERE id=?',(data['status'],data.get('id')))
        if not cur.rowcount: abort(404)
    return jsonify(ok=True)

@app.get('/health')
def health():
    with db() as conn: conn.execute('SELECT 1')
    return jsonify(ok=True)

if __name__=='__main__': app.run(host='127.0.0.1',port=int(os.environ.get('PORT','8000')))
