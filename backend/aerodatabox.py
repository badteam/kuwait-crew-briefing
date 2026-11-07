"""Read the Kuwait departure board on the server with a RapidAPI credential."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HOST = "aerodatabox.p.rapidapi.com"
KUWAIT = timezone(timedelta(hours=3))


def kuwait_departures(payload):
    flights = []
    for flight in payload["departures"]:
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
            "number": re.sub(r"\s+", "", flight["number"]),
            "scheduled_departure": moment.astimezone(KUWAIT).isoformat(),
            "destination": destination.get("municipalityName") or destination.get("name") or destination.get("iata"),
            "aircraft": (flight.get("aircraft") or {}).get("model"),
            "status": flight.get("status"),
        })
    return flights


def fetch_departures():
    key = os.environ["RAPIDAPI_KEY"]
    params = urlencode(dict(offsetMinutes=0, durationMinutes=720, direction="Departure",
                            withLeg="true", withCodeshared="false", withCargo="false"))
    request = Request("https://" + HOST + "/flights/airports/iata/KWI?" + params,
                      headers={"X-RapidAPI-Host": HOST, "X-RapidAPI-Key": key})
    with urlopen(request, timeout=25) as response:
        return kuwait_departures(json.load(response))


if __name__ == "__main__":
    directory = Path(os.environ.get("DATA_DIR", "/data"))
    target = directory / "kuwait-departures.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"retrieved_at": datetime.now(KUWAIT).isoformat(),
                                     "departures": fetch_departures()}, ensure_ascii=False))
    temporary.replace(target)
