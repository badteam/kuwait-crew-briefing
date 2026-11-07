"""Read Kuwait Airways departures from KWI via FlightAware AeroAPI v4.

The key is read from FLIGHTAWARE_KEY_FILE (default /run/secrets/flightaware.key)
or FLIGHTAWARE_KEY. Billing is per page of up to 15 flights.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://aeroapi.flightaware.com/aeroapi"
KEY_FILE = Path(os.environ.get("FLIGHTAWARE_KEY_FILE", "/run/secrets/flightaware.key"))
MAX_PAGES = 4


def api_key():
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        key = ""
    return key or os.environ.get("FLIGHTAWARE_KEY", "").strip()


def has_key():
    return bool(api_key())


def iso(value):
    if not value:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def kuwait_departures(payload):
    flights = []
    for f in payload.get("scheduled_departures") or []:
        operator = (f.get("operator_iata") or "").upper()
        ident = (f.get("ident_iata") or "").upper().replace(" ", "")
        if operator != "KU" and not ident.startswith("KU") and not (f.get("ident") or "").startswith("KAC"):
            continue
        number = ident if ident.startswith("KU") else "KU" + str(f.get("flight_number") or "").lstrip("0")
        if not re.fullmatch(r"KU\d{1,4}", number):
            continue
        departure = iso(f.get("scheduled_out") or f.get("scheduled_off"))
        if not departure:
            continue
        destination = f.get("destination") or {}
        flights.append({
            "number": number,
            "departure_utc": departure,
            "destination": destination.get("city") or destination.get("name") or destination.get("code_iata"),
            "aircraft": f.get("aircraft_type"),
            "status": "Cancelled" if f.get("cancelled") else (f.get("status") or "Scheduled"),
        })
    return flights


def fetch_departures(start, end):
    """Return (departures, pages_used) for KU departures scheduled between start and end (UTC)."""
    key = api_key()
    if not key:
        raise RuntimeError("missing key")
    params = urlencode({"airline": "KAC", "type": "Airline", "max_pages": MAX_PAGES,
                        "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "end": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    url = BASE + "/airports/OKBK/flights/scheduled_departures?" + params
    departures, pages = [], 0
    while url and pages < 8:
        request = Request(url, headers={"x-apikey": key, "Accept": "application/json", "User-Agent": "crew-briefing/1.0"})
        with urlopen(request, timeout=25) as response:
            payload = json.load(response)
        pages += max(1, int(payload.get("num_pages") or 1))
        departures += kuwait_departures(payload)
        nxt = (payload.get("links") or {}).get("next")
        url = BASE + nxt if nxt else None
    unique = {(d["number"], d["departure_utc"]): d for d in departures}
    return list(unique.values()), pages


def describe_error(error):
    if isinstance(error, HTTPError):
        return {401: "key rejected (401)", 403: "access denied (403)", 429: "rate limit (429)"}.get(error.code, f"HTTP {error.code}")
    if isinstance(error, URLError):
        return "network error"
    if isinstance(error, RuntimeError):
        return str(error)
    return type(error).__name__


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    flights, used = fetch_departures(now - timedelta(hours=3), now + timedelta(hours=12))
    for f in sorted(flights, key=lambda x: x["departure_utc"]):
        print(f["number"], f["departure_utc"][11:16] + "Z", (f["destination"] or "")[:14], f["aircraft"], f["status"])
    print(len(flights), "flights,", used, "pages")
