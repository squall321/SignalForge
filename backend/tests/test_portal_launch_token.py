# 포털 진입 토큰(scope=launch) 검증이 필수 클레임을 실제로 요구하는지 — python-jose 가 "require": [...] 를 말없이 버리던 결함(10-18)
"""왜 — `_verify_portal_token` 은 options={"require": ["exp","aud","sub","jti"]} 로 필수를 걸었다고 적었지만 그건 PyJWT 꼴이고
python-jose 3.5 는 그 키를 모른 채 버린다. 그래서 aud 가 없는 토큰은 청중 확인을 건너뛰었고, jti · exp 가 없는 토큰은 재사용 막기에서
KeyError 로 500 이 났다. HEAXHub 에서 같은 꼴을 고치다 찾았다(HWAXPortal docs/followups-1017 FU-D19).

  실행:  .venv/bin/python -m pytest tests/test_portal_launch_token.py -q
"""
import asyncio
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jose import jwk, jwt

from app.api import portal_sso
from app.config import settings

KID = "portal-test-kid"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIV = _KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
PUB = _KEY.public_key().public_bytes(serialization.Encoding.PEM,
                                     serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@pytest.fixture(autouse=True)
def _portal(monkeypatch):
    async def _jwks():
        return [{**jwk.construct(PUB, "RS256").to_dict(), "kid": KID}]
    monkeypatch.setattr(portal_sso, "_portal_jwks", _jwks)
    monkeypatch.setattr(portal_sso, "_seen_jti", {})


def _launch(**over) -> str:
    now = int(time.time())
    claims = {"sub": "me@example.com", "email": "me@example.com", "aud": settings.PORTAL_AUDIENCE, "scope": "launch",
              "iat": now, "nbf": now, "exp": now + 90, "jti": uuid.uuid4().hex}
    claims.update(over)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, PRIV, algorithm="RS256", headers={"kid": KID})


def test_정상_진입_토큰은_통과하고_두_번은_못_쓴다():
    tok = _launch()
    assert asyncio.run(portal_sso._verify_portal_token(tok))["sub"] == "me@example.com"
    with pytest.raises(HTTPException) as e:
        asyncio.run(portal_sso._verify_portal_token(tok))
    assert e.value.status_code == 401


@pytest.mark.parametrize("missing", ["aud", "jti", "exp", "sub"])
def test_필수_클레임이_없으면_401(missing):
    with pytest.raises(HTTPException) as e:
        asyncio.run(portal_sso._verify_portal_token(_launch(**{missing: None})))
    assert e.value.status_code == 401, f"{missing} 없는 토큰 — 500 이나 통과가 아니라 401"
