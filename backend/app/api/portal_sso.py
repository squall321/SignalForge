"""HWAX Portal SSO callback — stateless single sign-on for SignalForge.

Flow: the user is logged into the HWAX portal and clicks the SignalForge tile.
The portal mints a short-lived RS256 "launch" JWT (aud = signalforge) and auto-POSTs
it here (form field 'token'). We:
  1. fetch the portal's JWKS (cached 300s) and verify the token
     (RS256, aud=PORTAL_AUDIENCE, scope=launch, require exp/aud/sub/jti, jti replay-guard),
  2. mint a stateless signed 'sf_session' cookie carrying {email, name} (NO DB),
  3. 303-redirect the browser into the app under /signalforge/ — already logged in.

Disabled (404) unless PORTAL_JWKS_URL is set, so standalone deploys are unaffected.
SignalForge has no local login / no User table — the signed cookie IS the session.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jose import jwt

from app.config import settings
from app.core.session import sign_session

router = APIRouter(prefix="/auth", tags=["portal-sso"])

# Tiny in-process JWKS cache + replay guard (single-process uvicorn). For multi-replica,
# back these with Redis — same seam as the rest of the app.
_jwks_cache: dict[str, Any] = {"keys": None, "fetched": 0.0}
_seen_jti: dict[str, float] = {}


async def _portal_jwks() -> list[dict[str, Any]]:
    now = time.time()
    if _jwks_cache["keys"] is not None and now - _jwks_cache["fetched"] < 300:
        return _jwks_cache["keys"]
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(settings.PORTAL_JWKS_URL)
        r.raise_for_status()
        keys = r.json().get("keys", [])
    _jwks_cache["keys"] = keys
    _jwks_cache["fetched"] = now
    return keys


def _gc_jti(now: float) -> None:
    for k, exp in list(_seen_jti.items()):
        if exp < now:
            del _seen_jti[k]


async def _verify_portal_token(token: str) -> dict[str, Any]:
    keys = await _portal_jwks()
    try:
        header = jwt.get_unverified_header(token)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="malformed launch token") from e
    key = next((k for k in keys if k.get("kid") == header.get("kid")), None) or (keys[0] if keys else None)
    if key is None:
        raise HTTPException(status_code=401, detail="portal JWKS has no usable key")
    try:
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=settings.PORTAL_AUDIENCE,
            options={"require": ["exp", "aud", "sub", "jti"]},
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="launch token rejected") from e
    if claims.get("scope") != "launch":
        raise HTTPException(status_code=401, detail="not a launch token")
    now = time.time()
    _gc_jti(now)
    jti = claims["jti"]
    if jti in _seen_jti:
        raise HTTPException(status_code=401, detail="launch token already used")
    _seen_jti[jti] = float(claims["exp"])
    return claims


@router.post("/portal-callback")
async def portal_callback(
    request: Request,
    token: str = Form(...),
) -> Response:
    if not settings.PORTAL_JWKS_URL:
        raise HTTPException(status_code=404, detail="portal SSO not enabled")

    claims = await _verify_portal_token(token)
    signed = sign_session({"email": claims.get("email"), "name": claims.get("name") or ""})

    resp = RedirectResponse(url=settings.PORTAL_SSO_LANDING, status_code=303)
    resp.set_cookie(
        key="sf_session",
        value=signed,
        httponly=True,
        secure=(settings.APP_ENV != "development"),
        samesite="lax",
        path=settings.SESSION_COOKIE_PATH,
        max_age=settings.SESSION_TTL_SECONDS,
    )
    return resp


@router.post("/logout", status_code=204)
async def logout(resp: Response) -> None:
    """세션 쿠키만 즉시 만료시킨다 — 인가도 본문도 요구하지 않는다.

    왜 필요한가. 포털에서 로그아웃해도 여기 세션이 남아 SignalForge 가 계속 열려 있었다.
    쿠키가 httpOnly 라 브라우저 JS 로는 못 지우고, 이 서비스에는 로그아웃 경로가 아예
    없었다(실측 401). 인가를 요구하지 않는 이유 — 이 동작은 '요청한 브라우저가 가진
    쿠키를 지운다' 뿐이라 남의 세션에 영향이 없고, 인가를 걸면 정작 쿠키만 있는
    브라우저가 못 쓴다.
    """
    resp.delete_cookie(key="sf_session", path=settings.SESSION_COOKIE_PATH)
