"""Read Kuwait Airways departures from KWI via AeroDataBox (RapidAPI).

The key is read from RAPIDAPI_KEY_FILE (default /run/secrets/rapidapi.key)
or the RAPIDAPI_KEY environment variable. It is never logged or returned.
One airport-schedule request costs 2 API units.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HOST = "aerodatabox.p.rapidapi.com"
KUWAIT = timezone(timedelta(hours=3))
KEY_FILE = Path(os.environ.get("RAPIDAPI_KEY_FILE", "/run/secrets/rapidapi.key"))


def api_key():
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        key = ""
    return key or os.environ.get("RAPIDAPI_KEY", "").strip()


def has_key():
    return bool(api_key())


def kuwait_departures(payload):
    flights = []
    for flight in payload.get("departures") or []:
        airline = flight.get("airline") or {}
        if airline.get("iata") != "KU" and airline.get("icao") != "KAC":
            continue
        if flight.get("isCargo") or flight.get("codeshareStatus") == "IsCodeshared":
            continue
        movement = flight.get("departure") or {}
        scheduled = (movement.get("scheduledTime") or {}).get("utc")
        if not scheduled:
            continue
        moment = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        destination = (flight.get("arrival") or {}).get("airport") or {}
        flights.append({
            "number": re.sub(r"\s+", "", flight["number"]).upper(),
            "departure_utc": moment.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "scheduled_departure": moment.astimezone(KUWAIT).isoformat(),
            "destination": destination.get("municipalityName") or destination.get("name") or destination.get("iata"),
            "aircraft": (flight.get("aircraft") or {}).get("model"),
            "status": flight.get("status"),
        })
    return flights


def fetch_departures(duration_minutes=720):
    key = api_key()
    if not key:
        raise RuntimeError("missing key")
    params = urlencode(dict(offsetMinutes=0, durationMinutes=duration_minutes, direction="Departure",
                            withLeg="true", withCodeshared="false", withCargo="false",
                            withCancelled="true", withPrivate="false", withLocation="false"))
    request = Request("https://" + HOST + "/flights/airports/iata/KWI?" + params,
                      headers={"X-RapidAPI-Host": HOST, "X-RapidAPI-Key": key, "User-Agent": "crew-briefing/1.0"})
    with urlopen(request, timeout=25) as response:
        if response.status == 204:
            return []
        return kuwait_departures(json.load(response))


def describe_error(error):
    """A short message that never contains the key."""
    if isinstance(error, HTTPError):
        return {401: "key rejected (401)", 403: "not subscribed or key rejected (403)",
                429: "rate or monthly quota exceeded (429)"}.get(error.code, f"HTTP {error.code}")
    if isinstance(error, URLError):
        return "network error"
    if isinstance(error, RuntimeError):
        return str(error)
    return type(error).__name__


if __name__ == "__main__":
    # One-off test: DATA_DIR=/etc/crew-briefing python aerodatabox.py
    directory = Path(os.environ.get("DATA_DIR", "/data"))
    target = directory / "kuwait-departures.json"
    temporary = target.with_suffix(".tmp")
    departures = fetch_departures()
    temporary.write_text(json.dumps({"retrieved_at": datetime.now(KUWAIT).isoformat(),
                                     "departures": departures}, ensure_ascii=False))
    temporary.replace(target)
    print(len(departures), "Kuwait Airways departures saved to", target)
