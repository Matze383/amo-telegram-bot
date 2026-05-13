"""
Command Plugin with Secret Example
Demonstrates using encrypted secrets and various settings types.
"""

import hashlib
import time
from typing import Dict, Any, Optional

class Plugin:
    """Weather command plugin with API key secret."""
    
    CACHE_TTL_SECONDS = 600  # 10 minutes
    
    def __init__(self, context: Dict[str, Any]):
        self.context = context
        self.settings = context.get("settings", {})
        self.logger = context.get("logger")
        self.secrets = context.get("secrets", {})
        self.cache = {}  # Simple in-memory cache
    
    def execute(self, trigger_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the weather command.
        
        Args:
            trigger_data: Contains user input, chat context, etc.
        
        Returns:
            Weather data or error message
        """
        # Get API key from secrets (decrypted at runtime)
        api_key = self.secrets.get("api_key")
        if not api_key:
            self.logger.error("Weather API key not configured")
            return {
                "status": "error",
                "message": "⚠️ Weather API key not configured. Please set it in plugin settings.",
                "type": "text"
            }
        
        # Get city from user input or use default
        city = trigger_data.get("args", [self.settings.get("default_city", "Berlin")])[0]
        units = self.settings.get("units", "metric")
        
        # Check cache if enabled
        if self.settings.get("cache_enabled", True):
            cached = self._get_cached(city, units)
            if cached:
                self.logger.info(f"Cache hit for weather: {city}")
                return cached
        
        # Log API call (never log the actual API key!)
        self.logger.info(f"Fetching weather for city: {city} (units: {units})")
        
        # Simulate API call (in real implementation, use actual weather API)
        weather_data = self._fetch_weather(city, units, api_key)
        
        # Cache the result if enabled
        if self.settings.get("cache_enabled", True):
            self._cache_result(city, units, weather_data)
        
        return weather_data
    
    def _fetch_weather(self, city: str, units: str, api_key: str) -> Dict[str, Any]:
        """
        Fetch weather data from external API.
        
        Note: api_key is securely provided from encrypted storage.
        Never log or expose the actual key value.
        """
        try:
            # In a real implementation, this would call an actual weather API
            # For this example, we simulate a successful response
            
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
            
        except Exception as e:
            self.logger.error(f"Weather API error: {e}")
            # Never include the API key in error messages!
            return {
                "status": "error",
                "message": "❌ Failed to fetch weather data. Please try again later.",
                "type": "text"
            }
    
    def _get_cache_key(self, city: str, units: str) -> str:
        """Generate cache key for city/units combination."""
        return f"{city.lower()}:{units}"
    
    def _get_cached(self, city: str, units: str) -> Optional[Dict[str, Any]]:
        """Get cached weather data if not expired."""
        key = self._get_cache_key(city, units)
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.CACHE_TTL_SECONDS:
                return data
            else:
                del self.cache[key]
        return None
    
    def _cache_result(self, city: str, units: str, data: Dict[str, Any]):
        """Cache weather result with timestamp."""
        key = self._get_cache_key(city, units)
        self.cache[key] = (data, time.time())
    
    def get_help(self) -> str:
        """Return help text for this command."""
        default_city = self.settings.get("default_city", "Berlin")
        return f"/weather [city] - Get weather info (default: {default_city})"
    
    def get_settings_summary(self) -> Dict[str, Any]:
        """
        Return settings summary for display.
        Secrets are masked, never exposed.
        """
        return {
            "default_city": self.settings.get("default_city", "Berlin"),
            "units": self.settings.get("units", "metric"),
            "cache_enabled": self.settings.get("cache_enabled", True),
            "api_key_configured": bool(self.secrets.get("api_key")),
            "api_key_masked": "***MASKED***" if self.secrets.get("api_key") else None
        }


# Plugin entry point
def create_plugin(context: Dict[str, Any]) -> Plugin:
    """Factory function to create plugin instance."""
    return Plugin(context)
