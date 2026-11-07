import base64, importlib.util, os, sys, tempfile, unittest
from datetime import timedelta
from unittest import mock
sandbox = tempfile.TemporaryDirectory(); os.environ['DATA_DIR'] = sandbox.name
os.environ['RAPIDAPI_KEY_FILE'] = os.path.join(sandbox.name, 'none'); os.environ.pop('RAPIDAPI_KEY', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('app', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'server.py'))
app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)
import aerodatabox

def csv64(text): return base64.b64encode(text.encode()).decode()

SAMPLE = {'departures': [
    {'number': 'KU 157', 'airline': {'iata': 'KU'}, 'departure': {'scheduledTime': {'utc': '2026-11-07 14:00Z'}},
     'arrival': {'airport': {'municipalityName': 'Istanbul'}}, 'aircraft': {'model': 'Airbus A321 NEO'}, 'status': 'Expected'},
    {'number': 'KU 101', 'airline': {'iata': 'KU'}, 'departure': {'scheduledTime': {'utc': '2026-11-07 14:00Z'}},
     'arrival': {'airport': {'municipalityName': 'London'}}, 'aircraft': {'model': 'Boeing 777-300ER'}, 'status': 'Expected'},
    {'number': 'KU 999', 'airline': {'iata': 'KU'}, 'departure': {'scheduledTime': {'utc': '2026-11-07 20:00Z'}},
     'arrival': {'airport': {'iata': 'XYZ'}}, 'status': 'Expected'},
    {'number': 'EK 860', 'airline': {'iata': 'EK'}, 'departure': {'scheduledTime': {'utc': '2026-11-07 14:00Z'}}, 'arrival': {'airport': {}}},
]}

class Tests(unittest.TestCase):
    def setUp(self):
        with app.db() as c:
            for t in ('flights', 'crew_pending', 'settings', 'tokens', 'pairings'): c.execute('DELETE FROM ' + t)

    def test_offsets(self):
        for aircraft, minutes in [('A320', 85), ('A321', 85), ('SMALL', 85), ('Airbus A321 NEO', 85), ('A20N', 85),
                                  ('B777-300ER', 70), ('Boeing 777-300ER', 70), ('A330-800', 70), ('Airbus A330-900neo', 70), ('B77W', 70)]:
            self.assertEqual(app.aircraft_offset(aircraft), minutes, aircraft)
        for bad in ('unknown', '', None, 'Airbus A350-900'):
            with self.assertRaises(ValueError): app.aircraft_offset(bad)

    def test_reporting_rule_from_gmt_departure(self):
        # User's rule: 14:00 GMT departure -> 15:25 (A320/A321) or 15:10 (777/A330) Kuwait reporting time.
        self.assertEqual(app.briefing_from_departure('2026-11-07T14:00:00+00:00', 'Airbus A321 NEO'), '2026-11-07T15:25:00+03:00')
        self.assertEqual(app.briefing_from_departure('2026-11-07T14:00:00+00:00', 'B777'), '2026-11-07T15:10:00+03:00')
        self.assertIsNone(app.briefing_from_departure('2026-11-07T14:00:00+00:00', None))
        self.assertEqual(app.briefing_from_departure('2026-11-07T23:30:00+00:00', 'A320'), '2026-11-08T00:55:00+03:00')

    def test_feed_parse_and_store_keeps_sccm_and_room(self):
        deps = aerodatabox.kuwait_departures(SAMPLE)
        self.assertEqual([d['number'] for d in deps], ['KU157', 'KU101', 'KU999'])
        with app.db() as c:
            self.assertEqual(app.store_departures(c, deps), (3, 0))
            row = c.execute("SELECT * FROM flights WHERE number='KU157'").fetchone()
            self.assertEqual(row['briefing'], '2026-11-07T15:25:00+03:00'); self.assertEqual(row['destination'], 'Istanbul')
            unknown = c.execute("SELECT * FROM flights WHERE number='KU999'").fetchone()
            self.assertIsNone(unknown['briefing']); self.assertEqual(unknown['needs_type'], 1)
            c.execute("UPDATE flights SET sccm='Test Chief', room='A-1', start='2026-11-07T15:25:00+03:00', end='2026-11-07T15:35:00+03:00' WHERE number='KU157'")
            changed = [dict(d, destination='Istanbul IST', aircraft='Airbus A320') if d['number'] == 'KU157' else d for d in deps]
            self.assertEqual(app.store_departures(c, changed), (0, 3))
            row = c.execute("SELECT * FROM flights WHERE number='KU157'").fetchone()
            self.assertEqual((row['sccm'], row['room'], row['start']), ('Test Chief', 'A-1', '2026-11-07T15:25:00+03:00'))
            self.assertEqual(row['destination'], 'Istanbul IST')
            # Not started: SCCM still kept when the schedule refreshes.
            c.execute("UPDATE flights SET sccm='Second Chief' WHERE number='KU101'")
            app.store_departures(c, deps)
            self.assertEqual(c.execute("SELECT sccm FROM flights WHERE number='KU101'").fetchone()[0], 'Second Chief')

    def test_crew_upload_pending_until_flight_arrives(self):
        data = 'flight,date,sccm\nKU 157,2026-11-07,Pending Chief\n'
        rows = app.import_preview({'name': 'c.csv', 'data': csv64(data), 'kind': 'crew'})
        with app.db() as c:
            self.assertEqual(app.import_rows(c, 'crew', rows)['pending'], 1)
            app.store_departures(c, aerodatabox.kuwait_departures(SAMPLE))
            self.assertEqual(c.execute("SELECT sccm FROM flights WHERE number='KU157'").fetchone()[0], 'Pending Chief')
            self.assertEqual(c.execute('SELECT count(*) FROM crew_pending').fetchone()[0], 0)

    def test_midnight_import(self):
        data = 'flight,destination,aircraft,date,time,sccm\nKU1,Test,A320,2026-11-07,23:30,Demo\n'
        rows = app.import_preview({'name': 'test.csv', 'data': csv64(data)})
        self.assertEqual(rows[0]['briefing'], '2026-11-08T00:55:00+03:00')

    def test_seven_rooms_and_queue(self):
        with app.db() as c:
            for i in range(8): c.execute('INSERT INTO flights(id,number,briefing) VALUES(?,?,?)', (str(i), str(i), app.stamp(app.now() - timedelta(minutes=1))))
            c.execute("INSERT INTO flights(id,number,departure) VALUES('x','KU9',?)", ('2099-01-01T00:00:00+00:00',))
        app.tick()
        with app.db() as c:
            rows = c.execute('SELECT * FROM flights WHERE start IS NOT NULL').fetchall()
            self.assertEqual(len(rows), 7); self.assertEqual(len({r['room'] for r in rows}), 7)
            self.assertIsNone(c.execute("SELECT start FROM flights WHERE id='x'").fetchone()[0])
            c.execute('UPDATE flights SET end=? WHERE id=?', (app.stamp(app.now() - timedelta(seconds=1)), rows[0]['id']))
        app.tick()
        with app.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM flights WHERE done=0 AND start IS NOT NULL').fetchone()[0], 7)
            self.assertIsNotNone(c.execute('SELECT start FROM flights WHERE id="7"').fetchone()[0])

    def test_duplicate_rejected(self):
        row = 'KU1,Test,A330,2026-11-07,10:00,Demo\n'; data = 'flight,destination,aircraft,date,time,sccm\n' + row + row
        with self.assertRaises(ValueError): app.import_preview({'name': 'x.csv', 'data': csv64(data)})

    def test_budget_guard(self):
        with app.db() as c: app.put_setting(c, app.units_key(), app.MONTHLY_UNITS)
        with self.assertRaises(ValueError): app.run_sync()

    def test_sync_failure_recorded(self):
        with mock.patch.object(aerodatabox, 'fetch_departures', side_effect=RuntimeError('missing key')):
            with self.assertRaises(ValueError): app.run_sync()
        with app.db() as c:
            feed = app.setting(c, 'feed'); self.assertFalse(feed['ok']); self.assertEqual(app.setting(c, app.units_key()), 2)

    def test_manual_departure_survives_feed(self):
        deps = aerodatabox.kuwait_departures(SAMPLE)
        with app.db() as c:
            app.store_departures(c, deps)
            row = c.execute("SELECT * FROM flights WHERE number='KU157'").fetchone()
            app.set_departure(c, row, app.gmt_departure('10:00', row['departure'][:10]))
            row = c.execute("SELECT * FROM flights WHERE number='KU157'").fetchone()
            self.assertEqual(row['departure'], '2026-11-07T10:00:00+00:00'); self.assertEqual(row['briefing'], '2026-11-07T11:25:00+03:00')
            app.store_departures(c, deps)
            row = c.execute("SELECT * FROM flights WHERE number='KU157'").fetchone()
            self.assertEqual(row['briefing'], '2026-11-07T11:25:00+03:00'); self.assertEqual(row['departure_manual'], 1)

    def test_crew_upload_with_std(self):
        data = 'flight,date,std,sccm\nKU 157,2026-11-07,10:00,Chief A\nKU101,2026-11-07,09:15,Chief B\n'
        rows = app.import_preview({'name': 'c.csv', 'data': csv64(data), 'kind': 'crew'})
        with app.db() as c:
            app.store_departures(c, aerodatabox.kuwait_departures(SAMPLE)[:1])
            r = app.import_rows(c, 'crew', rows); self.assertEqual((r['matched'], r['pending']), (1, 1))
            self.assertEqual(c.execute("SELECT briefing FROM flights WHERE number='KU157'").fetchone()[0], '2026-11-07T11:25:00+03:00')
            app.store_departures(c, aerodatabox.kuwait_departures(SAMPLE))
            row = c.execute("SELECT * FROM flights WHERE number='KU101'").fetchone()
            self.assertEqual((row['sccm'], row['briefing']), ('Chief B', '2026-11-07T10:25:00+03:00'))
        with self.assertRaises(ValueError): app.gmt_departure('25:99', '2026-11-07')

if __name__ == '__main__': unittest.main()
