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
import flightaware

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
FA_MONTHLY_PAGES = int(os.environ.get('FLIGHTAWARE_MONTHLY_PAGES', '500'))   # ~$2.50 at $0.005/page
PROVIDERS = {'aerodatabox': aerodatabox, 'flightaware': flightaware}
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
                          ('departure_manual', 'INTEGER DEFAULT 0'), ('briefing_manual', 'INTEGER DEFAULT 0'), ('crew', 'TEXT'), ('label', 'TEXT')]:
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
    briefing = row['briefing'] if row['briefing_manual'] else briefing_from_departure(departure_utc, row['aircraft'])
    c.execute('UPDATE flights SET departure=?,source=?,briefing=?,needs_type=?,departure_manual=1,done=0 WHERE id=?',
              (departure_utc, local, briefing, int(briefing is None), row['id']))


def norm(v): return re.sub(r'[\s_\-]+', '', str(v).strip().lower())


ROLE_WORDS = {'SCCM': 'SCCM', 'CCI': 'CCI', 'FSS': 'FSS', 'INS': 'INS', 'SUPERVISOR': 'FSS', 'INSTRUCTOR': 'INS', 'CHIEF': 'SCCM', 'PURSER': 'SCCM'}
ROLE_RE = re.compile(r'\b(SCCM|CCI|FSS|SUPERVISOR|INSTRUCTOR)\b', re.I)


def parse_crew(text, role='SCCM'):
    """'Ahmed Ali / Sara (FSS)' -> [{'role': 'SCCM', 'name': 'Ahmed Ali'}, {'role': 'FSS', 'name': 'Sara'}]."""
    people = []
    for part in re.split(r'\s*(?:/|\n|;|&|\+|،|,|\|)\s*', str(text or '')):
        r = role; m = ROLE_RE.search(part)
        if m:
            r = ROLE_WORDS[m[1].upper()]
            part = (part[:m.start()] + ' ' + part[m.end():])
        name = re.sub(r'\s+', ' ', re.sub(r'^[\s:\-–()]+|[\s:\-–()]+$', '', part)).strip()[:80]
        if name: people.append({'role': r, 'name': name})
    return people[:6]


def crew_text(people):
    return ' / '.join(p['name'] + ('' if p['role'] == 'SCCM' else ' (' + p['role'] + ')') for p in people)


def crew_of(row):
    try:
        people = json.loads(row['crew']) if row['crew'] else None
    except (ValueError, TypeError):
        people = None
    return people if people is not None else parse_crew(row['sccm'])


def set_crew(c, flight, text_or_people):
    people = text_or_people if isinstance(text_or_people, list) else parse_crew(text_or_people)
    c.execute('UPDATE flights SET sccm=?,crew=? WHERE id=?', (crew_text(people), json.dumps(people, ensure_ascii=False), flight))


def roster_dates(c): return set(setting(c, 'roster_dates', []))


def kuwait_date(value): return datetime.fromisoformat(value).astimezone(TZ).date().isoformat()


def parse_reporting(value, date):
    """Reporting time from the roster (Kuwait clock, used as is). Returns (iso, has_date)."""
    s = str(value).strip()
    if re.fullmatch(r'\d{4,6}\.\d+', s):          # Excel date+time serial
        t = datetime(1899, 12, 30) + timedelta(minutes=round(float(s) * 1440))
        return stamp(t.replace(tzinfo=TZ)), True
    if re.fullmatch(r'0?\.\d+|0', s):              # Excel time only
        seconds = round(float(s) * 86400); s = f'{seconds // 3600 % 24:02}:{seconds % 3600 // 60:02}'
    if re.fullmatch(r'\d{3,4}', s): s = s.zfill(4)[:2] + ':' + s.zfill(4)[2:]
    m = re.fullmatch(r'(\d{1,2})[/.](\d{1,2})[/.](\d{4})[ T]+(\d{1,2}:\d{2})(?::\d{2})?', s)
    if m: return stamp(datetime.fromisoformat(f'{m[3]}-{int(m[2]):02}-{int(m[1]):02}T{int(m[4][:-3]):02}:{m[4][-2:]}').replace(tzinfo=TZ)), True
    m = re.fullmatch(r'(\d{1,2}):(\d{2})(?::\d{2})?', s)
    if m:
        if date: return stamp(datetime.fromisoformat(f'{date}T{int(m[1]):02}:{m[2]}').replace(tzinfo=TZ)), True
        t = now().replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
        if t < now() - timedelta(hours=3): t += timedelta(days=1)   # no date: the next occurrence
        return stamp(t), False
    try:
        t = datetime.fromisoformat(s.replace(' ', 'T'))
        return stamp(t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)), True
    except ValueError:
        raise ValueError('Use HH:MM for the reporting time.')


ALIASES = {
    'number': ['flight', 'flightnumber', 'flightno', 'flt', 'fltno', 'رقمالرحلة', 'رقمالرحله'],
    'destination': ['destination', 'flightname', 'الوجهة', 'الوجهه', 'اسمالرحلة', 'اسمالرحله'],
    'aircraft': ['aircraft', 'aircrafttype', 'الطائرة', 'الطائره', 'نوعالطائرة'],
    'date': ['date', 'التاريخ'], 'time': ['time', 'filetime', 'الوقت', 'وقتالملف'],
    'sccm': ['sccm', 'chief', 'name', 'رئيسالطاقم'],
    'std': ['std', 'stdgmt', 'stdutc', 'departuregmt', 'departureutc', 'وقتالاقلاع', 'وقتالإقلاع'],
    'reporting': ['reporting', 'reportingtime', 'report', 'reporttime', 'rpt', 'rept', 'reptkuwait', 'reportingkuwait', 'briefing', 'briefingtime', 'وقتالريبورتينج', 'الريبورتينج', 'وقتالحضور'],
    'rank': ['rank', 'role', 'position', 'title', 'الرتبة', 'المسمى'],
    'reg': ['reg', 'registration', 'tail', 'التسجيل']}


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
                if typ == 's': v = shared[int(v)] if v.isdigit() and int(v) < len(shared) else ''
                elif typ == 'inlineStr':
                    node = cell.find('m:is', ns); v = ''.join(node.itertext()) if node is not None else v
                row[idx - 1] = v
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
    def header_map(row):
        found = {}
        for i, v in enumerate(row):
            h = re.sub(r'\(.*?\)', '', str(v))   # 'REPT (Kuwait)' -> 'REPT'
            for key, aliases in ALIASES.items():
                if (norm(v) in aliases or norm(h) in aliases) and key not in found: found[key] = i
        return found
    head = 0
    for i, row in enumerate(rows[:10]):       # title rows may sit above the column headers
        m = header_map(row)
        if 'number' in m and ('reporting' in m or 'sccm' in m or 'time' in m): head = i; break
    titles = ' '.join(str(v) for row in rows[:head] for v in row)
    rows = rows[head:]
    mapping = header_map(rows[0])
    if payload.get('kind') == 'roster': return roster_preview(rows, mapping, titles + ' ' + str(payload.get('name', '')))
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


MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


def date_from_text(text):
    """'Crew Reporting — 09 Nov 2026' or 'Crew_09Nov2026.xlsx' -> '2026-11-09'."""
    m = re.search(r'(\d{1,2})[\s\-_]*([A-Za-z]{3})[a-z]*[\s\-_]*(\d{4})', str(text))
    if m and m[2].lower() in MONTHS: return f'{m[3]}-{MONTHS[m[2].lower()]:02}-{int(m[1]):02}'
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', str(text))
    return m[0] if m else ''


def clean_name(name):
    return re.sub(r'\s+', ' ', re.sub(r'^[\s.,\-]+', '', str(name))).strip()[:80]


def roster_preview(rows, mapping, titles=''):
    if 'number' not in mapping or 'reporting' not in mapping: raise ValueError('Required columns: flight (FLT), reporting time (REPT)')
    used = {mapping.get(k) for k in ('number', 'reporting', 'date', 'destination', 'rank', 'reg')}
    name_cols = []
    for i, header in enumerate(rows[0]):
        h = norm(header)
        if i in used or 'id' in h or 'staff' in h or 'reg' == h: continue
        if 'fss' in h or 'supervisor' in h or 'سوبر' in h: name_cols.append((i, 'FSS'))
        elif 'cci' in h: name_cols.append((i, 'CCI'))
        elif any(k in h for k in ('sccm', 'chief', 'cabin', 'crew', 'name', 'رئيس', 'شيف', 'اسم')): name_cols.append((i, 'SCCM'))
    default_date = date_from_text(titles)
    groups = {}; order = []; latest = None
    for n, row in enumerate(rows[1:], 2):
        def cell(i): return str(row[i]).strip() if i is not None and i < len(row) else ''
        raw = cell(mapping['number'])
        if not raw: continue
        number = re.sub(r'\s+', '', raw.upper())
        if re.fullmatch(r'\d{1,4}', number): number = 'KU' + number
        if not re.fullmatch(r'[A-Z0-9]{2,3}\d{1,4}[A-Z]?', number): raise ValueError('Invalid flight number in row ' + str(n))
        date = excel_date(cell(mapping.get('date')))
        if date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date): raise ValueError('Use YYYY-MM-DD dates (row ' + str(n) + ').')
        try: reporting, dated = parse_reporting(cell(mapping['reporting']), date or default_date)
        except ValueError: raise ValueError('Invalid reporting time in row ' + str(n) + '; use HH:MM.')
        if not date and default_date:   # a list sorted by time: times after midnight belong to the next day
            t = datetime.fromisoformat(reporting)
            if latest and t < latest - timedelta(hours=6): t += timedelta(days=1); reporting = stamp(t)
            latest = max(latest or t, t)
        rank = cell(mapping.get('rank')).upper()
        role = ROLE_WORDS.get(rank, rank[:6]) if rank else None
        people = []
        for i, col_role in name_cols:
            people += [dict(p, name=clean_name(p['name'])) for p in parse_crew(cell(i), role or col_role)]
        reg = re.sub(r'\s+', '', cell(mapping.get('reg')).upper())
        key = (reg or number, reporting)   # same aircraft and time = one crew briefing (e.g. KU619 / KU620)
        if key not in groups:
            groups[key] = {'number': number, 'numbers': [], 'date': reporting[:10], 'reporting': reporting, 'dated': dated or bool(default_date),
                           'destination': cell(mapping.get('destination'))[:120], 'crew': []}
            order.append(key)
        if number not in groups[key]['numbers']: groups[key]['numbers'].append(number)
        for p in people:
            if p['name'] and p not in groups[key]['crew']: groups[key]['crew'].append(p)
    if not order: raise ValueError('No flights found in the file.')
    result = []
    for key in order:
        g = groups[key]; g['crew'] = g['crew'][:6]; g['crew_text'] = crew_text(g['crew'])
        g['label'] = g['numbers'][0] + ''.join(' / ' + re.sub(r'^KU', '', n) for n in g['numbers'][1:])
        result.append(g)
    return result


def import_roster(c, rows, full_list):
    """Reporting times from the roster are used as is and are never recalculated."""
    touched = set(); matched = added = 0
    for r in rows:
        rep_ = datetime.fromisoformat(r['reporting'])
        lo, hi = stamp(rep_ - timedelta(hours=6)), stamp(rep_ + timedelta(hours=6))
        utc_lo = rep_.astimezone(timezone.utc).isoformat(timespec='seconds'); utc_hi = (rep_ + timedelta(hours=6)).astimezone(timezone.utc).isoformat(timespec='seconds')
        candidates = c.execute("SELECT * FROM flights WHERE number=? AND ((briefing>=? AND briefing<=?) OR (briefing IS NULL AND departure>=? AND departure<=?)) "
                               "ORDER BY start IS NOT NULL, done", (r['number'], lo, hi, utc_lo, utc_hi)).fetchall()
        row = next((x for x in candidates if x['id'] not in touched), None)
        if not r['destination']:
            known = c.execute("SELECT destination FROM flights WHERE number=? AND destination<>'' ORDER BY updated DESC LIMIT 1", (r['number'],)).fetchone()
            if known: r = dict(r, destination=known['destination'])
        if row:
            fid = row['id']; matched += 1
            if not row['start']:
                c.execute("UPDATE flights SET briefing=?,briefing_manual=1,needs_type=0,done=0,status='Roster',destination=CASE WHEN ?<>'' THEN ? ELSE destination END WHERE id=?",
                          (r['reporting'], r['destination'], r['destination'], fid))
        else:
            fid = 'ku-' + r['number'] + '-' + r['date']
            if fid in touched: fid += '-' + r['reporting'][11:16].replace(':', '')
            added += 1
            c.execute("INSERT INTO flights(id,number,destination,aircraft,source,briefing,briefing_manual,sccm,origin,status,done) VALUES(?,?,?,'',?,?,1,'','roster','Roster',0) "
                      "ON CONFLICT(id) DO UPDATE SET briefing=excluded.briefing,briefing_manual=1,needs_type=0,done=0,status='Roster',"
                      "destination=CASE WHEN excluded.destination<>'' THEN excluded.destination ELSE flights.destination END",
                      (fid, r['number'], r['destination'], r['reporting'], r['reporting']))
        if r['crew']: set_crew(c, fid, r['crew'])
        c.execute('UPDATE flights SET label=? WHERE id=?', (r.get('label') if r.get('label', r['number']) != r['number'] else None, fid))
        touched.add(fid)
    closed = 0
    if full_list:
        dates = {r['date'] for r in rows}
        for row in c.execute('SELECT id,briefing,departure FROM flights WHERE done=0 AND start IS NULL').fetchall():
            day = row['briefing'][:10] if row['briefing'] else (kuwait_date(row['departure']) if row['departure'] else None)
            if day in dates and row['id'] not in touched:
                c.execute("UPDATE flights SET done=1,status='Not in roster' WHERE id=?", (row['id'],)); closed += 1
        keep = sorted((roster_dates(c) | dates))[-14:]
        put_setting(c, 'roster_dates', keep)
    return {'count': len(rows), 'matched': matched, 'added': added, 'closed': closed}


def match_flight(c, number, date):
    """Open flights with this number; a date may be the Kuwait or GMT departure date."""
    return c.execute("SELECT id FROM flights WHERE number=? AND done=0 AND (?='' OR substr(source,1,10)=? OR substr(departure,1,10)=?)",
                     (number, date, date, date)).fetchall()


def import_rows(c, kind, rows, full_list=False):
    if kind == 'roster': return import_roster(c, rows, full_list)
    if kind == 'crew':
        matched = pending = 0; problems = []
        for r in rows:
            found = match_flight(c, r['number'], r['date'])
            if len(found) == 1:
                set_crew(c, found[0]['id'], r['sccm']); matched += 1
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


def store_departures(c, departures, window=None):
    """Upsert feed flights. SCCM, room and session times are never overwritten.

    With a window (start, end) in UTC, feed flights in that window that the source no longer
    lists (and that have not started or been edited) are closed as 'Not in schedule'.
    """
    t = stamp(now()); added = updated = 0; seen = set(); rdates = roster_dates(c)
    for f in departures:
        fid = flight_id(f['number'], f['departure_utc'])
        local = stamp(datetime.fromisoformat(f['departure_utc']).astimezone(TZ))
        dep = datetime.fromisoformat(f['departure_utc'])
        linked = c.execute('SELECT id FROM flights WHERE number=? AND briefing_manual=1 AND briefing>=? AND briefing<=?',
                           (f['number'], stamp(dep - timedelta(hours=6)), stamp(dep))).fetchone()
        if linked: fid = linked['id']
        seen.add(fid)
        row = c.execute('SELECT * FROM flights WHERE id=?', (fid,)).fetchone()
        cancelled = str(f.get('status') or '').lower().startswith('cancel')
        if row is None:
            aircraft = f.get('aircraft') or ''
            briefing = briefing_from_departure(f['departure_utc'], aircraft)
            if local[:10] in rdates or (briefing and briefing[:10] in rdates):
                cancelled = True; f = dict(f, status='Not in roster')
            c.execute("INSERT INTO flights(id,number,destination,aircraft,source,departure,briefing,sccm,origin,needs_type,status,updated,done) "
                      "VALUES(?,?,?,?,?,?,?,'','feed',?,?,?,?)",
                      (fid, f['number'], f.get('destination') or '', aircraft, local, f['departure_utc'], briefing,
                       int(briefing is None), f.get('status'), t, int(cancelled)))
            local_date, utc_date = local[:10], f['departure_utc'][:10]
            pending = c.execute('SELECT * FROM crew_pending WHERE number=? AND date IN (?,?)', (f['number'], local_date, utc_date)).fetchone()
            if pending:
                set_crew(c, fid, pending['sccm'])
                if pending['std']: set_departure(c, c.execute('SELECT * FROM flights WHERE id=?', (fid,)).fetchone(), pending['std'])
                c.execute('DELETE FROM crew_pending WHERE number=? AND date=?', (pending['number'], pending['date']))
            added += 1
            continue
        aircraft = row['aircraft'] if row['aircraft_manual'] else (f.get('aircraft') or row['aircraft'] or '')
        if row['briefing_manual']:   # roster flight: reporting time and status come from the roster
            c.execute('UPDATE flights SET destination=?,aircraft=?,departure=?,updated=? WHERE id=?',
                      (row['destination'] or f.get('destination') or '', aircraft, row['departure'] if row['departure_manual'] else f['departure_utc'], t, fid))
            updated += 1; continue
        if row['status'] == 'Not in roster': cancelled = True; f = dict(f, status='Not in roster')
        if row['start'] is None:
            departure = row['departure'] if row['departure_manual'] else f['departure_utc']
            local = stamp(datetime.fromisoformat(departure).astimezone(TZ))
            briefing = briefing_from_departure(departure, aircraft)
            c.execute('UPDATE flights SET destination=?,aircraft=?,source=?,departure=?,briefing=?,needs_type=?,status=?,updated=?,done=? WHERE id=?',
                      (f.get('destination') or row['destination'], aircraft, local, departure, briefing,
                       int(briefing is None), f.get('status'), t, int(cancelled), fid))
        else:   # session already running or finished: keep its room and times
            c.execute('UPDATE flights SET destination=?,status=?,updated=? WHERE id=?', (f.get('destination') or row['destination'], f.get('status'), t, fid))
        updated += 1
    if window:
        lo, hi = (w.astimezone(timezone.utc).isoformat(timespec='seconds') for w in window)
        for row in c.execute("SELECT id FROM flights WHERE origin='feed' AND done=0 AND start IS NULL AND departure_manual=0 AND briefing_manual=0 AND departure>=? AND departure<?", (lo, hi)).fetchall():
            if row['id'] not in seen:
                c.execute("UPDATE flights SET done=1,status='Not in schedule',updated=? WHERE id=?", (t, row['id']))
    return added, updated


def units_key(): return 'units:' + now().strftime('%Y-%m')
def pages_key(): return 'fa-pages:' + now().strftime('%Y-%m')


def provider_name(c=None):
    if c is None:
        with db() as c2: return provider_name(c2)
    name = setting(c, 'provider', os.environ.get('FLIGHT_PROVIDER', 'aerodatabox'))
    return name if name in PROVIDERS else 'aerodatabox'


def run_sync(manual=False):
    """Fetch the next 12 hours of KU departures once; every screen shares the result."""
    with LOCK, db() as c:
        name = provider_name(c); feed = setting(c, 'feed', {})
        if manual and feed.get('last_attempt') and now() - datetime.fromisoformat(feed['last_attempt']) < timedelta(minutes=15):
            raise ValueError('Flights were refreshed less than 15 minutes ago. Please wait before refreshing again.')
        if name == 'aerodatabox':
            used = setting(c, units_key(), 0)
            if used + UNITS_PER_CALL > MONTHLY_UNITS:
                raise ValueError(f'Monthly AeroDataBox budget reached ({used}/{MONTHLY_UNITS} units).')
            put_setting(c, units_key(), used + UNITS_PER_CALL)
        elif setting(c, pages_key(), 0) + flightaware.MAX_PAGES > FA_MONTHLY_PAGES:
            raise ValueError(f'Monthly FlightAware budget reached ({FA_MONTHLY_PAGES} pages).')
        feed['last_attempt'] = stamp(now()); feed['provider'] = name
        put_setting(c, 'feed', feed)
    module = PROVIDERS[name]
    start = now(); end = start + timedelta(hours=12)
    try:
        if name == 'flightaware':
            departures, pages = module.fetch_departures(start, end)
            with LOCK, db() as c: put_setting(c, pages_key(), setting(c, pages_key(), 0) + pages)
        else:
            departures = module.fetch_departures()
    except Exception as e:
        message = module.describe_error(e)
        with LOCK, db() as c:
            feed = setting(c, 'feed', {}); feed.update(ok=False, error=message); put_setting(c, 'feed', feed)
        raise ValueError('Flight source unavailable (' + name + '): ' + message)
    with LOCK, db() as c:
        added, updated = store_departures(c, departures, (start, end))
        feed = setting(c, 'feed', {})
        feed.update(ok=True, error='', last_success=stamp(now()), count=len(departures), added=added, updated=updated, provider=name)
        put_setting(c, 'feed', feed)
    return feed


def sync_loop():
    while True:
        try:
            if PROVIDERS[provider_name()].has_key():
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


PUBLIC_FIELDS = ['id', 'number', 'label', 'destination', 'aircraft', 'briefing', 'sccm', 'crew', 'room', 'start', 'end', 'done']


def snapshot(auth):
    tick()
    recent = stamp(now() - timedelta(hours=1))
    with db() as c:
        rows = [dict(r, crew=crew_of(r)) for r in c.execute(
            'SELECT * FROM flights WHERE done=0 OR end>? ORDER BY briefing IS NULL,briefing,departure,number', (recent,))]
        feed = setting(c, 'feed', {})
        state = {'now': stamp(now()), 'rooms': ROOMS, 'timezone': 'Asia/Kuwait',
                 'feed': {k: feed.get(k) for k in ('last_success', 'last_attempt', 'ok', 'error', 'count', 'provider')}}
        if auth['role'] == 'admin':
            state['flights'] = rows
            name = provider_name(c)
            state['feed'].update(units_used=setting(c, units_key(), 0), units_budget=MONTHLY_UNITS,
                                 fa_pages_used=setting(c, pages_key(), 0), fa_pages_budget=FA_MONTHLY_PAGES,
                                 interval_hours=SYNC_HOURS, provider=name, key_configured=PROVIDERS[name].has_key(),
                                 providers={k: m.has_key() for k, m in PROVIDERS.items()})
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
            with LOCK, db() as c: result = import_rows(c, p.get('kind'), rows, bool(p.get('full_list')))
            return self.send(200, result)
        if path == '/api/sccm':
            with LOCK, db() as c:
                if not c.execute('SELECT 1 FROM flights WHERE id=?', (str(p.get('id')),)).fetchone(): raise ValueError('Flight not found.')
                set_crew(c, str(p.get('id')), str(p.get('sccm', ''))[:300])
            return self.send(200, {'ok': True})
        if path == '/api/aircraft':
            aircraft = str(p.get('aircraft', '')).strip()[:24]; aircraft_offset(aircraft)
            with LOCK, db() as c:
                row = c.execute('SELECT * FROM flights WHERE id=?', (str(p.get('id')),)).fetchone()
                if not row or not row['departure']: raise ValueError('Flight not found.')
                if row['start']: raise ValueError('This briefing has already started.')
                c.execute('UPDATE flights SET aircraft=?,aircraft_manual=1,needs_type=0,briefing=? WHERE id=?',
                          (aircraft, row['briefing'] if row['briefing_manual'] else briefing_from_departure(row['departure'], aircraft), row['id']))
            return self.send(200, {'ok': True})
        if path == '/api/departure':
            with LOCK, db() as c:
                row = c.execute('SELECT * FROM flights WHERE id=?', (str(p.get('id')),)).fetchone()
                if not row or not row['departure']: raise ValueError('Flight not found.')
                value = str(p.get('time', '')).strip()
                set_departure(c, row, gmt_departure(value, row['departure'][:10]))
            return self.send(200, {'ok': True})
        if path == '/api/provider':
            name = clean_choice(p.get('provider'), list(PROVIDERS), 'flight data source')
            if not PROVIDERS[name].has_key(): raise ValueError('The key for ' + name + ' is not configured on the server.')
            with db() as c:
                put_setting(c, 'provider', name)
                feed = setting(c, 'feed', {}); feed.pop('last_attempt', None); put_setting(c, 'feed', feed)   # refresh on next cycle
            return self.send(200, {'ok': True, 'provider': name})
        if path == '/api/sync':
            if not PROVIDERS[provider_name()].has_key(): raise ValueError('The flight data key for this source is not configured on the server.')
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
