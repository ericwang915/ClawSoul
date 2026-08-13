---
name: weather
description: "Get current weather and forecasts via Open-Meteo and wttr.in. Use when: user asks about weather, temperature, rain, wind, or forecasts for any location. NOT for: historical weather data, severe weather alerts, or detailed meteorological analysis. No API key needed."
metadata:
  emoji: "🌤️"
---

# Weather Skill

Fetch current weather and forecasts via Open-Meteo (Python script) or wttr.in (curl).

## When to Use

✅ **USE this skill when:**
- "What's the weather in Tokyo?"
- "Will it rain in London today?"
- "5-day forecast for New York"
- "Temperature and wind in Paris"
- "Is it snowing in Boston?"
- User asks about temperature, humidity, wind, precipitation, or conditions for any place

## When NOT to Use

❌ **DON'T use this skill when:**
- Historical weather data → use specialized historical APIs
- Severe weather alerts or warnings → use official weather alert services
- Detailed meteorological analysis → use professional weather tools

## Usage/Commands

### Option A — Python script (Open-Meteo)

```bash
python {skill_path}/weather.py "City Name" [options]
```

Options:
- `--forecast 3` — include N-day forecast (default: current only)
- `--format json` — output as JSON (default: human-readable text)
- `--units imperial` — use Fahrenheit/mph (default: metric)

### Option B — wttr.in (curl, no Python needed)

```bash
# Current weather for a city
curl -s "wttr.in/CityName?format=%l%20%t%20%h%20%w%20%c"

# Human-readable output (default)
curl -s "wttr.in/CityName"

# JSON output
curl -s "wttr.in/CityName?format=j1"

# 3-day forecast
curl -s "wttr.in/CityName?2"
```

### Examples

- "What's the weather in Tokyo?" → `python {skill_path}/weather.py "Tokyo"` or `curl -s wttr.in/Tokyo`
- "5-day forecast for New York" → `python {skill_path}/weather.py "New York" --forecast 5`
- "Weather in Paris in Fahrenheit" → `python {skill_path}/weather.py "Paris" --units imperial`

## Notes

- Open-Meteo geocodes city names and returns temperature, humidity, wind, condition, and precipitation
- wttr.in supports city names, airport codes, and lat/long in the URL path
- Both approaches are free and require no API key
