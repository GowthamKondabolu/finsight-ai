"""Authentication boundary for non-public FinSight API routes."""

from __future__ import annotations

import hmac

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class ApiAuthorizationMiddleware:
    """Require one deployment-managed bearer token for every versioned route."""

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope["path"]).startswith("/v1/"):
            await self.app(scope, receive, send)
            return

        if self.token is None:
            await self.app(scope, receive, send)
            return

        authorization = Headers(scope=scope).get("Authorization")
        expected = f"Bearer {self.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            response = JSONResponse(
                {"detail": "authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
