"""외부 공유 토큰 검증 endpoint — _internal 가 아닌 외부 공개 router.

Track E3 의 후속:
- ``POST /api/v1/_internal/share-token`` (localhost only) 가 발급한 토큰을
- ``GET  /api/v1/shared/{token}`` 로 검증 — 외부에서 직접 호출 가능.

응답에는 만료 시각과 resource path 만 포함.  실제 데이터 조회는 frontend 가
``resource`` 를 사용해 정규 endpoint 를 호출하는 모델 (토큰은 link 검증용).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services import export_service

router = APIRouter(prefix="/shared", tags=["shared"])


@router.get("/{token}")
def resolve_token(token: str) -> dict:
    """공유 토큰 검증.

    응답::

        {"token": "...", "resource": "/insights",
         "expires_at": "2026-06-11T..."}

    만료/미존재 → 404.
    """
    rec = export_service.resolve_share_token(token)
    if not rec:
        raise HTTPException(status_code=404, detail="invalid or expired token")
    return rec
