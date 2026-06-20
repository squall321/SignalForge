"""Stateless signed-session cookie helpers (HWAX Portal SSO).

The portal-callback verifies the portal launch JWT, then mints a self-contained
signed cookie 'sf_session' carrying {email, name, iat}. There is NO DB and no
server-side session store — the cookie itself is the session, signed with
SF_SESSION_SECRET via itsdangerous.TimestampSigner (TTL = SESSION_TTL_SECONDS).

Used by portal_sso.py (sign on callback) and the main.py HTTP gate (verify).
"""
from __future__ import annotations

import json
import time
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from app.config import settings


def _signer() -> TimestampSigner:
    return TimestampSigner(settings.SF_SESSION_SECRET)


def sign_session(claims: dict) -> str:
    payload = {
        "email": claims.get("email"),
        "name": claims.get("name"),
        "iat": int(time.time()),
    }
    return _signer().sign(json.dumps(payload).encode()).decode()


def verify_session(raw: str) -> Optional[dict]:
    try:
        data = _signer().unsign(raw, max_age=settings.SESSION_TTL_SECONDS)
        return json.loads(data)
    except (BadSignature, SignatureExpired, json.JSONDecodeError, ValueError):
        return None
