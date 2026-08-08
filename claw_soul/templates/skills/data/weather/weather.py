#!/usr/bin/env python3
"""Fetch weather data from Open-Meteo (free, no API key)."""

import argparse
import json
import sys
import urllib.parse
import urllib.request

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Slight showers", 81: "Moderate showers",
    82: "Violent showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ClawSoul/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def geocode(city: str) -> dict:
    params = urllib.parse.urlencode({"name": city, "count": 1, "language": "en"})
    data = _fetch_json(f"{GEOCODE_URL}?{params}")
    results = data.get("results", [])
    if not results:
        raise ValueError(f"Location not found: {city}")
    r = results[0]
    return {
        "name": r.get("name", city),
        "country": r.get("country", ""),
        "lat": r["latitude"],
        "lon": r["longitude"],
    }


def fetch_weather(lat: float, lon: float, forecast_days: int = 1,
                  imperial: bool = False) -> dict:
    temp_unit = "fahrenheit" if imperial else "celsius"
    wind_unit = "mph" if imperial else "kmh"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
        "temperature_unit": temp_unit,
        "wind_speed_unit": wind_unit,
    }
    if forecast_days > 1:
        params["daily"] = "temperature_2m_max,temperature_2m_min,weather_code,precipitation_sum"
        params["forecast_days"] = forecast_days
        params["timezone"] = "auto"

    qs = urllib.parse.urlencode(params)
    return _fetch_json(f"{WEATHER_URL}?{qs}")


def format_current(location: dict, data: dict, imperial: bool) -> str:
    c = data.get("current", {})
    temp = c.get("temperature_2m", "?")
    humidity = c.get("relative_humidity_2m", "?")
    wind = c.get("wind_speed_10m", "?")
    precip = c.get("precipitation", 0)
    code = c.get("weather_code", 0)
    condition = WMO_CODES.get(code, f"Code {code}")

    t_unit = "F" if imperial else "C"
    w_unit = "mph" if imperial else "km/h"

    lines = [
        f"Weather in {location['name']}, {location['country']}",
        f"  Condition: {condition}",
        f"  Temperature: {temp}°{t_unit}",
        f"  Humidity: {humidity}%",
        f"  Wind: {wind} {w_unit}",
    ]
    if precip > 0:
        lines.append(f"  Precipitation: {precip} mm")
    return "\n".join(lines)


def format_forecast(data: dict, imperial: bool) -> str:
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    if not dates:
        return ""

    t_unit = "F" if imperial else "C"
    lines = ["\nForecast:"]
    for i, date in enumerate(dates):
        hi = daily["temperature_2m_max"][i]
        lo = daily["temperature_2m_min"][i]
        code = daily["weather_code"][i]
        precip = daily["precipitation_sum"][i]
        condition = WMO_CODES.get(code, f"Code {code}")
        line = f"  {date}: {lo}–{hi}°{t_unit}  {condition}"
        if precip > 0:
            line += f"  (precip: {precip}mm)"
        lines.append(line)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Get weather for any location.")
    parser.add_argument("city", help="City name (e.g. 'Tokyo', 'New York')")
    parser.add_argument("--forecast", type=int, default=1,
                        help="Number of forecast days (default: current only)")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--units", choices=["metric", "imperial"], default="metric")
    args = parser.parse_args()

    imperial = args.units == "imperial"

    try:
        loc = geocode(args.city)
        data = fetch_weather(loc["lat"], loc["lon"],
                             forecast_days=max(args.forecast, 1),
                             imperial=imperial)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        data["location"] = loc
        print(json.dumps(data, indent=2))
    else:
        print(format_current(loc, data, imperial))
        if args.forecast > 1:
            print(format_forecast(data, imperial))


if __name__ == "__main__":
    main()
