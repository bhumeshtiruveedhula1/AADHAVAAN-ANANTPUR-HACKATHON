"""Small source-backed topology examples; operations are explicitly simulated."""
import re

METRO_SOURCE = "https://www.bmrc.co.in/schematic-route-map/"
BUS_SOURCE = "https://www.capitaland.com/content/dam/capitaland-media-library/businesspark-industrial-logistics/india/bangalore/international-tech-park-bangalore/BMTC%20ITPB%20BUS%20ROUTE%20TIME%20SCHEDULE-FEB%202023_2.pdf"
CORRIDORS = [
    {"id": "purple-central", "mode": "metro", "stops": ["Majestic", "Sir M Visvesvaraya", "Vidhana Soudha", "Cubbon Park", "MG Road", "Trinity", "Halasuru", "Indiranagar"], "source": METRO_SOURCE, "label": "Purple Line static station sequence"},
    {"id": "green-south", "mode": "metro", "stops": ["Lalbagh", "South End Circle", "Jayanagar"], "source": METRO_SOURCE, "label": "Green Line static station sequence"},
    {"id": "itpb-historical-bus", "mode": "bus", "stops": ["Hebbal Bus Stop", "Kempapura Bus Stop", "Nagavara Kere Arch", "Nagavara Cross"], "source": BUS_SOURCE, "label": "Historical BMTC ITPB stop sequence, February 2023; current service not verified"},
]


def normal(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold().replace("metro station", "").replace("station", ""))


def demo_connection(origin, destination, preference):
    mode = (preference or "").casefold()
    if mode not in {"metro", "bus", "transit", "public transport", ""}:
        return None
    for corridor in CORRIDORS:
        if (mode == "bus") != (corridor["mode"] == "bus"):
            continue
        stops = [normal(stop) for stop in corridor["stops"]]
        if normal(origin) not in stops or normal(destination) not in stops or normal(origin) == normal(destination):
            continue
        first, last = stops.index(normal(origin)), stops.index(normal(destination))
        ordered = corridor["stops"][min(first, last):max(first, last) + 1]
        if first > last:
            ordered = list(reversed(ordered))
        return {"id": corridor["id"], "mode": corridor["mode"], "stops": ordered,
                "source": corridor["source"], "static_label": corridor["label"], "checked_on": "2026-10-25",
                "simulated": True, "frequency_minutes": 5, "next_departure_in_minutes": 3,
                "notice": "Demo / Simulated: example frequency 5 minutes; example next departure in 3 minutes. This is a fixed demonstration snapshot, not live BMRCL/BMTC service information. External Google Maps may show different services."}
    return None
