"""Identity, RBAC and zero-trust request authentication.

* prod: OIDC (Keycloak / Azure AD / any IdP) — RS256/ES256 JWTs verified against the issuer's JWKS
* dev:  HS256 tokens minted by `jyotiveda token --role DISCOM_OPERATOR`
Every request, human or service, carries a verified principal; permissions are checked per route.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jyotiveda.config import Settings, get_settings


class Role(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    DISCOM_ADMIN = "DISCOM_ADMIN"
    DISCOM_OPERATOR = "DISCOM_OPERATOR"
    URJA_SAKHI = "URJA_SAKHI"
    FIELD_ENGINEER = "FIELD_ENGINEER"
    RESIDENT = "RESIDENT"
    ANALYST = "ANALYST"
    SERVICE = "SERVICE"  # workload identity (control loop, edge sync)


class Perm(StrEnum):
    READ_GRID = "grid:read"
    READ_HOUSEHOLD = "household:read"
    RUN_SIMULATION = "simulation:run"
    PROPOSE_DISPATCH = "dispatch:propose"
    APPROVE_DISPATCH = "dispatch:approve"
    EMERGENCY_MODE = "dispatch:emergency"
    OFFER_FLEX = "flex:offer"
    CLEAR_MARKET = "flex:clear"
    READ_AUDIT = "audit:read"
    USE_COPILOT = "copilot:use"
    MANAGE_ASSETS = "assets:manage"


ROLE_PERMS: dict[Role, set[Perm]] = {
    Role.SUPER_ADMIN: set(Perm),
    Role.DISCOM_ADMIN: set(Perm) - {Perm.OFFER_FLEX},
    Role.DISCOM_OPERATOR: {
        Perm.READ_GRID,
        Perm.RUN_SIMULATION,
        Perm.PROPOSE_DISPATCH,
        Perm.APPROVE_DISPATCH,
        Perm.CLEAR_MARKET,
        Perm.READ_AUDIT,
        Perm.USE_COPILOT,
        Perm.READ_HOUSEHOLD,
    },
    Role.URJA_SAKHI: {Perm.READ_GRID, Perm.EMERGENCY_MODE, Perm.USE_COPILOT, Perm.READ_HOUSEHOLD},
    Role.FIELD_ENGINEER: {Perm.READ_GRID, Perm.MANAGE_ASSETS, Perm.READ_AUDIT},
    Role.RESIDENT: {Perm.OFFER_FLEX, Perm.USE_COPILOT},
    Role.ANALYST: {Perm.READ_GRID, Perm.RUN_SIMULATION, Perm.READ_AUDIT},
    Role.SERVICE: {Perm.READ_GRID, Perm.PROPOSE_DISPATCH, Perm.CLEAR_MARKET, Perm.RUN_SIMULATION},
}


@dataclass(frozen=True)
class Principal:
    sub: str
    role: Role
    transformers: tuple[str, ...] = ()  # ABAC scope; empty = all (DISCOM-level roles)
    household_id: str | None = None

    def can(self, perm: Perm) -> bool:
        return perm in ROLE_PERMS.get(self.role, set())

    def in_scope(self, transformer_id: str) -> bool:
        return not self.transformers or transformer_id in self.transformers


def mint_dev_token(
    sub: str,
    role: Role,
    settings: Settings | None = None,
    hours: int = 12,
    transformers: list[str] | None = None,
    household_id: str | None = None,
) -> str:
    s = settings or get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": sub,
            "role": role.value,
            "dts": transformers or [],
            "hh": household_id,
            "iat": now,
            "exp": now + timedelta(hours=hours),
            "aud": s.oidc_audience,
            "iss": "jyotiveda-dev",
        },
        s.dev_jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:  # pragma: no cover - network
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def decode_token(token: str, s: Settings) -> Principal:
    try:
        if s.auth_mode == "oidc":  # pragma: no cover - needs an IdP
            key = _jwks_client(
                s.oidc_jwks_url or f"{s.oidc_issuer}/protocol/openid-connect/certs"
            ).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256", "ES256"], audience=s.oidc_audience, issuer=s.oidc_issuer
            )
            role = next(
                (Role(r) for r in claims.get("realm_access", {}).get("roles", []) if r in Role.__members__),
                Role.RESIDENT,
            )
        else:
            claims = jwt.decode(
                token, s.dev_jwt_secret.get_secret_value(), algorithms=["HS256"], audience=s.oidc_audience
            )
            role = Role(claims["role"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
    return Principal(
        sub=claims["sub"],
        role=role,
        transformers=tuple(claims.get("dts") or ()),
        household_id=claims.get("hh"),
    )


bearer = HTTPBearer(auto_error=False)


async def current_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    s: Settings = Depends(get_settings),
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    p = decode_token(creds.credentials, s)
    request.state.principal = p
    return p


def require(perm: Perm):
    async def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.can(perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"{p.role} lacks {perm}")
        return p

    return dep
