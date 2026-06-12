"""
Home Assistant Integration Provider

Fetches time and weather data from Home Assistant API.
Used for clock synchronization and weather display features.
"""
import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from app.utils.logging import logger


@dataclass
class HomeAssistantConfig:
    """Configuration for Home Assistant connection"""
    url: str  # Base URL e.g., "http://homeassistant.local:8123"
    access_token: str  # Long-lived access token
    enabled: bool = False


class HomeAssistantProvider:
    """
    Service for fetching data from Home Assistant.

    Provides time and weather data for screensaver overlays.
    Weather data is cached to avoid excessive API calls.
    """

    def __init__(self, config: Optional[HomeAssistantConfig] = None):
        self.config = config
        self._http_client: Optional[httpx.AsyncClient] = None
        self._weather_cache: Dict[str, tuple] = {}  # entity_id -> (data, timestamp)
        self._cache_ttl = int(os.environ.get("HA_WEATHER_CACHE_TTL", "600"))

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client"""
        if self._http_client is None or self._http_client.is_closed:
            timeout = float(os.environ.get("HA_HTTP_TIMEOUT", "10.0"))
            self._http_client = httpx.AsyncClient(timeout=timeout)
        return self._http_client

    def _get_headers(self) -> Dict[str, str]:
        """Get authorization headers for HA API"""
        return {
            "Authorization": f"Bearer {self.config.access_token}",
            "Content-Type": "application/json",
        }

    async def get_time(self) -> Dict[str, Any]:
        """
        Get current time from Home Assistant.

        Attempts to fetch from sensor.date_time or falls back to
        rendering a template to get server time.

        Returns:
            Dict with 'datetime' (ISO format) and 'source' fields,
            or 'error' field on failure
        """
        if not self.config or not self.config.enabled:
            return {"error": "Home Assistant not configured"}

        client = await self._get_client()
        headers = self._get_headers()
        base_url = self.config.url.rstrip('/')

        try:
            # Try to get time via template API (most reliable)
            response = await client.post(
                f"{base_url}/api/template",
                headers=headers,
                json={"template": "{{ now().isoformat() }}"}
            )

            if response.status_code == 200:
                datetime_str = response.text.strip().strip('"')
                return {
                    "datetime": datetime_str,
                    "source": "homeassistant"
                }

            # Fallback: return error
            logger.warning(f"HA time API returned {response.status_code}")
            return {"error": f"HA API returned {response.status_code}"}

        except httpx.TimeoutException:
            logger.error("Timeout fetching time from Home Assistant")
            return {"error": "Connection timeout"}
        except Exception as e:
            logger.error(f"Failed to get HA time: {e}")
            return {"error": str(e)}

    async def get_weather(self, entity_id: str) -> Dict[str, Any]:
        """
        Get weather data from a Home Assistant weather entity.

        Results are cached for 10 minutes to reduce API load.

        Args:
            entity_id: Weather entity ID (e.g., "weather.home")

        Returns:
            Dict with weather data including condition, temperature, etc.,
            or 'error' field on failure
        """
        if not self.config or not self.config.enabled:
            return {"error": "Home Assistant not configured"}

        if not entity_id:
            return {"error": "No weather entity specified"}

        # Check cache
        now_ts = datetime.now(timezone.utc).timestamp()
        if entity_id in self._weather_cache:
            data, cached_ts = self._weather_cache[entity_id]
            if (now_ts - cached_ts) < self._cache_ttl:
                logger.debug(f"Returning cached weather for {entity_id}")
                return data

        client = await self._get_client()
        headers = self._get_headers()
        base_url = self.config.url.rstrip('/')

        try:
            response = await client.get(
                f"{base_url}/api/states/{entity_id}",
                headers=headers
            )
            response.raise_for_status()
            state = response.json()

            attrs = state.get("attributes", {})

            # Build weather data response (current conditions only)
            # Note: Forecast data is now fetched separately via get_forecast()
            # using the weather.get_forecasts service (deprecated from attributes)
            weather_data = {
                "condition": state.get("state"),  # e.g., "sunny", "cloudy", "rainy"
                "temperature": attrs.get("temperature"),
                "temperature_unit": attrs.get("temperature_unit", "°C"),
                "humidity": attrs.get("humidity"),
                "pressure": attrs.get("pressure"),
                "wind_speed": attrs.get("wind_speed"),
                "wind_bearing": attrs.get("wind_bearing"),
                "visibility": attrs.get("visibility"),
                "friendly_name": attrs.get("friendly_name", entity_id),
            }

            # Cache result
            self._weather_cache[entity_id] = (weather_data, now_ts)
            logger.debug(f"Cached weather for {entity_id}")

            return weather_data

        except httpx.HTTPStatusError as e:
            logger.error(f"HA weather API error: {e.response.status_code}")
            return {"error": f"API error: {e.response.status_code}"}
        except httpx.TimeoutException:
            logger.error(f"Timeout fetching weather from Home Assistant")
            return {"error": "Connection timeout"}
        except Exception as e:
            logger.error(f"Failed to get HA weather: {e}")
            return {"error": str(e)}

    async def get_sun(self, entity_id: str = "sun.sun") -> Dict[str, Any]:
        """
        Get the HA sun entity state for night-mode scheduling (F2).

        Returns {state: 'above_horizon'|'below_horizon', next_dawn, next_dusk}
        or an 'error' field on failure. Cached like weather.
        """
        if not self.config or not self.config.enabled:
            return {"error": "Home Assistant not configured"}
        if not entity_id:
            entity_id = "sun.sun"

        now_ts = datetime.now(timezone.utc).timestamp()
        cache_key = f"__sun__{entity_id}"
        if cache_key in self._weather_cache:
            data, cached_ts = self._weather_cache[cache_key]
            if (now_ts - cached_ts) < self._cache_ttl:
                return data

        client = await self._get_client()
        headers = self._get_headers()
        base_url = self.config.url.rstrip('/')
        try:
            response = await client.get(f"{base_url}/api/states/{entity_id}", headers=headers)
            response.raise_for_status()
            state = response.json()
            attrs = state.get("attributes", {})
            sun_data = {
                "state": state.get("state"),  # above_horizon | below_horizon
                "next_dawn": attrs.get("next_dawn"),
                "next_dusk": attrs.get("next_dusk"),
                "next_rising": attrs.get("next_rising"),
                "next_setting": attrs.get("next_setting"),
            }
            self._weather_cache[cache_key] = (sun_data, now_ts)
            return sun_data
        except httpx.HTTPStatusError as e:
            return {"error": f"API error: {e.response.status_code}"}
        except httpx.TimeoutException:
            return {"error": "Connection timeout"}
        except Exception as e:
            logger.error(f"Failed to get HA sun: {e}")
            return {"error": str(e)}

    async def get_forecast(
        self,
        entity_id: str,
        forecast_type: str = "daily",
        limit: int = 3
    ) -> Dict[str, Any]:
        """
        Fetch weather forecast via weather.get_forecasts service.

        Modern Home Assistant requires calling this service instead of
        reading forecast from entity attributes (deprecated).

        Args:
            entity_id: Weather entity ID (e.g., "weather.home")
            forecast_type: "daily", "hourly", or "twice_daily"
            limit: Number of forecast entries to return

        Returns:
            Dict with forecast_type and forecast array, or 'error' field on failure
        """
        if not self.config or not self.config.enabled:
            return {"error": "Home Assistant not configured"}

        if not entity_id:
            return {"error": "No weather entity specified"}

        # Check cache with type-specific key
        cache_key = f"{entity_id}:forecast:{forecast_type}"
        now_ts = datetime.now(timezone.utc).timestamp()
        if cache_key in self._weather_cache:
            data, cached_ts = self._weather_cache[cache_key]
            if (now_ts - cached_ts) < self._cache_ttl:
                # Return cached but apply limit
                logger.debug(f"Returning cached forecast for {cache_key}")
                cached_forecast = data.get("forecast", [])[:limit]
                return {**data, "forecast": cached_forecast}

        client = await self._get_client()
        headers = self._get_headers()
        base_url = self.config.url.rstrip('/')

        try:
            # Must use ?return_response to get forecast data from HA service call
            response = await client.post(
                f"{base_url}/api/services/weather/get_forecasts?return_response",
                headers=headers,
                json={
                    "entity_id": entity_id,
                    "type": forecast_type
                }
            )
            response.raise_for_status()
            result = response.json()

            # Extract forecast from service_response (not root level)
            # Response format: {"changed_states": [...], "service_response": {"weather.home": {"forecast": [...]}}}
            service_response = result.get("service_response", {})
            entity_data = service_response.get(entity_id, {})
            raw_forecast = entity_data.get("forecast", [])

            forecast_data = {
                "forecast_type": forecast_type,
                "forecast": [
                    {
                        "datetime": f.get("datetime"),
                        "condition": f.get("condition"),
                        "temperature": f.get("temperature"),
                        "templow": f.get("templow"),
                        "precipitation": f.get("precipitation"),
                        "precipitation_probability": f.get("precipitation_probability"),
                        "humidity": f.get("humidity"),
                        "wind_speed": f.get("wind_speed"),
                    }
                    for f in raw_forecast
                ]
            }

            # Cache full result
            self._weather_cache[cache_key] = (forecast_data, now_ts)
            logger.debug(f"Cached forecast for {cache_key}")

            # Return with limit applied
            return {**forecast_data, "forecast": forecast_data["forecast"][:limit]}

        except httpx.HTTPStatusError as e:
            logger.error(f"HA forecast API error: {e.response.status_code}")
            return {"error": f"API error: {e.response.status_code}"}
        except httpx.TimeoutException:
            logger.error("Timeout fetching forecast from Home Assistant")
            return {"error": "Connection timeout"}
        except Exception as e:
            logger.error(f"Failed to get HA forecast: {e}")
            return {"error": str(e)}

    def clear_cache(self, entity_id: Optional[str] = None):
        """
        Clear weather cache.

        Args:
            entity_id: Specific entity to clear, or None to clear all
        """
        if entity_id:
            # Clear both current weather and all forecast types for this entity
            keys_to_remove = [
                k for k in self._weather_cache.keys()
                if k == entity_id or k.startswith(f"{entity_id}:")
            ]
            for key in keys_to_remove:
                self._weather_cache.pop(key, None)
        else:
            self._weather_cache.clear()

    async def test_connection(self) -> Dict[str, Any]:
        """
        Test the connection to Home Assistant.

        Returns:
            Dict with 'success' boolean and 'message' or 'error'
        """
        if not self.config or not self.config.enabled:
            return {"success": False, "error": "Home Assistant not configured"}

        client = await self._get_client()
        headers = self._get_headers()
        base_url = self.config.url.rstrip('/')

        try:
            response = await client.get(
                f"{base_url}/api/",
                headers=headers
            )
            response.raise_for_status()
            data = response.json()

            return {
                "success": True,
                "message": data.get("message", "Connected"),
            }

        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def close(self):
        """Close the HTTP client"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
