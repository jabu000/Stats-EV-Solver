"""Game-time weather from Open-Meteo (no API key required).

Weather matters differently per sport, so this provider only supplies conditions; the
feature modules decide what they mean. What it *does* decide is whether weather applies
at all: under a dome or a closed retractable roof the adjustment is switched off rather
than applied at a neutral value, so a Ford Field game is never quietly given a 5 mph
wind penalty.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain import RoofState
from app.features.context import WeatherContext
from app.providers.base import Provider, ProviderError


class WeatherProvider(Provider):
    name = "weather"
    label = "Open-Meteo Weather"
    base_url = "https://api.open-meteo.com/v1"

    HOURLY = (
        "temperature_2m,relative_humidity_2m,precipitation_probability,"
        "wind_speed_10m,wind_direction_10m"
    )

    def forecast(
        self,
        lat: float,
        lon: float,
        when: datetime | None,
        roof: RoofState = RoofState.OPEN,
        *,
        fixture_key: str = "default",
    ) -> WeatherContext:
        """Conditions at the hour nearest `when`."""
        # Indoors nothing else matters, and we skip the network call entirely.
        if roof.is_indoors:
            return WeatherContext(roof=roof, applies=False, source="indoors")

        try:
            result = self.fetch(
                "/forecast",
                fixture=f"forecast_{fixture_key}",
                params={
                    "latitude": round(lat, 4),
                    "longitude": round(lon, 4),
                    "hourly": self.HOURLY,
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "forecast_days": 3,
                    "timezone": "UTC",
                },
            )
        except ProviderError:
            # A weather outage must not blank the board; fall back to seasonal-neutral
            # conditions and let the caller mark confidence down.
            return WeatherContext(roof=roof, applies=True, source="unavailable")

        return self._pick_hour(result.payload, when, roof)

    @staticmethod
    def _pick_hour(
        payload: dict, when: datetime | None, roof: RoofState
    ) -> WeatherContext:
        hourly = (payload or {}).get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            return WeatherContext(roof=roof, applies=True, source="unavailable")

        target = when or datetime.now(timezone.utc)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)

        best_index, best_delta = 0, None
        for index, stamp in enumerate(times):
            try:
                parsed = datetime.fromisoformat(str(stamp)).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            delta = abs((parsed - target).total_seconds())
            if best_delta is None or delta < best_delta:
                best_index, best_delta = index, delta

        def at(key: str, default: float) -> float:
            series = hourly.get(key) or []
            if best_index < len(series) and series[best_index] is not None:
                try:
                    return float(series[best_index])
                except (TypeError, ValueError):
                    return default
            return default

        return WeatherContext(
            temperature_f=at("temperature_2m", 70.0),
            wind_mph=at("wind_speed_10m", 5.0),
            wind_direction_deg=at("wind_direction_10m", 0.0),
            humidity_pct=at("relative_humidity_2m", 50.0),
            precipitation_chance=at("precipitation_probability", 0.0) / 100.0,
            roof=roof,
            applies=True,
            source="open-meteo",
        )

    def health_check(self) -> tuple[bool, str, str]:
        try:
            result = self.fetch(
                "/forecast",
                fixture="forecast_default",
                params={
                    "latitude": 40.8296,
                    "longitude": -73.9262,
                    "hourly": "temperature_2m",
                    "forecast_days": 1,
                },
            )
        except ProviderError as exc:
            return False, exc.status, exc.message
        hours = len(((result.payload or {}).get("hourly") or {}).get("time") or [])
        return bool(hours), "ok" if hours else "empty", f"{hours} forecast hours"
