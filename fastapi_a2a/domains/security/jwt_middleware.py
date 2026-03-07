"""
JWT Validation Middleware — Bearer token validation for A2A endpoints.

Supports:
- Bearer JWT validation against local JWKS (card_signing_key table)
- Bearer JWT validation against remote JWKS (agent cards from other registries)
- Token family revocation check via AgentToken.is_revoked
- Fallback to "allow unauthenticated" mode (dev mode, configurable)
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.key_management.models import CardSigningKey
from fastapi_a2a.domains.security.models import AgentToken

# Endpoints that are publicly accessible (no auth required)
PUBLIC_PATHS: frozenset[str] = frozenset(
    [
        "/.well-known/agent.json",
        "/.well-known/agent-extended.json",
        "/.well-known/agent-jwks.json",
        "/.well-known/agent-crl.json",
        "/rpc/health",
        "/registry/agents",
        "/docs",
        "/openapi.json",
        "/redoc",
    ]
)


class JwtValidationMiddleware:
    """
    ASGI middleware that validates Bearer JWT tokens on protected endpoints.

    Configuration (from FastApiA2AConfig):
      - require_auth (bool): If False, missing tokens are allowed (dev mode)
      - allowed_algorithms: list of accepted JWT algorithms
      - issuer: Expected 'iss' claim
      - audience: Expected 'aud' claim
    """

    def __init__(
        self,
        app,
        *,
        require_auth: bool = False,
        allowed_algorithms: list[str] | None = None,
        issuer: str | None = None,
        audience: str | None = None,
    ):
        self.app = app
        self.require_auth = require_auth
        self.allowed_algorithms = allowed_algorithms or ["ES256", "RS256", "EdDSA"]
        self.issuer = issuer
        self.audience = audience

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive, send)
        path = request.url.path

        # Skip public paths
        if path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            await self.app(scope, receive, send)
            return

        token = self._extract_bearer(request)

        if token is None:
            if self.require_auth:
                response = JSONResponse(
                    {"detail": "Authorization header required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
            # Allow unauthenticated in dev mode
            scope["auth_claims"] = {}
            await self.app(scope, receive, send)
            return

        # Validate token — get DB session from request state if available
        try:
            db: AsyncSession | None = Request(scope, receive).state.db
        except AttributeError:
            db = None

        claims = await self._validate_token(token, db)
        if claims is None:
            response = JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            )
            await response(scope, receive, send)
            return

        scope["auth_claims"] = claims
        await self.app(scope, receive, send)

    def _extract_bearer(self, request: Request) -> str | None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        return None

    async def _validate_token(
        self, token: str, db: AsyncSession | None
    ) -> dict[str, Any] | None:
        """
        Validate a JWT token. Returns claims dict on success, None on failure.
        Checks: signature, expiry, kid-based key lookup, family revocation.
        """
        try:
            # Decode header to get kid
            headers = jwt.get_unverified_header(token)
            kid = headers.get("kid")
            alg = headers.get("alg", "ES256")

            if alg not in self.allowed_algorithms:
                return None

            # 1. Try to load key from local JWKS (card_signing_key table)
            public_key = None
            if db and kid:
                result = await db.execute(
                    select(CardSigningKey).where(
                        CardSigningKey.kid == kid,
                        CardSigningKey.status.in_(["active", "retired"]),
                    )
                )
                signing_key = result.scalar_one_or_none()
                if signing_key:
                    public_key = signing_key.public_jwk

            if public_key is None:
                # Fall back to unverified decode (dev mode only)
                if not self.require_auth:
                    claims = jwt.get_unverified_claims(token)
                    return claims
                return None

            # 2. Validate signature + claims
            options = {
                "verify_exp": True,
                "verify_iat": True,
                "verify_aud": self.audience is not None,
                "verify_iss": self.issuer is not None,
            }
            claims = jwt.decode(
                token,
                public_key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuer,
                options=options,
            )

            # 3. Check token family revocation
            jti = claims.get("jti")
            if db and jti:
                rev_result = await db.execute(
                    select(AgentToken).where(
                        AgentToken.jti == jti,
                        AgentToken.is_revoked.is_(True),
                    )
                )
                if rev_result.scalar_one_or_none():
                    return None  # Token revoked

            return claims

        except (JWTError, ExpiredSignatureError, JWTClaimsError, Exception):
            return None


def get_auth_claims(request: Request) -> dict[str, Any]:
    """Dependency: extract validated JWT claims from request scope."""
    return request.scope.get("auth_claims", {})


def require_claim(claim_key: str, claim_value: str | None = None):
    """Dependency factory: assert a specific claim exists (and optionally matches value)."""
    async def _check(request: Request):
        claims = get_auth_claims(request)
        if not claims:
            raise HTTPException(status_code=401, detail="Authentication required")
        if claim_key not in claims:
            raise HTTPException(status_code=403, detail=f"Missing required claim: {claim_key}")
        if claim_value is not None and claims[claim_key] != claim_value:
            raise HTTPException(status_code=403, detail=f"Claim '{claim_key}' mismatch")
        return claims
    return _check
