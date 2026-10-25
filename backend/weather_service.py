"""Open-Meteo city-level forecasts; all calendar arithmetic is Bengaluru local time."""
from datetime import datetime, timedelta, timezone
import math
import re
from threading import Lock
from time import monotonic

from city_http import CityServiceError, fetch_json

# India has no daylight-saving changes. Fixed UTC+05:30 works on Windows without
# installing an OS timezone database; the provider receives the IANA timezone.
IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
URL = "https://api.open-meteo.com/v1/forecast"
# IMD's Bangalore city reference: https://packolkata.imd.gov.in/download/smrs/Bangalore.pdf
REFERENCE = {"latitude": 12.97, "longitude": 77.58, "scope": "city-level forecast reference, not user GPS"}
FIELDS = ("temperature_2m", "precipitation_probability", "precipitation")
UNITS = {"temperature_2m": "°C", "precipitation_probability": "%", "precipitation": "mm"}


def time_window(phrase, now):
    local = now.astimezone(IST)
    text = (phrase or "today").strip().casefold()
    day = local.date()
    hours = (0, 24)
    if text in ("now", "right now", "current", "currently"):
        hours = (local.hour, local.hour + 1)
    else:
        match = re.fullmatch(r"(?:(today|tomorrow|this|tonight|\d{4}-\d{2}-\d{2})(?:\s+(morning|afternoon|evening|night))?|(morning|afternoon|evening))", text)
        if not match:
            raise CityServiceError("unsupported_time")
        day_word, part, standalone = match.groups()
        if day_word == "tomorrow":
            day += timedelta(days=1)
        elif day_word and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_word):
            try:
                day = datetime.fromisoformat(day_word).date()
            except ValueError:
                raise CityServiceError("unsupported_time") from None
        part = part or standalone or ("night" if day_word == "tonight" else None)
        hours = {"morning": (6, 12), "afternoon": (12, 18), "evening": (18, 22), "night": (21, 24)}.get(part, (0, 24))
    if not 0 <= (day - local.date()).days < 7:
        raise CityServiceError("unsupported_range")
    midnight = datetime.combine(day, datetime.min.time(), IST)
    return midnight + timedelta(hours=hours[0]), midnight + timedelta(hours=hours[1])


class WeatherService:
    def __init__(self, client=None, clock=None, timer=monotonic):
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timer = timer
        self._cache = {}  # One city/parameter/day key, at most one cached forecast.
        self._lock = Lock()

    def forecast(self, phrase=None):
        now = self.clock()
        start, end = time_window(phrase, now)
        params = {"latitude": REFERENCE["latitude"], "longitude": REFERENCE["longitude"],
                  "timezone": "Asia/Kolkata", "hourly": ",".join(FIELDS), "forecast_days": 8,
                  "temperature_unit": "celsius", "precipitation_unit": "mm"}
        key = (now.astimezone(IST).date().isoformat(), tuple(params.items()))
        if not self._lock.acquire(blocking=False):
            raise CityServiceError("busy")
        try:
            cached = self._cache.get(key)
            hit = cached is not None and self.timer() - cached[0] < 300
            if hit:
                rows, retrieved = cached[1:]
            else:
                payload = fetch_json("GET", URL, self.client, params=params)
                rows = self._validate(payload)
                retrieved = self.clock().isoformat()
                self._cache = {key: (self.timer(), rows, retrieved)}
        finally:
            self._lock.release()
        selected = [row for row in rows if start <= row[0] < end]
        # Temperature is instantaneous; precipitation probability/amount describe
        # the PRECEDING hour. For 18:00–22:00 use rain rows ending 19,20,21,22.
        precipitation = [row for row in rows if start < row[0] <= end]
        expected = int((end - start).total_seconds() / 3600)
        if len(selected) != expected or any(row[0] != start + timedelta(hours=i) for i, row in enumerate(selected)):
            raise CityServiceError("missing_data")
        if len(precipitation) != expected or any(row[0] != start + timedelta(hours=i + 1) for i, row in enumerate(precipitation)):
            raise CityServiceError("missing_data")
        if any(row[1] is None for row in selected) or any(value is None for row in precipitation for value in row[2:]):
            raise CityServiceError("missing_data")
        temperatures = [row[1] for row in selected]
        chances, rain = zip(*(row[2:] for row in precipitation))
        return {"source": {"name": "Open-Meteo", "url": "https://open-meteo.com/", "attribution": "Weather data by Open-Meteo (CC BY 4.0)", "retrieved_at": retrieved},
                "reference_location": dict(REFERENCE), "timezone": "Asia/Kolkata",
                "interval": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
                "temperature_min": min(temperatures), "temperature_max": max(temperatures),
                "precipitation_probability_max": max(chances), "precipitation_total": round(sum(rain), 2),
                "units": dict(UNITS), "cache_hit": hit, "cache_ttl_seconds": 300,
                "hourly": [{"time": row[0].isoformat(), "temperature_2m": row[1],
                            "precipitation_interval_end": precip[0].isoformat(),
                            "precipitation_probability": precip[2], "precipitation": precip[3]}
                           for row, precip in zip(selected, precipitation)]}

    @staticmethod
    def _validate(payload):
        try:
            if payload.get("timezone") != "Asia/Kolkata" or payload.get("utc_offset_seconds") != 19800:
                raise ValueError()
            if any(payload["hourly_units"].get(name) != unit for name, unit in UNITS.items()):
                raise ValueError()
            hourly = payload["hourly"]
            times = hourly["time"]
            if not isinstance(times, list) or not 1 <= len(times) <= 192:
                raise ValueError()
            if any(not isinstance(hourly.get(field), list) or len(hourly[field]) != len(times) for field in FIELDS):
                raise ValueError()
            rows = []
            for i, value in enumerate(times):
                moment = datetime.fromisoformat(value)
                if moment.tzinfo is not None or moment.minute or moment.second:
                    raise ValueError()
                values = [hourly[field][i] for field in FIELDS]
                for index, number in enumerate(values):
                    if number is not None and (type(number) not in (int, float) or not math.isfinite(number) or
                        not ((-100 <= number <= 70) if index == 0 else (0 <= number <= 100) if index == 1 else (number >= 0))):
                        raise ValueError()
                rows.append((moment.replace(tzinfo=IST), *values))
            if any(a[0] >= b[0] for a, b in zip(rows, rows[1:])):
                raise ValueError()
            return rows
        except (KeyError, TypeError, ValueError, AttributeError):
            raise CityServiceError("invalid_response") from None
