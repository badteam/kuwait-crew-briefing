# Kuwait Airways Crew Briefing
Independent briefing-room dashboard. This is a custom project, not an official AIMS integration.

## Links
- Administration: https://badteam.github.io/kuwait-crew-briefing/admin.html
- Display (every screen): https://badteam.github.io/kuwait-crew-briefing/
- Demo preview: https://badteam.github.io/kuwait-crew-briefing/?demo=1

## Displays
The display page has no buttons or settings. An unpaired screen shows a 6-digit code; enter it in
Administration → Displays with the room (A-1, B-2, C-3, D-4, E-5, F-6, H-8 or main hall) and the
orientation (Automatic, Portrait, Landscape). Room and orientation can be changed later from Administration.
Display tokens are stored only on the screen device and hashed on the server.

## Flights
The server fetches Kuwait Airways departures from KWI (AeroDataBox via RapidAPI) every 6 hours for the
next 12 hours. One request costs 2 units; the plan allows 400 units/month, the service stops at 360.
Reporting time (Kuwait clock) = scheduled departure GMT clock + 1:25 for A320/A321, or + 1:10 for B777/A330.
Example: 14:00 GMT departure → 15:25 (A321) or 15:10 (B777/A330).
Flights with a missing or unknown aircraft type are flagged in Administration and wait for a manual type.
Refreshing a flight never changes its SCCM name, or the room and times of a started session.
Displays show the time of the last successful schedule update and warn when it is older than 8 hours.

## Rules
Sessions last 10 minutes; administrators can extend a live session by 5 minutes.
Due flights enter free rooms in time order and wait when all seven rooms are occupied.
Never-started flights over two hours late are closed.

## SCCM names
Type a name next to the flight in Administration, or upload CSV/XLSX with flight, date, sccm.
Names for flights not yet received are kept and applied when the flight arrives.
No names, flight records, keys, passwords or database files belong in this repository.

## Backend
Python 3.12, standard library only, SQLite under DATA_DIR. Environment: ADMIN_PASSWORD,
FRONTEND_ORIGIN=https://badteam.github.io, RAPIDAPI_KEY_FILE (default /run/secrets/rapidapi.key).
Server: /opt/crew-briefing, container crew-briefing-api (127.0.0.1:8091, network locarb-qr_default),
secrets in /etc/crew-briefing (root only). HTTPS through the existing Caddy as a separate site block
crew-api.187-124-4-29.sslip.io; other sites in that Caddyfile are untouched.

## Validation
cd backend && python test_server.py
