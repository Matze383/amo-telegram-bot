"""
Command Plugin with Secret Example
Demonstrates using encrypted secrets and various settings types.
"""

import hashlib
import time


# Simple in-memory cache (per-process)
_cache = {}
CACHE_TTL_SECONDS = 600  # 10 minutes


def _get_cache_key(city: str, units: str) -> str:
    """Generate cache key for city/units combination."""
    return f"{city.lower()}:{units}"


def _get_cached(city: str, units: str):
    """Get cached weather data if not expired."""
    key = _get_cache_key(city, units)
    if key in _cache:
        data, timestamp = _cache[key]
        if time.time() - timestamp < CACHE_TTL_SECONDS:
            return data
        else:
            del _cache[key]
    return None


def _cache_result(city: str, units: str, data: dict):
    """Cache weather result with timestamp."""
    key = _get_cache_key(city, units)
    _cache[key] = (data, time.time())


def _fetch_weather(city: str, units: str, api_key: str) -> dict:
    """
    Fetch weather data from external API.

    Note: api_key is securely provided from encrypted storage.
    Never log or expose the actual key value.
    """
    # Create a deterministic mock response based on city name
    city_hash = int(hashlib.md5(city.encode()).hexdigest(), 16)
    temp_base = 15 + (city_hash % 15)  # 15-30 degrees base

    temp_map = {
        "metric": f"{temp_base}°C",
        "imperial": f"{int(temp_base * 9/5 + 32)}°F",
        "kelvin": f"{temp_base + 273.15}K"
    }

    conditions = ["Sunny", "Cloudy", "Rainy", "Partly Cloudy"]
    condition = conditions[city_hash % len(conditions)]

    return {
        "status": "success",
        "message": f"🌤 Weather in {city}: {condition}, {temp_map.get(units, temp_base)}",
        "type": "text",
        "data": {
            "city": city,
            "temperature": temp_map.get(units, temp_base),
            "condition": condition,
            "units": units
        }
    }


async def handle_command(context, host_api):
    """
    Execute the weather command.

    Args:
        context: Contains command_name, chat_id, message_id, user_id,
                 settings, secrets, etc.
        host_api: Provides send_message, reply methods
    """
    # Get API key from secrets (decrypted at runtime)
    api_key = context.secrets.get("api_key")
    if not api_key:
        await host_api.reply(
            context.chat_id,
            context.message_id,
            "⚠️ Weather API key not configured. Please set it in plugin settings."
        )
        return

    # Get city from command argument or use default
    args = context.argument.split() if context.argument else []
    city = args[0] if args else context.settings.get("default_city", "Berlin")
    units = context.settings.get("units", "metric")

    # Check cache if enabled
    if context.settings.get("cache_enabled", True):
        cached = _get_cached(city, units)
        if cached:
            await host_api.reply(context.chat_id, context.message_id, cached["message"])
            return

    # Fetch weather data (never log the actual API key!)
    weather_data = _fetch_weather(city, units, api_key)

    # Cache the result if enabled
    if context.settings.get("cache_enabled", True):
        _cache_result(city, units, weather_data)

    await host_api.reply(context.chat_id, context.message_id, weather_data["message"])
