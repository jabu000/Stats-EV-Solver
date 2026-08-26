"""Provider plumbing shared by every upstream data source.

Every provider fetch goes through `Provider.fetch`, which has two backends:

* **live** -- a real HTTP request, with retries and a short on-disk cache.
* **fixture** -- a recorded JSON payload read from ``backend/fixtures/``.

The mode is a config switch, not a code path the callers know about, so the model and
API layers behave identically whether or not the machine can reach the internet. This
matters because the sandbox this was built in blocks egress to every sports data host;
fixture mode is what makes the pipeline testable there, and it doubles as an offline
mode and a deterministic substrate for the test suite.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import DataMode, get_settings


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce usable data."""

    def __init__(self, provider: str, message: str, *, status: str = "error") -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.message = message
        self.status = status


class FixtureMissing(ProviderError):
    """No recorded payload for this request. Distinct so callers can degrade politely."""


@dataclass
class FetchResult:
    payload: Any
    source: str  # "live" | "fixture" | "cache"
    duration_ms: int = 0
    status: str = "ok"


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class Provider:
    """Base class. Subclasses set `name`, `label`, `base_url` and add typed methods."""

    name: str = "provider"
    label: str = "Provider"
    base_url: str = ""
    requires_key: bool = False

    def __init__(self) -> None:
        self.settings = get_settings()
        self._cache: dict[str, _CacheEntry] = {}

    # ------------------------------------------------------------------ hooks
    def headers(self) -> dict[str, str]:
        """Subclasses override to add auth. Kept separate so tokens are never logged."""
        return {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        }

    def is_configured(self) -> tuple[bool, str]:
        """Whether this provider has what it needs to run. Shown in Settings."""
        return True, ""

    # ------------------------------------------------------------------ fetch
    def fetch(
        self,
        path: str,
        *,
        fixture: str,
        params: dict[str, Any] | None = None,
        parse: str = "json",
    ) -> FetchResult:
        """Fetch `path`, or load the `fixture` file when not in live mode."""
        started = time.perf_counter()

        if self.settings.data_mode is DataMode.FIXTURE:
            payload = self.load_fixture(fixture, parse=parse)
            return FetchResult(
                payload=payload,
                source="fixture",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        cache_key = f"{path}?{sorted((params or {}).items())}"
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > time.monotonic():
            return FetchResult(payload=cached.value, source="cache", duration_ms=0)

        payload = self._http_get(path, params=params, parse=parse)
        self._cache[cache_key] = _CacheEntry(
            value=payload, expires_at=time.monotonic() + self.settings.cache_ttl_seconds
        )
        return FetchResult(
            payload=payload,
            source="live",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _http_get(
        self, path: str, *, params: dict[str, Any] | None, parse: str
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(self.settings.http_retries + 1):
            try:
                with httpx.Client(
                    timeout=self.settings.http_timeout_seconds, follow_redirects=True
                ) as client:
                    response = client.get(url, params=params, headers=self.headers())
                if response.status_code == 401 or response.status_code == 403:
                    raise ProviderError(
                        self.name,
                        f"HTTP {response.status_code} -- the endpoint requires "
                        f"authentication or is blocking this client.",
                        status=f"http_{response.status_code}",
                    )
                response.raise_for_status()
                if parse == "json":
                    return response.json()
                return response.text
            except ProviderError:
                raise
            except Exception as exc:  # network hiccup: back off and retry
                last_error = exc
                if attempt < self.settings.http_retries:
                    time.sleep(2**attempt)

        raise ProviderError(self.name, f"request to {url} failed: {last_error}")

    # ---------------------------------------------------------------- fixture
    def fixture_path(self, fixture: str) -> Path:
        return self.settings.fixture_dir / self.name / f"{fixture}.json"

    def load_fixture(self, fixture: str, *, parse: str = "json") -> Any:
        path = self.fixture_path(fixture)
        if not path.exists():
            # Most fixture names end in a date or a season ("schedule_2026-04-01",
            # "weekly_2025"). Recording one per day would mean the offline board goes
            # dark the next morning, so fall back to a `_default` variant of the same
            # request. This is what keeps fixture mode usable indefinitely.
            fallback = next(
                (
                    candidate
                    for candidate in (
                        self.fixture_path(name) for name in default_variants(fixture)
                    )
                    if candidate.exists()
                ),
                None,
            )
            if fallback is None:
                raise FixtureMissing(
                    self.name,
                    f"no recorded fixture at "
                    f"{path.relative_to(self.settings.fixture_dir.parent)}",
                    status="fixture_missing",
                )
            path = fallback
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if parse == "json" else text

    def save_fixture(self, fixture: str, payload: Any) -> Path:
        """Record a live payload so the same call works offline later."""
        path = self.fixture_path(fixture)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path

    # ----------------------------------------------------------- diagnostics
    def health_check(self) -> tuple[bool, str, str]:
        """(ok, status, detail) for the Settings tab. Subclasses usually override."""
        configured, why = self.is_configured()
        if not configured:
            return False, "not_configured", why
        return True, "ok", ""


_DATE_TOKEN = re.compile(r"^(\d{4}(-\d{2}-\d{2})?)$")


def default_variants(fixture: str) -> list[str]:
    """Fallback fixture names to try when the exact one is not recorded.

    Two rules, in order of specificity:

    1. Replace a season or ISO-date token wherever it appears, so `hitting_2025_vl`
       falls back to `hitting_default_vl` rather than losing the `vl` split code.
    2. Replace the trailing segment, which covers keys like `forecast_BOS`.

    A name containing neither (``over_under_lines``) yields no date-based candidate, so
    a genuinely missing fixture still raises rather than silently loading the wrong file.
    """
    candidates: list[str] = []
    parts = fixture.split("_")

    if any(_DATE_TOKEN.match(part) for part in parts):
        candidates.append(
            "_".join("default" if _DATE_TOKEN.match(part) else part for part in parts)
        )
    if len(parts) > 1:
        candidates.append("_".join(parts[:-1] + ["default"]))

    seen: set[str] = set()
    return [c for c in candidates if c != fixture and not (c in seen or seen.add(c))]


@dataclass
class ProviderRegistry:
    """Lazily-instantiated singletons, so a provider is constructed at most once."""

    _instances: dict[str, Provider] = field(default_factory=dict)

    def get(self, cls: type[Provider]) -> Provider:
        if cls.name not in self._instances:
            self._instances[cls.name] = cls()
        return self._instances[cls.name]

    def clear(self) -> None:
        self._instances.clear()


registry = ProviderRegistry()
