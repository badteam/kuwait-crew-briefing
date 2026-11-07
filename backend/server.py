import base64,csv,hashlib,hmac,io,json,os,re,secrets,sqlite3,threading,time,zipfile
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path(__file__).parent
DATA=Path(os.environ.get('DATA_DIR',str(ROOT/'data'))); DATA.mkdir(exist_ok=True)
TZ=timezone(timedelta(hours=3)); ROOMS=['A-1','B-2','C-3','D-4','E-5','F-6','H-8']
LOCK=threading.RLock(); SESSIONS={}; FAILURES={}
DB=DATA/'briefing.sqlite3'
def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
with db() as c:
 c.execute('CREATE TABLE IF NOT EXISTS flights(id TEXT PRIMARY KEY,number TEXT,destination TEXT,aircraft TEXT,source TEXT,briefing TEXT,sccm TEXT,room TEXT,start TEXT,end TEXT,done INTEGER DEFAULT 0)')
 c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
def now(): return datetime.now(TZ)
def stamp(t): return t.isoformat(timespec='seconds')
def aircraft_offset(s):
 s=str(s).strip().upper().replace(' ','').replace('-','')
 if re.fullmatch(r'(B|BOEING)?777\w*',s) or re.fullmatch(r'(A|AIRBUS)?330\w*',s): return 70
 if s in ('SMALL','صغيرة','صغير') or re.fullmatch(r'(A|AIRBUS)?32[01]\w*',s): return 85
 raise ValueError('نوع طائرة غير معروف: '+s+'؛ استخدم SMALL أو A320 أو A321 أو B777 أو A330')
def parse_time(value,date=''):
 s=str(value).strip()
 try:
  if re.fullmatch(r'\d{1,2}:\d{2}(:\d{2})?',s): s=str(date).strip()+'T'+s
  t=datetime.fromisoformat(s.replace('Z','+00:00'))
  return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)
 except Exception: raise ValueError('أدخل التاريخ YYYY-MM-DD والوقت HH:MM أو تاريخًا ووقتًا كاملين')
def norm(v): return re.sub(r'[\s_\-]+','',str(v).strip().lower())
ALIASES={
 'number':['flight','flightnumber','رقمالرحلة','رقمالرحله'],
 'destination':['destination','flightname','الوجهة','الوجهه','اسمالرحلة','اسمالرحله'],
 'aircraft':['aircraft','aircrafttype','الطائرة','الطائره','نوعالطائرة'],
 'date':['date','التاريخ'], 'time':['time','filetime','الوقت','وقتالملف'],
 'sccm':['sccm','chief','رئيسالطاقم']}
def read_rows(data,name):
 if name.lower().endswith('.csv'): return list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
 if not name.lower().endswith('.xlsx'): raise ValueError('الملفات المدعومة XLSX وCSV')
 ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  if sum(i.file_size for i in z.infolist())>20_000_000: raise ValueError('الملف كبير جدًا')
  def xml(path):
   raw=z.read(path)
   if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw: raise ValueError('صيغة XML غير مدعومة')
   return ET.fromstring(raw)
  shared=[]
  if 'xl/sharedStrings.xml' in z.namelist():
   shared=[''.join(n.itertext()) for n in xml('xl/sharedStrings.xml').findall('m:si',ns)]
  sheets=sorted(n for n in z.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+.xml',n))
  if not sheets: raise ValueError('لا توجد ورقة عمل')
  rows=[]
  for r in xml(sheets[0]).findall('.//m:sheetData/m:row',ns):
   row=[]
   for cell in r.findall('m:c',ns):
    ref=re.match(r'[A-Z]+',cell.get('r','A')).group(); idx=0
    for ch in ref: idx=idx*26+ord(ch)-64
    if idx>50: raise ValueError('الملف يحتوي أعمدة أكثر من اللازم')
    while len(row)<idx: row.append('')
    if cell.find('m:f',ns) is not None: raise ValueError('حوّل معادلات Excel إلى قيم قبل الرفع')
    v=cell.findtext('m:v','',ns); typ=cell.get('t')
    row[idx-1]=shared[int(v)] if typ=='s' else ''.join(cell.find('m:is',ns).itertext()) if typ=='inlineStr' else v
   rows.append(row)
  return rows

def import_preview(payload):
 data=base64.b64decode(payload['data'],validate=True)
 if len(data)>3_000_000: raise ValueError('الحد الأقصى 3 ميغابايت')
 rows=[r for r in read_rows(data,payload['name']) if any(str(v).strip() for v in r)]
 if len(rows)<2 or len(rows)>2001: raise ValueError('الملف يجب أن يحتوي من 1 إلى 2000 صف')
 mapping={}
 for i,v in enumerate(rows[0]):
  for key,aliases in ALIASES.items():
   if norm(v) in aliases: mapping[key]=i
 crew=payload.get('kind')=='crew'; required=['number','sccm'] if crew else ['number','destination','aircraft','time']
 if any(k not in mapping for k in required): raise ValueError('الأعمدة المطلوبة: '+', '.join(required))
 result=[]; seen=set()
 for n,row in enumerate(rows[1:],2):
  def get(k): return str(row[mapping[k]]).strip() if k in mapping and mapping[k]<len(row) else ''
  number=re.sub(r'\s+','',get('number').upper())
  if not number or len(number)>20: raise ValueError('رقم رحلة غير صالح في صف '+str(n))
  item={'number':number,'sccm':get('sccm')[:120]}
  if crew:
   if not item['sccm']: raise ValueError('اسم رئيس الطاقم ناقص في صف '+str(n))
   item['date']=get('date')
  else:
   dt=get('date'); tm=get('time')
   # Excel numeric date/time cells use the 1899-12-30 epoch.
   if re.fullmatch(r'\d+(\.\d+)?',dt): dt=(datetime(1899,12,30)+timedelta(days=float(dt))).date().isoformat()
   if re.fullmatch(r'0?\.\d+|0',tm):
    seconds=round(float(tm)*86400); tm=f'{seconds//3600:02}:{seconds%3600//60:02}:00'
   t=parse_time(tm,dt); offset=aircraft_offset(get('aircraft'))
   key=number+'|'+stamp(t)
   if key in seen: raise ValueError('رحلة مكررة في صف '+str(n))
   seen.add(key)
   item.update(id=hashlib.sha256(key.encode()).hexdigest()[:24],destination=get('destination')[:120],aircraft=get('aircraft')[:24],source=stamp(t),briefing=stamp(t+timedelta(minutes=offset)))
   if not item['destination']: raise ValueError('الوجهة ناقصة في صف '+str(n))
  result.append(item)
 return result

def tick():
 with LOCK,db() as c:
  t=now()
  c.execute('UPDATE flights SET done=1 WHERE done=0 AND end IS NOT NULL AND end<=?',(stamp(t),))
  busy={r['room'] for r in c.execute('SELECT room FROM flights WHERE done=0 AND start IS NOT NULL')}
  # Expired, never-started imports are not suddenly presented as current sessions.
  c.execute('UPDATE flights SET done=1 WHERE done=0 AND start IS NULL AND briefing<?',(stamp(t-timedelta(hours=2)),))
  queue=c.execute('SELECT id FROM flights WHERE done=0 AND start IS NULL AND briefing<=? ORDER BY briefing,number',(stamp(t),)).fetchall()
  free=[r for r in ROOMS if r not in busy]
  for room,row in zip(free,queue): c.execute('UPDATE flights SET room=?,start=?,end=? WHERE id=?',(room,stamp(t),stamp(t+timedelta(minutes=10)),row['id']))

def snapshot():
 tick()
 with db() as c: rows=[dict(r) for r in c.execute('SELECT * FROM flights ORDER BY briefing,number')]
 return {'now':stamp(now()),'rooms':ROOMS,'flights':rows,'timezone':'Asia/Kuwait'}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def send(self,status,obj,ctype='application/json'):
  raw=json.dumps(obj,ensure_ascii=False).encode() if ctype=='application/json' else obj
  self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(raw)))
  self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
  origin=self.headers.get('Origin',''); allowed=os.environ.get('FRONTEND_ORIGIN','https://badteam.github.io')
  if origin==allowed: self.send_header('Access-Control-Allow-Origin',origin); self.send_header('Vary','Origin')
  self.end_headers(); self.wfile.write(raw)
 def do_OPTIONS(self):
  self.send_response(204)
  if self.headers.get('Origin')==os.environ.get('FRONTEND_ORIGIN','https://badteam.github.io'):
   self.send_header('Access-Control-Allow-Origin',self.headers['Origin']); self.send_header('Access-Control-Allow-Headers','Content-Type,Authorization'); self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
  self.end_headers()
 def role(self):
  token=self.headers.get('Authorization','').removeprefix('Bearer ')
  record=SESSIONS.get(token)
  return record[0] if record and record[1]>time.time() else None
 def do_GET(self):
  if self.path=='/health': return self.send(200,{'status':'ok'})
  if self.path=='/api/state':
   if not self.role(): return self.send(401,{'error':'سجّل الدخول أولًا'})
   return self.send(200,snapshot())
  return self.send(404,{'error':'Not found'})
 def do_POST(self):
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size<1 or size>4_100_000: return self.send(413,{'error':'حجم الطلب غير صالح'})
   p=json.loads(self.rfile.read(size))
   if self.path=='/api/login':
    ip=self.client_address[0]; attempts=FAILURES.get(ip,[]); attempts=[t for t in attempts if t>time.time()-300]
    if len(attempts)>=10: return self.send(429,{'error':'محاولات كثيرة؛ انتظر خمس دقائق'})
    supplied=str(p.get('password','')); role=None
    for key,label in [('ADMIN_PASSWORD','admin'),('DISPLAY_PASSWORD','display')]:
     expected=os.environ.get(key,'')
     if expected and hmac.compare_digest(supplied,expected): role=label
    if not role: FAILURES[ip]=attempts+[time.time()]; return self.send(401,{'error':'رمز الدخول غير صحيح'})
    token=secrets.token_urlsafe(32); SESSIONS[token]=(role,time.time()+86400*7); return self.send(200,{'token':token,'role':role})
   if not self.role(): return self.send(401,{'error':'سجّل الدخول أولًا'})
   if self.path=='/api/logout':
    SESSIONS.pop(self.headers.get('Authorization','').removeprefix('Bearer '),None); return self.send(200,{'ok':True})
   if self.role()!='admin': return self.send(403,{'error':'هذه العملية للمسؤول فقط'})
   if self.path in ('/api/preview','/api/import'):
    rows=import_preview(p)
    if self.path=='/api/preview': return self.send(200,{'rows':rows})
    with LOCK,db() as c:
     if p.get('kind')=='crew':
      for r in rows:
       matches=c.execute('SELECT id FROM flights WHERE number=? AND done=0 AND (?="" OR substr(source,1,10)=?)',(r['number'],r['date'],r['date'])).fetchall()
       if len(matches)!=1: raise ValueError('رقم '+r['number']+' غير موجود أو مكرر؛ أضف التاريخ أو صحّح رقم الرحلة')
       c.execute('UPDATE flights SET sccm=? WHERE id=?',(r['sccm'],matches[0]['id']))
     else:
      for r in rows:
       c.execute('INSERT INTO flights(id,number,destination,aircraft,source,briefing,sccm) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET destination=excluded.destination,aircraft=excluded.aircraft,sccm=CASE WHEN excluded.sccm="" THEN flights.sccm ELSE excluded.sccm END',(r['id'],r['number'],r['destination'],r['aircraft'],r['source'],r['briefing'],r['sccm']))
    return self.send(200,{'count':len(rows)})
   if self.path=='/api/extend':
    with LOCK,db() as c:
     row=c.execute('SELECT * FROM flights WHERE id=?',(p.get('id'),)).fetchone()
     if not row or row['done'] or not row['end'] or parse_time(row['end'])<=now(): raise ValueError('لا يمكن تمديد جلسة منتهية')
     c.execute('UPDATE flights SET end=? WHERE id=?',(stamp(parse_time(row['end'])+timedelta(minutes=5)),row['id']))
    return self.send(200,{'ok':True})
   return self.send(404,{'error':'Not found'})
  except (ValueError,KeyError,TypeError,zipfile.BadZipFile,ET.ParseError) as e: self.send(400,{'error':str(e)[:250]})
  except Exception: self.send(500,{'error':'تعذر تنفيذ العملية؛ لم تُحفظ التغييرات'})
if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler).serve_forever()
