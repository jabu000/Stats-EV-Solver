"""Optional password gate for deployed instances.

Locally this is off and should stay off. But a deployment has a public URL, and the
default posture of "anyone with the link sees the board and can write to Settings" is
wrong for something holding a bearer token and a betting record. Setting
`ACCESS_PASSWORD` turns on HTTP Basic across the whole app.

This is a lock on a door, not a security system: one shared password, no accounts, no
sessions. It is proportionate to a single-user tool behind a URL nobody has been given.
"""

from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

#: Reachable without credentials, so a platform health check does not need them.
OPEN_PATHS = frozenset({"/api/health"})


def _unauthorized() -> Response:
    return JSONResponse(
        {"detail": "Authentication required."},
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Stats EV Solver"'},
    )


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Require HTTP Basic on everything except the health check.

    Any username is accepted; only the password is checked, and it is compared with a
    constant-time comparison so the check cannot be timed character by character.
    """

    def __init__(self, app, password: str) -> None:
        super().__init__(app)
        self._password = password

    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            return _unauthorized()

        import base64
        import binascii

        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return _unauthorized()

        _, _, supplied = decoded.partition(":")
        if not secrets.compare_digest(supplied, self._password):
            return _unauthorized()

        return await call_next(request)
