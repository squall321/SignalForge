"""Redis 캐시 데코레이터 (트랙 B 성능 안정화).

`@redis_cache(ttl_seconds=300, key_prefix='analytics:')` 형태로 async 서비스
메서드에 부착하여 두 번째 호출부터 Redis 에서 직접 응답을 돌려준다.

설계 규칙:
- async 함수 전용. sync 함수는 dec 적용 시 그대로 패스스루.
- Redis 미설치/연결실패 → 캐시 우회 (원함수 실행). 절대로 예외 전파하지 않음.
- 키 생성: f"{key_prefix}{func.__qualname__}:{sha1(json(args, kwargs))}".
  - bound method 인 경우 self 는 제외 (메서드 의존성은 함수명에 이미 반영).
- 직렬화:
  - 결과가 Pydantic BaseModel → .model_dump() (mode='json') → json.dumps.
  - dict / list / primitive → json.dumps (default=str).
  - 그 외 → 캐시 skip (로그 후 원함수 결과 그대로 반환).
- 역직렬화: 원함수 반환 타입을 알 수 없으므로 dict/list 로 복원.
  서비스 메서드가 BaseModel 을 반환한다면 모델 클래스를 `model_cls` 로 명시.

로그:
- HIT  : `cache HIT key=... took=...ms`
- MISS : `cache MISS key=... ttl=...`
- ERR  : `cache ERR key=... err=...` (단, 원함수는 정상 실행)

환경:
- `REDIS_URL` 우선, 없으면 host=127.0.0.1 port=6379 default db=0.
- ping 2s timeout. 실패하면 client=None 으로 캐시 영구 우회.
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import os
import time
from typing import Any, Callable, Optional, Type

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# Redis 클라이언트 (모듈 전역 single-instance, lazy)
# ────────────────────────────────────────────────────────────
_redis_client: Any = None
_redis_probed: bool = False

# Redis hit/miss 카운터 키 (모든 redis_cache 데코레이터가 공유)
_HIT_KEY = "sf:cache:hits"
_MISS_KEY = "sf:cache:misses"


def _get_redis():
    """싱글톤 Redis 클라이언트. ping 실패 시 None 반환 후 더 이상 시도하지 않음."""
    global _redis_client, _redis_probed
    if _redis_probed:
        return _redis_client
    _redis_probed = True
    try:
        import redis  # type: ignore

        url = os.getenv("REDIS_URL", "").strip()
        if url:
            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
        else:
            client = redis.Redis(
                host=os.getenv("REDIS_HOST", "127.0.0.1"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD") or None,
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
        client.ping()
        _redis_client = client
        logger.info("redis_cache 활성 (url=%s)", url or "127.0.0.1:6379")
    except Exception as e:  # pragma: no cover - 의존성 미설치/네트워크 실패
        logger.warning("redis_cache 비활성 (캐시 우회): %s", e)
        _redis_client = None
    return _redis_client


def _reset_for_test() -> None:
    """테스트용. 모듈 전역 캐시 클라이언트 상태 초기화."""
    global _redis_client, _redis_probed
    _redis_client = None
    _redis_probed = False


# ────────────────────────────────────────────────────────────
# 직렬화 helper
# ────────────────────────────────────────────────────────────
def _serialize_args(args: tuple, kwargs: dict) -> str:
    """args/kwargs 의 안정적 해시 키 (self 는 호출 시점에서 이미 제거된 상태)."""
    try:
        payload = json.dumps([args, sorted(kwargs.items())], default=str, sort_keys=True)
    except (TypeError, ValueError):
        payload = repr((args, sorted(kwargs.items())))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _dump_result(result: Any) -> Optional[str]:
    """결과 → JSON 문자열. 직렬화 불가 시 None."""
    try:
        # pydantic v2 BaseModel 지원
        if hasattr(result, "model_dump"):
            return json.dumps(result.model_dump(mode="json"), default=str)
        # dataclasses 등 그 외 dict-able
        return json.dumps(result, default=str)
    except (TypeError, ValueError) as e:
        logger.debug("cache 직렬화 실패: %s", e)
        return None


def _load_result(raw: str, model_cls: Optional[Type[Any]]) -> Any:
    """JSON → dict/list. model_cls 가 있으면 Pydantic 인스턴스로 복원."""
    obj = json.loads(raw)
    if model_cls is not None and hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(obj)
    return obj


# ────────────────────────────────────────────────────────────
# 데코레이터
# ────────────────────────────────────────────────────────────
def redis_cache(
    ttl_seconds: int = 300,
    key_prefix: str = "sf:",
    model_cls: Optional[Type[Any]] = None,
    skip_self: bool = True,
):
    """Async 메서드/함수 캐시 데코레이터.

    Args:
        ttl_seconds: 캐시 TTL (초). 0 이면 캐시하지 않음 (passthrough).
        key_prefix: Redis 키 prefix. (예: 'analytics:')
        model_cls: 반환 모델 클래스 (Pydantic BaseModel). 명시 시 hit 응답을
                   원본 타입으로 복원. 미명시면 dict/list 로 응답.
        skip_self: bound method 의 첫 인자 (self) 를 키 계산에서 제외.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(func):
            # sync 함수는 그대로. (이 프로젝트는 async 서비스가 대상)
            logger.debug("redis_cache: sync 함수는 passthrough — %s", func.__qualname__)
            return func

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if ttl_seconds <= 0:
                return await func(*args, **kwargs)

            client = _get_redis()
            if client is None:
                # Redis 비활성 — 캐시 우회
                return await func(*args, **kwargs)

            key_args = args[1:] if (skip_self and args) else args
            digest = _serialize_args(key_args, kwargs)
            key = f"{key_prefix}{func.__qualname__}:{digest}"

            # GET
            try:
                t0 = time.perf_counter()
                raw = client.get(key)
                if raw is not None:
                    took_ms = (time.perf_counter() - t0) * 1000.0
                    logger.info("cache HIT  key=%s took=%.1fms", key, took_ms)
                    try:
                        result = _load_result(raw, model_cls)
                        # 카운터 INCR — 실패해도 본 흐름엔 영향 없음
                        try:
                            client.incr(_HIT_KEY)
                        except Exception:
                            pass
                        return result
                    except Exception as e:
                        logger.warning("cache 역직렬화 실패 (재계산): %s", e)
            except Exception as e:
                logger.warning("cache ERR (GET) key=%s err=%s", key, e)

            # MISS
            logger.info("cache MISS key=%s ttl=%ds", key, ttl_seconds)
            try:
                client.incr(_MISS_KEY)
            except Exception:
                pass
            result = await func(*args, **kwargs)

            # SET
            try:
                dumped = _dump_result(result)
                if dumped is not None:
                    client.setex(key, ttl_seconds, dumped)
            except Exception as e:
                logger.warning("cache ERR (SET) key=%s err=%s", key, e)

            return result

        return wrapper

    return decorator


def get_cache_stats() -> dict:
    """현재 누적 hit/miss/ratio 반환. Redis 비활성 시 ratio=None.

    카운터는 Redis INCR 로 누적 — 워커 재시작 후에도 보존되며,
    수동 리셋이 필요한 경우 `reset_cache_stats()` 호출.
    """
    client = _get_redis()
    if client is None:
        return {"hits": 0, "misses": 0, "ratio": None, "enabled": False}
    try:
        hits = int(client.get(_HIT_KEY) or 0)
        misses = int(client.get(_MISS_KEY) or 0)
    except Exception as e:
        return {"hits": 0, "misses": 0, "ratio": None, "enabled": False, "error": str(e)}
    total = hits + misses
    ratio = round(hits / total, 4) if total > 0 else None
    return {"hits": hits, "misses": misses, "ratio": ratio, "enabled": True}


def reset_cache_stats() -> None:
    """카운터 리셋 (테스트/운영 수동 사용)."""
    client = _get_redis()
    if client is None:
        return
    try:
        client.delete(_HIT_KEY, _MISS_KEY)
    except Exception:
        pass


__all__ = [
    "redis_cache",
    "_reset_for_test",
    "_get_redis",
    "get_cache_stats",
    "reset_cache_stats",
]
