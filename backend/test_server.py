import base64, importlib.util, json, os, sys, tempfile, unittest
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

    def test_flightaware_parse_and_missing_flights_closed(self):
        import flightaware
        from datetime import datetime, timezone
        payload = {'scheduled_departures': [
            {'ident': 'KAC619', 'ident_iata': 'KU619', 'operator_iata': 'KU', 'flight_number': '619', 'scheduled_out': '2026-11-07T10:00:00Z',
             'destination': {'city': 'Doha'}, 'aircraft_type': 'A20N', 'cancelled': False, 'status': 'Scheduled'},
            {'ident': 'KAC101', 'ident_iata': 'KU101', 'operator_iata': 'KU', 'scheduled_out': '2026-11-07T14:00:00Z',
             'destination': {'city': 'London'}, 'aircraft_type': 'B77W', 'cancelled': True},
            {'ident': 'QTR1077', 'ident_iata': 'QR1077', 'operator_iata': 'QR', 'scheduled_out': '2026-11-07T10:00:00Z'}]}
        deps = flightaware.kuwait_departures(payload)
        self.assertEqual([(d['number'], d['aircraft'], d['status']) for d in deps], [('KU619', 'A20N', 'Scheduled'), ('KU101', 'B77W', 'Cancelled')])
        self.assertEqual(app.briefing_from_departure(deps[0]['departure_utc'], deps[0]['aircraft']), '2026-11-07T11:25:00+03:00')
        window = (datetime(2026, 11, 7, 9, tzinfo=timezone.utc), datetime(2026, 11, 7, 21, tzinfo=timezone.utc))
        with app.db() as c:
            app.store_departures(c, aerodatabox.kuwait_departures(SAMPLE))       # KU157, KU101, KU999 from the other source
            c.execute("UPDATE flights SET sccm='Kept' WHERE number='KU157'")
            app.store_departures(c, deps, window)
            rows = {r['number']: dict(r) for r in c.execute('SELECT * FROM flights')}
            self.assertEqual((rows['KU157']['done'], rows['KU157']['status'], rows['KU157']['sccm']), (1, 'Not in schedule', 'Kept'))
            self.assertEqual(rows['KU101']['done'], 1); self.assertEqual(rows['KU619']['done'], 0)
            app.store_departures(c, aerodatabox.kuwait_departures(SAMPLE))       # reappears -> reopened, name kept
            row = c.execute("SELECT * FROM flights WHERE number='KU157'").fetchone()
            self.assertEqual((row['done'], row['sccm']), (0, 'Kept'))

    def test_parse_crew(self):
        self.assertEqual(app.parse_crew('Ahmed Ali / Sara M (FSS)'), [{'role': 'SCCM', 'name': 'Ahmed Ali'}, {'role': 'FSS', 'name': 'Sara M'}])
        self.assertEqual(app.parse_crew('CCI - Noura Saleh', 'SCCM'), [{'role': 'CCI', 'name': 'Noura Saleh'}])
        self.assertEqual(app.parse_crew('Fahad, Yousef', 'SCCM'), [{'role': 'SCCM', 'name': 'Fahad'}, {'role': 'SCCM', 'name': 'Yousef'}])
        self.assertEqual(app.crew_text(app.parse_crew('A / B (CCI)')), 'A / B (CCI)')

    def test_roster_upload_exact_reporting_and_names(self):
        data = ('Flight,Date,Reporting,Chief Cabin,Chief Cabin 2,FSS Supervisor\n'
                'KU157,2026-11-07,15:05,Ahmed Ali,,Sara M\n'
                '619,2026-11-07,11:25,Fahad K,Noura S (CCI),\n')
        rows = app.import_preview({'name': 'r.csv', 'data': csv64(data), 'kind': 'roster'})
        self.assertEqual(rows[0]['reporting'], '2026-11-07T15:05:00+03:00')
        self.assertEqual(rows[0]['crew'], [{'role': 'SCCM', 'name': 'Ahmed Ali'}, {'role': 'FSS', 'name': 'Sara M'}])
        self.assertEqual(rows[1]['crew'], [{'role': 'SCCM', 'name': 'Fahad K'}, {'role': 'CCI', 'name': 'Noura S'}])
        with app.db() as c:
            app.store_departures(c, aerodatabox.kuwait_departures(SAMPLE))      # KU157 (calc 15:25), KU101, KU999
            r = app.import_rows(c, 'roster', rows, True)
            self.assertEqual((r['matched'], r['added']), (1, 1)); self.assertEqual(r['closed'], 2)   # KU101 and KU999 not in roster
            ku157 = c.execute("SELECT * FROM flights WHERE number='KU157'").fetchone()
            self.assertEqual((ku157['briefing'], ku157['sccm']), ('2026-11-07T15:05:00+03:00', 'Ahmed Ali / Sara M (FSS)'))
            # A later schedule update neither recalculates the time nor reopens flights missing from the roster.
            app.store_departures(c, aerodatabox.kuwait_departures(SAMPLE))
            ku157 = c.execute("SELECT * FROM flights WHERE number='KU157'").fetchone()
            self.assertEqual((ku157['briefing'], ku157['sccm'], ku157['done']), ('2026-11-07T15:05:00+03:00', 'Ahmed Ali / Sara M (FSS)', 0))
            self.assertEqual(c.execute("SELECT done FROM flights WHERE number='KU101'").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM flights WHERE number='KU157'").fetchone()[0], 1)
            ku619 = c.execute("SELECT * FROM flights WHERE number='KU619'").fetchone()
            self.assertEqual((ku619['briefing'], json.loads(ku619['crew'])[1]['role']), ('2026-11-07T11:25:00+03:00', 'CCI'))

    def test_roster_excel_time_cells(self):
        self.assertEqual(app.parse_reporting('0.4722222222', '2026-11-07')[0], '2026-11-07T11:20:00+03:00')
        self.assertEqual(app.parse_reporting('46333.4722222222', '')[0][11:16], '11:20')
        self.assertEqual(app.parse_reporting('1125', '2026-11-07')[0], '2026-11-07T11:25:00+03:00')
        with self.assertRaises(ValueError): app.parse_reporting('soon', '2026-11-07')

    def test_operations_crew_list_format(self):
        import shutil
        here = os.path.dirname(os.path.abspath(__file__))
        data = ('Crew Reporting Kuwait Airways — Crew Reporting — 09 Nov 2026,,,,,\n'
                'REPT: Kuwait time (UTC+3). Each matching CCI / FSS / INS is listed separately.,,,,,\n'
                'FLT,REG,NAME,RANK,STAFF ID,REPT (Kuwait)\n'
                '545,9K-AOM,Test Two,INS,2,06:15\n545,9K-AOM,TEST THREE,CCI,3,06:15\n'
                '161,9K-AQA,TEST FIVE,CCI,5,07:20\n161,9K-AQA,TEST SIX,CCI,6,08:25\n'
                '619,9K-AQB,TEST SEVEN,FSS,7,11:25\n620,9K-AQB,TEST TEN,CCI,10,11:25\n411,9K-AOI,TEST NINE,CCI,9,21:45\n303,9K-APG,TEST EIGHT,CCI,8,00:40\n')
        rows = app.import_preview({'name': 'list.csv', 'data': csv64(data), 'kind': 'roster'})
        got = [(r['number'], r['reporting'][5:16], r['crew_text']) for r in rows]
        self.assertEqual(got[0], ('KU545', '11-09T06:15', 'Test Two (INS) / TEST THREE (CCI)'))
        self.assertEqual(got[-1], ('KU303', '11-10T00:40', 'TEST EIGHT (CCI)'))
        self.assertEqual([g[1] for g in got if g[0] == 'KU161'], ['11-09T07:20', '11-09T08:25'])
        self.assertNotIn('5', ' '.join(r['crew_text'] for r in rows))   # staff IDs are not shown
        ku619 = next(r for r in rows if r['number'] == 'KU619')
        self.assertEqual((ku619['label'], ku619['crew_text']), ('KU619 / 620', 'TEST SEVEN (FSS) / TEST TEN (CCI)'))
        with app.db() as c:
            r = app.import_rows(c, 'roster', rows, True)
            self.assertEqual(r['added'], 6)
            self.assertEqual(c.execute("SELECT count(*) FROM flights WHERE number='KU161' AND done=0").fetchone()[0], 2)

if __name__ == '__main__': unittest.main()
