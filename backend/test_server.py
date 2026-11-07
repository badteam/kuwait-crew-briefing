import base64,importlib.util,os,tempfile,unittest
from datetime import timedelta
sandbox=tempfile.TemporaryDirectory();os.environ['DATA_DIR']=sandbox.name
spec=importlib.util.spec_from_file_location('app','server.py');app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
class Tests(unittest.TestCase):
 def test_offsets(self):
  for aircraft,minutes in [('A320',85),('A321',85),('SMALL',85),('B777-300ER',70),('A330-800',70)]: self.assertEqual(app.aircraft_offset(aircraft),minutes)
  with self.assertRaises(ValueError): app.aircraft_offset('unknown')
 def test_midnight(self):
  data='flight,destination,aircraft,date,time,sccm\nKU1,Test,A320,2026-11-07,23:30,Demo\n'
  rows=app.import_preview({'name':'test.csv','data':base64.b64encode(data.encode()).decode()})
  self.assertEqual(rows[0]['briefing'],'2026-11-08T00:55:00+03:00')
 def test_seven_rooms_and_queue(self):
  with app.db() as c:
   c.execute('DELETE FROM flights')
   for i in range(8): c.execute('INSERT INTO flights(id,number,briefing) VALUES(?,?,?)',(str(i),str(i),app.stamp(app.now()-timedelta(minutes=1))))
  app.tick()
  with app.db() as c:
   rows=c.execute('SELECT * FROM flights WHERE start IS NOT NULL').fetchall()
   self.assertEqual(len(rows),7);self.assertEqual(len({r['room'] for r in rows}),7)
   c.execute('UPDATE flights SET end=? WHERE id=?',(app.stamp(app.now()-timedelta(seconds=1)),rows[0]['id']))
  app.tick()
  with app.db() as c:
   self.assertEqual(c.execute('SELECT count(*) FROM flights WHERE done=0 AND start IS NOT NULL').fetchone()[0],7)
   self.assertIsNotNone(c.execute('SELECT start FROM flights WHERE id="7"').fetchone()[0])
 def test_duplicate_rejected(self):
  row='KU1,Test,A330,2026-11-07,10:00,Demo\n';data='flight,destination,aircraft,date,time,sccm\n'+row+row
  with self.assertRaises(ValueError): app.import_preview({'name':'x.csv','data':base64.b64encode(data.encode()).decode()})
if __name__=='__main__':unittest.main()
