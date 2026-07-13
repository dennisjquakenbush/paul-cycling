"""
Race-day weather via Open-Meteo (free, no API key). Fetches the forecast for a
venue on race day plus the two days before (to judge mud), and returns a compact
summary the analytics can turn into pacing/fueling adjustments.

Forecasts only exist ~16 days out, so callers should check days_out first.
"""

import json
import urllib.request

WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "heavy showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorms", 96: "thunderstorms w/ hail", 99: "severe thunderstorms",
}
CARDINALS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _cardinal(deg):
    if deg is None:
        return None
    return CARDINALS[int((deg % 360) / 45 + 0.5) % 8]


def fetch_forecast(lat, lon, race_date):
    """Return a forecast summary for race_date, or None if unavailable."""
    # look back two days for antecedent rain (mud)
    from datetime import datetime, timedelta
    d0 = (datetime.fromisoformat(race_date).date() - timedelta(days=2)).isoformat()
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,"
           "precipitation_probability_max,windspeed_10m_max,winddirection_10m_dominant,"
           "weathercode&temperature_unit=fahrenheit&windspeed_unit=mph"
           "&precipitation_unit=inch&timezone=America/Indiana/Indianapolis"
           f"&start_date={d0}&end_date={race_date}")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read())
    except Exception:
        return None
    dl = d.get("daily") or {}
    times = dl.get("time") or []
    if race_date not in times:
        return None
    i = times.index(race_date)

    def at(key):
        v = dl.get(key) or []
        return v[i] if i < len(v) else None

    prior_precip = sum((dl.get("precipitation_sum") or [0])[:i]) if i > 0 else 0.0
    raceday_precip = at("precipitation_sum") or 0.0
    code = at("weathercode")
    wind_dir = at("winddirection_10m_dominant")

    return {
        "high_f": round(at("temperature_2m_max")) if at("temperature_2m_max") is not None else None,
        "low_f": round(at("temperature_2m_min")) if at("temperature_2m_min") is not None else None,
        "precip_in": round(raceday_precip, 2),
        "precip_prob": at("precipitation_probability_max"),
        "wind_mph": round(at("windspeed_10m_max")) if at("windspeed_10m_max") is not None else None,
        "wind_dir": _cardinal(wind_dir),
        "conditions": WMO.get(code, "mixed") if code is not None else None,
        "prior_precip_in": round(prior_precip, 2),
        "mud_risk": prior_precip >= 0.25 or raceday_precip >= 0.1,
    }


if __name__ == "__main__":
    print(fetch_forecast(39.664, -86.269, "2026-07-15"))
