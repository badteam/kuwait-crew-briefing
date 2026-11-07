# Kuwait Airways Crew Briefing
Independent briefing-room dashboard. This is a custom project, not an official AIMS integration.

## Display
Open the GitHub Pages site. Choose main hall, a room, or administration.
Orientation: landscape, portrait, or automatic; saved separately per screen on each device.
Examples: ?screen=A-1&orientation=portrait and ?screen=hall&orientation=landscape.
The demo uses fictional data only. Live screens need the authenticated backend.

## Rules
Rooms: A-1, B-2, C-3, D-4, E-5, F-6, H-8.
Add 85 minutes to the file timestamp for SMALL/A320/A321, or 70 for B777/A330.
All timestamps are interpreted in Kuwait time (UTC+03:00); day rollover is preserved.
Sessions last 10 minutes. Administrators can extend a live session by 5 minutes.
Due flights enter free rooms in time order. Flights wait when all seven rooms are occupied.
Never-started flights over two hours late are marked finished. Review imported timestamps before acceptance.

## Import
UTF-8 CSV or XLSX (first worksheet, values only).
Flights: flight,destination,aircraft,date,time,sccm.
Crew: flight,date,sccm. Date is needed when a flight number occurs on multiple dates.
Preview before committing. Import is additive; existing active sessions are not reset.
No operational flight records, names, passwords, or database files belong in this repository.

## Backend
Python 3.12, standard library only. SQLite persists under DATA_DIR.
Run server.py behind HTTPS. Configure ADMIN_PASSWORD and DISPLAY_PASSWORD with distinct strong values,
FRONTEND_ORIGIN=https://badteam.github.io, DATA_DIR=/data, PORT=8080.
Display credentials cannot import or extend sessions. Tokens expire after seven days and server restart.
Keep secrets outside source control. Only publish the static docs folder to Pages.
Use a separate container/volume and loopback port; do not replace another app or its proxy configuration.

## Validation
cd backend && python test_server.py
