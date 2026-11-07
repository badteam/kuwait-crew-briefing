"""Kuwait Airways crew briefing service (standard library only).

Flights come from AeroDataBox (scheduled departures from KWI) on a shared
server-side schedule, or from an admin CSV/XLSX import. Admins add the SCCM
name manually or by upload; displays are paired from the admin panel.
"""
import base64, csv, hashlib, hmac, io, json, os, re, secrets, sqlite3, threading, time, zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

import aerodatabox

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('DATA_DIR', str(ROOT / 'data'))); DATA.mkdir(exist_ok=True)
TZ = timezone(timedelta(hours=3))
ROOMS = ['A-1', 'B-2', 'C-3', 'D-4', 'E-5', 'F-6', 'H-8']
SCREENS = ['hall'] + ROOMS
ORIENTATIONS = ['auto', 'portrait', 'landscape']
LOCK = threading.RLock(); FAILURES = {}
DB = DATA / 'briefing.sqlite3'
SYNC_HOURS = float(os.environ.get('SYNC_INTERVAL_HOURS', '6'))
MONTHLY_UNITS = int(os.environ.get('MONTHLY_UNIT_BUDGET', '360'))   # plan: 400 units/month
UNITS_PER_CALL = 2
ADMIN_HOURS = 12
DISPLAY_DAYS = 365


def db():
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; return c


def migrate():
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS flights(id TEXT PRIMARY KEY,number TEXT,destination TEXT,aircraft TEXT,source TEXT,briefing TEXT,sccm TEXT,room TEXT,start TEXT,end TEXT,done INTEGER DEFAULT 0)')
        cols = {r['name'] for r in c.execute('PRAGMA table_info(flights)')}
        for name, ddl in [('departure', 'TEXT'), ('origin', "TEXT DEFAULT 'import'"), ('needs_type', 'INTEGER DEFAULT 0'),
                          ('aircraft_manual', 'INTEGER DEFAULT 0'), ('status', 'TEXT'), ('updated', 'TEXT'),
                          ('departure_manual', 'INTEGER DEFAULT 0')]:
            if name not in cols: c.execute(f'ALTER TABLE flights ADD COLUMN {name} {ddl}')
        c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS tokens(id TEXT PRIMARY KEY,hash TEXT UNIQUE,role TEXT,screen TEXT,orientation TEXT,label TEXT,created TEXT,expires REAL,last_seen REAL)')
        c.execute('CREATE TABLE IF NOT EXISTS pairings(code TEXT PRIMARY KEY,secret TEXT UNIQUE,screen TEXT,orientation TEXT,created REAL,token TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS crew_pending(number TEXT,date TEXT,sccm TEXT,PRIMARY KEY(number,date))')
        if 'std' not in {r['name'] for r in c.execute('PRAGMA table_info(crew_pending)')}: c.execute('ALTER TABLE crew_pending ADD COLUMN std TEXT')
migrate()


def now(): return datetime.now(TZ)
def stamp(t): return t.isoformat(timespec='seconds')
def digest(token): return hashlib.sha256(token.encode()).hexdigest()


def setting(c, key, default=None):
    row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def put_setting(c, key, value):
    c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))


WIDE = re.compile(r'(BOEING)?B?777\w*|(AIRBUS)?A?330\w*|B77[23LW]|A33[2389]')
SMALL = re.compile(r'SMALL|صغيرة|صغير|(AIRBUS)?A?32[01]\w*|A2[01]N')


def aircraft_offset(s):
    """Minutes added to the file/GMT time: 70 for wide-body (777/A330), 85 for A320/A321."""
    s = str(s or '').strip().upper().replace(' ', '').replace('-', '')
    if WIDE.fullmatch(s): return 70
    if SMALL.fullmatch(s): return 85
    raise ValueError('Unknown aircraft type: ' + (s or 'missing') + '; use SMALL, A320, A321, B777 or A330.')


def offset_or_none(aircraft):
    try: return aircraft_offset(aircraft)
    except ValueError: return None


def briefing_from_departure(departure_utc, aircraft):
    """Reporting time (Kuwait clock) = scheduled departure GMT clock + 85/70 minutes.

    Example: 14:00 GMT departure -> 15:25 for A320/A321, 15:10 for B777/A330.
    """
    minutes = offset_or_none(aircraft)
    if minutes is None: return None
    wall = datetime.fromisoformat(departure_utc).astimezone(timezone.utc).replace(tzinfo=TZ)
    return stamp(wall + timedelta(minutes=minutes))


def parse_time(value, date=''):
    s = str(value).strip()
    try:
        if re.fullmatch(r'\d{1,2}:\d{2}(:\d{2})?', s): s = str(date).strip() + 'T' + s
        t = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)
    except Exception: raise ValueError('Enter a YYYY-MM-DD date and HH:MM time, or a full date and time.')


def gmt_departure(value, date):
    """'10:00' (+ YYYY-MM-DD date) or a full date-time, read as GMT -> ISO UTC string."""
    s = str(value).strip()
    if re.fullmatch(r'0?\.\d+|0', s):   # Excel time cell
        seconds = round(float(s) * 86400); s = f'{seconds // 3600:02}:{seconds % 3600 // 60:02}'
    m = re.fullmatch(r'(\d{1,2}):?(\d{2})(?::\d{2})?Z?', s)
    try:
        if m:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(date)): raise ValueError
            t = datetime.fromisoformat(f'{date}T{int(m[1]):02}:{m[2]}:00+00:00')
        else:
            t = datetime.fromisoformat(s.replace('Z', '+00:00').replace(' ', 'T'))
            t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
    except ValueError:
        raise ValueError('Enter the departure as HH:MM GMT (with a date), e.g. 10:00.')
    return t.astimezone(timezone.utc).isoformat(timespec='seconds')


def set_departure(c, row, departure_utc):
    if row['start']: raise ValueError('This briefing has already started.')
    local = stamp(datetime.fromisoformat(departure_utc).astimezone(TZ))
    briefing = briefing_from_departure(departure_utc, row['aircraft'])
    c.execute('UPDATE flights SET departure=?,source=?,briefing=?,needs_type=?,departure_manual=1,done=0 WHERE id=?',
              (departure_utc, local, briefing, int(briefing is None), row['id']))


def norm(v): return re.sub(r'[\s_\-]+', '', str(v).strip().lower())


ALIASES = {
    'number': ['flight', 'flightnumber', 'flightno', 'رقمالرحلة', 'رقمالرحله'],
    'destination': ['destination', 'flightname', 'الوجهة', 'الوجهه', 'اسمالرحلة', 'اسمالرحله'],
    'aircraft': ['aircraft', 'aircrafttype', 'الطائرة', 'الطائره', 'نوعالطائرة'],
    'date': ['date', 'التاريخ'], 'time': ['time', 'filetime', 'الوقت', 'وقتالملف'],
    'sccm': ['sccm', 'chief', 'name', 'رئيسالطاقم'],
    'std': ['std', 'stdgmt', 'stdutc', 'departuregmt', 'departureutc', 'وقتالاقلاع', 'وقتالإقلاع']}


def read_rows(data, name):
    if name.lower().endswith('.csv'): return list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
    if not name.lower().endswith('.xlsx'): raise ValueError('Supported files: XLSX and CSV.')
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > 20_000_000: raise ValueError('The file is too large.')

        def xml(path):
            raw = z.read(path)
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw: raise ValueError('Unsupported XML format.')
            return ET.fromstring(raw)
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            shared = [''.join(n.itertext()) for n in xml('xl/sharedStrings.xml').findall('m:si', ns)]
        sheets = sorted((n for n in z.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+.xml', n)),
                        key=lambda n: int(re.search(r'\d+', n.rsplit('/', 1)[1]).group()))
        if not sheets: raise ValueError('No worksheet found.')
        rows = []
        for r in xml(sheets[0]).findall('.//m:sheetData/m:row', ns):
            row = []
            for cell in r.findall('m:c', ns):
                ref = re.match(r'[A-Z]+', cell.get('r', 'A')).group(); idx = 0
                for ch in ref: idx = idx * 26 + ord(ch) - 64
                if idx > 50: raise ValueError('The file has too many columns.')
                while len(row) < idx: row.append('')
                if cell.find('m:f', ns) is not None and cell.findtext('m:v', None, ns) is None:
                    raise ValueError('Convert Excel formulas to values before uploading.')
                v = cell.findtext('m:v', '', ns); typ = cell.get('t')
                row[idx - 1] = shared[int(v)] if typ == 's' else ''.join(cell.find('m:is', ns).itertext()) if typ == 'inlineStr' else v
            rows.append(row)
        return rows


def excel_date(dt):
    if re.fullmatch(r'\d+(\.\d+)?', dt): return (datetime(1899, 12, 30) + timedelta(days=float(dt))).date().isoformat()
    m = re.fullmatch(r'(\d{1,2})[/.](\d{1,2})[/.](\d{4})', dt)   # DD/MM/YYYY
    if m: return f'{m[3]}-{int(m[2]):02}-{int(m[1]):02}'
    return dt


def import_preview(payload):
    data = base64.b64decode(payload['data'], validate=True)
    if len(data) > 3_000_000: raise ValueError('Maximum file size is 3 MB.')
    rows = [r for r in read_rows(data, payload['name']) if any(str(v).strip() for v in r)]
    if len(rows) < 2 or len(rows) > 2001: raise ValueError('The file must contain 1 to 2000 data rows.')
    mapping = {}
    for i, v in enumerate(rows[0]):
        for key, aliases in ALIASES.items():
            if norm(v) in aliases and key not in mapping: mapping[key] = i
    crew = payload.get('kind') == 'crew'
    required = ['number', 'sccm'] if crew else ['number', 'destination', 'aircraft', 'time']
    if any(k not in mapping for k in required): raise ValueError('Required columns: ' + ', '.join(required))
    result = []; seen = set()
    for n, row in enumerate(rows[1:], 2):
        def get(k): return str(row[mapping[k]]).strip() if k in mapping and mapping[k] < len(row) else ''
        number = re.sub(r'\s+', '', get('number').upper())
        if re.fullmatch(r'\d{1,4}', number): number = 'KU' + number
        if not number or len(number) > 20: raise ValueError('Invalid flight number in row ' + str(n))
        item = {'number': number, 'sccm': get('sccm')[:120]}
        if crew:
            if not item['sccm']: raise ValueError('Missing SCCM name in row ' + str(n))
            item['date'] = excel_date(get('date'))
            if item['date'] and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['date']): raise ValueError('Use YYYY-MM-DD dates (row ' + str(n) + ').')
            item['std'] = ''
            if get('std'):
                if not item['date']: raise ValueError('Add the date when an STD is given (row ' + str(n) + ').')
                try: item['std'] = gmt_departure(get('std'), item['date'])
                except ValueError: raise ValueError('Invalid STD in row ' + str(n) + '; use HH:MM GMT.')
        else:
            dt = excel_date(get('date')); tm = get('time')
            if re.fullmatch(r'0?\.\d+|0', tm):
                seconds = round(float(tm) * 86400); tm = f'{seconds // 3600:02}:{seconds % 3600 // 60:02}:00'
            t = parse_time(tm, dt); offset = aircraft_offset(get('aircraft'))
            key = number + '|' + stamp(t)
            if key in seen: raise ValueError('Duplicate flight in row ' + str(n))
            seen.add(key)
            item.update(id=hashlib.sha256(key.encode()).hexdigest()[:24], destination=get('destination')[:120], aircraft=get('aircraft')[:24],
                        source=stamp(t), briefing=stamp(t + timedelta(minutes=offset)))
            if not item['destination']: raise ValueError('Missing destination in row ' + str(n))
        result.append(item)
    return result


def match_flight(c, number, date):
    """Open flights with this number; a date may be the Kuwait or GMT departure date."""
    return c.execute("SELECT id FROM flights WHERE number=? AND done=0 AND (?='' OR substr(source,1,10)=? OR substr(departure,1,10)=?)",
                     (number, date, date, date)).fetchall()


def import_rows(c, kind, rows):
    if kind == 'crew':
        matched = pending = 0; problems = []
        for r in rows:
            found = match_flight(c, r['number'], r['date'])
            if len(found) == 1:
                c.execute('UPDATE flights SET sccm=? WHERE id=?', (r['sccm'], found[0]['id'])); matched += 1
                if r.get('std'):
                    row = c.execute('SELECT * FROM flights WHERE id=?', (found[0]['id'],)).fetchone()
                    if not row['start']: set_departure(c, row, r['std'])
            elif len(found) > 1:
                problems.append(r['number'] + ' occurs on several dates; add the date')
            elif r['date']:
                c.execute('INSERT INTO crew_pending(number,date,sccm,std) VALUES(?,?,?,?) ON CONFLICT(number,date) DO UPDATE SET sccm=excluded.sccm,std=excluded.std',
                          (r['number'], r['date'], r['sccm'], r.get('std') or None)); pending += 1
            else:
                problems.append(r['number'] + ' not found yet; add the date so it is applied when the flight arrives')
        if problems: raise ValueError('; '.join(problems[:5]))
        return {'count': matched + pending, 'matched': matched, 'pending': pending}
    for r in rows:
        c.execute("INSERT INTO flights(id,number,destination,aircraft,source,briefing,sccm,origin) VALUES(?,?,?,?,?,?,?,'import') "
                  "ON CONFLICT(id) DO UPDATE SET destination=excluded.destination,aircraft=excluded.aircraft,"
                  "sccm=CASE WHEN excluded.sccm='' THEN flights.sccm ELSE excluded.sccm END",
                  (r['id'], r['number'], r['destination'], r['aircraft'], r['source'], r['briefing'], r['sccm']))
    return {'count': len(rows)}


# ---------------------------------------------------------------- live feed
def flight_id(number, departure_utc):
    local_date = datetime.fromisoformat(departure_utc).astimezone(TZ).date().isoformat()
    return 'ku-' + number + '-' + local_date


def store_departures(c, departures):
    """Upsert feed flights. SCCM, room and session times are never overwritten."""
    t = stamp(now()); added = updated = 0
    for f in departures:
        fid = flight_id(f['number'], f['departure_utc'])
        local = stamp(datetime.fromisoformat(f['departure_utc']).astimezone(TZ))
        row = c.execute('SELECT * FROM flights WHERE id=?', (fid,)).fetchone()
        cancelled = str(f.get('status') or '').lower().startswith('cancel')
        if row is None:
            aircraft = f.get('aircraft') or ''
            briefing = briefing_from_departure(f['departure_utc'], aircraft)
            c.execute("INSERT INTO flights(id,number,destination,aircraft,source,departure,briefing,sccm,origin,needs_type,status,updated,done) "
                      "VALUES(?,?,?,?,?,?,?,'','feed',?,?,?,?)",
                      (fid, f['number'], f.get('destination') or '', aircraft, local, f['departure_utc'], briefing,
                       int(briefing is None), f.get('status'), t, int(cancelled)))
            local_date, utc_date = local[:10], f['departure_utc'][:10]
            pending = c.execute('SELECT * FROM crew_pending WHERE number=? AND date IN (?,?)', (f['number'], local_date, utc_date)).fetchone()
            if pending:
                c.execute('UPDATE flights SET sccm=? WHERE id=?', (pending['sccm'], fid))
                if pending['std']: set_departure(c, c.execute('SELECT * FROM flights WHERE id=?', (fid,)).fetchone(), pending['std'])
                c.execute('DELETE FROM crew_pending WHERE number=? AND date=?', (pending['number'], pending['date']))
            added += 1
            continue
        aircraft = row['aircraft'] if row['aircraft_manual'] else (f.get('aircraft') or row['aircraft'] or '')
        if row['start'] is None:
            departure = row['departure'] if row['departure_manual'] else f['departure_utc']
            local = stamp(datetime.fromisoformat(departure).astimezone(TZ))
            briefing = briefing_from_departure(departure, aircraft)
            c.execute('UPDATE flights SET destination=?,aircraft=?,source=?,departure=?,briefing=?,needs_type=?,status=?,updated=?,done=? WHERE id=?',
                      (f.get('destination') or row['destination'], aircraft, local, departure, briefing,
                       int(briefing is None), f.get('status'), t, int(cancelled or row['done']), fid))
        else:   # session already running or finished: keep its room and times
            c.execute('UPDATE flights SET destination=?,status=?,updated=? WHERE id=?', (f.get('destination') or row['destination'], f.get('status'), t, fid))
        updated += 1
    return added, updated


def units_key(): return 'units:' + now().strftime('%Y-%m')


def run_sync(manual=False):
    """Fetch the next 12 hours of KU departures once; every screen shares the result."""
    with LOCK, db() as c:
        feed = setting(c, 'feed', {})
        used = setting(c, units_key(), 0)
        if used + UNITS_PER_CALL > MONTHLY_UNITS:
            raise ValueError(f'Monthly AeroDataBox budget reached ({used}/{MONTHLY_UNITS} units). Next refresh happens next month.')
        if manual and feed.get('last_attempt') and now() - datetime.fromisoformat(feed['last_attempt']) < timedelta(minutes=15):
            raise ValueError('Flights were refreshed less than 15 minutes ago. Please wait before refreshing again.')
        feed['last_attempt'] = stamp(now())
        put_setting(c, units_key(), used + UNITS_PER_CALL)
        put_setting(c, 'feed', feed)
    try:
        departures = aerodatabox.fetch_departures()
    except Exception as e:
        message = aerodatabox.describe_error(e)
        with LOCK, db() as c:
            feed = setting(c, 'feed', {}); feed.update(ok=False, error=message); put_setting(c, 'feed', feed)
        raise ValueError('Flight source unavailable: ' + message)
    with LOCK, db() as c:
        added, updated = store_departures(c, departures)
        feed = setting(c, 'feed', {})
        feed.update(ok=True, error='', last_success=stamp(now()), count=len(departures), added=added, updated=updated)
        put_setting(c, 'feed', feed)
    return feed


def sync_loop():
    while True:
        try:
            if aerodatabox.has_key():
                with db() as c: feed = setting(c, 'feed', {})
                last = feed.get('last_attempt')
                if not last or now() - datetime.fromisoformat(last) >= timedelta(hours=SYNC_HOURS):
                    try: run_sync()
                    except ValueError as e: print('sync:', e, flush=True)
        except Exception as e:
            print('sync loop error:', type(e).__name__, flush=True)
        time.sleep(60)


# ---------------------------------------------------------------- scheduling
def tick():
    with LOCK, db() as c:
        t = now()
        c.execute('UPDATE flights SET done=1 WHERE done=0 AND end IS NOT NULL AND end<=?', (stamp(t),))
        busy = {r['room'] for r in c.execute('SELECT room FROM flights WHERE done=0 AND start IS NOT NULL')}
        # Expired, never-started flights are not suddenly presented as current sessions.
        c.execute('UPDATE flights SET done=1 WHERE done=0 AND start IS NULL AND briefing<?', (stamp(t - timedelta(hours=2)),))
        c.execute("UPDATE flights SET done=1 WHERE done=0 AND start IS NULL AND briefing IS NULL AND departure IS NOT NULL AND departure<?",
                  (t.astimezone(timezone.utc).isoformat(timespec='seconds'),))
        queue = c.execute('SELECT id FROM flights WHERE done=0 AND start IS NULL AND briefing IS NOT NULL AND briefing<=? ORDER BY briefing,number',
                          (stamp(t),)).fetchall()
        free = [r for r in ROOMS if r not in busy]
        for room, row in zip(free, queue):
            c.execute('UPDATE flights SET room=?,start=?,end=? WHERE id=?', (room, stamp(t), stamp(t + timedelta(minutes=10)), row['id']))


PUBLIC_FIELDS = ['id', 'number', 'destination', 'aircraft', 'briefing', 'sccm', 'room', 'start', 'end', 'done']


def snapshot(auth):
    tick()
    recent = stamp(now() - timedelta(hours=1))
    with db() as c:
        rows = [dict(r) for r in c.execute(
            'SELECT * FROM flights WHERE done=0 OR end>? ORDER BY briefing IS NULL,briefing,departure,number', (recent,))]
        feed = setting(c, 'feed', {})
        state = {'now': stamp(now()), 'rooms': ROOMS, 'timezone': 'Asia/Kuwait',
                 'feed': {k: feed.get(k) for k in ('last_success', 'last_attempt', 'ok', 'error', 'count')}}
        if auth['role'] == 'admin':
            state['flights'] = rows
            state['feed'].update(units_used=setting(c, units_key(), 0), units_budget=MONTHLY_UNITS,
                                 interval_hours=SYNC_HOURS, key_configured=aerodatabox.has_key())
            state['displays'] = [dict(r) for r in c.execute(
                "SELECT id,screen,orientation,label,created,last_seen FROM tokens WHERE role='display' AND expires>? ORDER BY screen,label", (time.time(),))]
            state['pending_crew'] = c.execute('SELECT count(*) FROM crew_pending').fetchone()[0]
        else:
            state['flights'] = [{k: r[k] for k in PUBLIC_FIELDS} for r in rows if r['briefing']]
            state['display'] = {'screen': auth['screen'], 'orientation': auth['orientation'], 'label': auth['label']}
    return state


# ---------------------------------------------------------------- auth
def issue_token(c, role, screen=None, orientation=None, label=None):
    token = secrets.token_urlsafe(32)
    life = ADMIN_HOURS * 3600 if role == 'admin' else DISPLAY_DAYS * 86400
    c.execute('INSERT INTO tokens(id,hash,role,screen,orientation,label,created,expires,last_seen) VALUES(?,?,?,?,?,?,?,?,?)',
              (secrets.token_hex(6), digest(token), role, screen, orientation, label, stamp(now()), time.time() + life, time.time()))
    return token


def limited(ip, bucket, limit, window=300):
    key = (bucket, ip); attempts = [t for t in FAILURES.get(key, []) if t > time.time() - window]
    FAILURES[key] = attempts
    return len(attempts) >= limit


def note(ip, bucket): FAILURES.setdefault((bucket, ip), []).append(time.time())


def clean_choice(value, allowed, name):
    if value not in allowed: raise ValueError('Invalid ' + name + '.')
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = 'CrewBriefing'; sys_version = ''

    def log_message(self, *args): pass

    def ip(self): return self.headers.get('X-Real-IP') or self.client_address[0]

    def cors(self):
        origin = self.headers.get('Origin', ''); allowed = os.environ.get('FRONTEND_ORIGIN', 'https://badteam.github.io').split(',')
        if origin in allowed:
            self.send_header('Access-Control-Allow-Origin', origin); self.send_header('Vary', 'Origin')

    def send(self, status, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff'); self.cors()
        self.end_headers(); self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204); self.cors()
        self.send_header('Access-Control-Allow-Headers', 'Content-Type,Authorization'); self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Max-Age', '600'); self.end_headers()

    def auth(self):
        token = self.headers.get('Authorization', '').removeprefix('Bearer ').strip()
        if not token: return None
        with db() as c:
            row = c.execute('SELECT * FROM tokens WHERE hash=? AND expires>?', (digest(token), time.time())).fetchone()
            if row and time.time() - (row['last_seen'] or 0) > 60:
                c.execute('UPDATE tokens SET last_seen=? WHERE id=?', (time.time(), row['id']))
        return dict(row) if row else None

    def do_GET(self):
        if self.path == '/health': return self.send(200, {'status': 'ok'})
        if self.path == '/api/state':
            auth = self.auth()
            if not auth: return self.send(401, {'error': 'Please sign in first.'})
            return self.send(200, snapshot(auth))
        return self.send(404, {'error': 'Not found'})

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 1 or size > 4_100_000: return self.send(413, {'error': 'Invalid request size.'})
            p = json.loads(self.rfile.read(size))
            if not isinstance(p, dict): raise ValueError('Invalid request.')
            ip = self.ip()
            if self.path == '/api/login':
                if limited(ip, 'login', 10): return self.send(429, {'error': 'Too many attempts. Please wait five minutes.'})
                expected = os.environ.get('ADMIN_PASSWORD', '')
                if not expected or not hmac.compare_digest(str(p.get('password', '')), expected):
                    note(ip, 'login'); return self.send(401, {'error': 'Incorrect access code.'})
                with db() as c: token = issue_token(c, 'admin')
                return self.send(200, {'token': token, 'role': 'admin'})
            if self.path == '/api/pair/start':   # a display asks for a pairing code
                if limited(ip, 'pair', 30): return self.send(429, {'error': 'Too many pairing requests. Please wait.'})
                note(ip, 'pair')
                screen = p.get('screen') if p.get('screen') in SCREENS else 'hall'
                orientation = p.get('orientation') if p.get('orientation') in ORIENTATIONS else 'auto'
                with LOCK, db() as c:
                    c.execute('DELETE FROM pairings WHERE created<?', (time.time() - 900,))
                    code = None
                    while not code or c.execute('SELECT 1 FROM pairings WHERE code=?', (code,)).fetchone():
                        code = f'{secrets.randbelow(1_000_000):06d}'
                    secret = secrets.token_urlsafe(24)
                    c.execute('INSERT INTO pairings(code,secret,screen,orientation,created) VALUES(?,?,?,?,?)', (code, secret, screen, orientation, time.time()))
                return self.send(200, {'code': code, 'secret': secret, 'expires_in': 900})
            if self.path == '/api/pair/poll':
                with LOCK, db() as c:
                    row = c.execute('SELECT * FROM pairings WHERE secret=? AND created>?', (str(p.get('secret', '')), time.time() - 900)).fetchone()
                    if not row: return self.send(404, {'error': 'Pairing code expired.'})
                    if not row['token']: return self.send(200, {'paired': False})
                    c.execute('DELETE FROM pairings WHERE code=?', (row['code'],))
                return self.send(200, {'paired': True, 'token': row['token']})
            auth = self.auth()
            if not auth: return self.send(401, {'error': 'Please sign in first.'})
            if self.path == '/api/logout':
                with db() as c: c.execute('DELETE FROM tokens WHERE id=?', (auth['id'],))
                return self.send(200, {'ok': True})
            if auth['role'] != 'admin': return self.send(403, {'error': 'Administrator access required.'})
            return self.admin(p, ip)
        except (ValueError, KeyError, TypeError, zipfile.BadZipFile, ET.ParseError, json.JSONDecodeError) as e:
            self.send(400, {'error': str(e)[:300]})
        except Exception as e:
            print('error:', type(e).__name__, e, flush=True)
            self.send(500, {'error': 'Unable to complete the operation. Changes were not saved.'})

    def admin(self, p, ip):
        path = self.path
        if path in ('/api/preview', '/api/import'):
            rows = import_preview(p)
            if path == '/api/preview': return self.send(200, {'rows': rows})
            with LOCK, db() as c: result = import_rows(c, p.get('kind'), rows)
            return self.send(200, result)
        if path == '/api/sccm':
            name = re.sub(r'\s+', ' ', str(p.get('sccm', ''))).strip()[:120]
            with LOCK, db() as c:
                if not c.execute('UPDATE flights SET sccm=? WHERE id=?', (name, str(p.get('id')))).rowcount: raise ValueError('Flight not found.')
            return self.send(200, {'ok': True})
        if path == '/api/aircraft':
            aircraft = str(p.get('aircraft', '')).strip()[:24]; aircraft_offset(aircraft)
            with LOCK, db() as c:
                row = c.execute('SELECT * FROM flights WHERE id=?', (str(p.get('id')),)).fetchone()
                if not row or not row['departure']: raise ValueError('Flight not found.')
                if row['start']: raise ValueError('This briefing has already started.')
                c.execute('UPDATE flights SET aircraft=?,aircraft_manual=1,needs_type=0,briefing=? WHERE id=?',
                          (aircraft, briefing_from_departure(row['departure'], aircraft), row['id']))
            return self.send(200, {'ok': True})
        if path == '/api/departure':
            with LOCK, db() as c:
                row = c.execute('SELECT * FROM flights WHERE id=?', (str(p.get('id')),)).fetchone()
                if not row or not row['departure']: raise ValueError('Flight not found.')
                value = str(p.get('time', '')).strip()
                set_departure(c, row, gmt_departure(value, row['departure'][:10]))
            return self.send(200, {'ok': True})
        if path == '/api/sync':
            if not aerodatabox.has_key(): raise ValueError('The flight data key is not configured on the server.')
            return self.send(200, run_sync(manual=True))
        if path == '/api/extend':
            with LOCK, db() as c:
                row = c.execute('SELECT * FROM flights WHERE id=?', (p.get('id'),)).fetchone()
                if not row or row['done'] or not row['end'] or parse_time(row['end']) <= now(): raise ValueError('A finished session cannot be extended.')
                c.execute('UPDATE flights SET end=? WHERE id=?', (stamp(parse_time(row['end']) + timedelta(minutes=5)), row['id']))
            return self.send(200, {'ok': True})
        if path == '/api/pair/approve':
            code = re.sub(r'\D', '', str(p.get('code', '')))
            screen = clean_choice(p.get('screen'), SCREENS, 'screen'); orientation = clean_choice(p.get('orientation'), ORIENTATIONS, 'orientation')
            label = str(p.get('label') or ('Room ' + screen if screen != 'hall' else 'Main hall')).strip()[:60]
            if limited(ip, 'approve', 10): return self.send(429, {'error': 'Too many incorrect codes. Please wait five minutes.'})
            with LOCK, db() as c:
                row = c.execute('SELECT * FROM pairings WHERE code=? AND created>? AND token IS NULL', (code, time.time() - 900)).fetchone()
                if not row: note(ip, 'approve'); raise ValueError('Pairing code not found or expired. Reload the display to get a new code.')
                token = issue_token(c, 'display', screen, orientation, label)
                c.execute('UPDATE pairings SET token=? WHERE code=?', (token, code))
            return self.send(200, {'ok': True})
        if path == '/api/displays/update':
            screen = clean_choice(p.get('screen'), SCREENS, 'screen'); orientation = clean_choice(p.get('orientation'), ORIENTATIONS, 'orientation')
            with db() as c:
                if not c.execute("UPDATE tokens SET screen=?,orientation=?,label=COALESCE(?,label) WHERE id=? AND role='display'",
                                 (screen, orientation, (str(p['label']).strip()[:60] or None) if p.get('label') else None, str(p.get('id')))).rowcount:
                    raise ValueError('Display not found.')
            return self.send(200, {'ok': True})
        if path == '/api/displays/remove':
            with db() as c: c.execute("DELETE FROM tokens WHERE id=? AND role='display'", (str(p.get('id')),))
            return self.send(200, {'ok': True})
        return self.send(404, {'error': 'Not found'})


if __name__ == '__main__':
    threading.Thread(target=sync_loop, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()
