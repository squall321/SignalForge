"""app.core.cache.redis_cache 데코레이터 단위 테스트.

- Redis 미설치/연결실패 → 캐시 우회, 매 호출마다 원함수 실행.
- Redis 활성 (FakeRedis 주입) → 두 번째 호출은 GET 히트 → 원함수 미실행.
- BaseModel 응답 → model_dump 직렬화 후 SETEX, model_validate 로 hit 복원.

이 테스트는 pytest-asyncio 없이 asyncio.run 으로 직접 실행한다.
"""
from __future__ import annotations

import asyncio
import json
import unittest

from pydantic import BaseModel

from app.core import cache as cache_mod
from app.core.cache import redis_cache


class _SampleModel(BaseModel):
    name: str
    n: int


class _FakeRedis:
    """ttl 무시. 단순 GET/SETEX/ping 만 구현."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls = 0
        self.set_calls = 0

    def ping(self) -> bool:
        return True

    def get(self, key: str):
        self.get_calls += 1
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.set_calls += 1
        self.store[key] = value


class TestRedisCache(unittest.TestCase):
    def setUp(self) -> None:
        cache_mod._reset_for_test()

    def tearDown(self) -> None:
        cache_mod._reset_for_test()

    def test_passthrough_when_redis_unavailable(self) -> None:
        """Redis client None → 캐시 우회, 매번 원함수 실행."""
        cache_mod._reset_for_test()
        # 강제로 probed=True, client=None 설정
        cache_mod._redis_client = None
        cache_mod._redis_probed = True

        calls = {"n": 0}

        @redis_cache(ttl_seconds=60, key_prefix="ut:")
        async def fn(x: int) -> dict:
            calls["n"] += 1
            return {"x": x}

        async def go():
            r1 = await fn(1)
            r2 = await fn(1)
            return r1, r2

        r1, r2 = asyncio.run(go())
        self.assertEqual(r1, {"x": 1})
        self.assertEqual(r2, {"x": 1})
        self.assertEqual(calls["n"], 2, "Redis 비활성 시 매번 원함수 실행되어야 한다")

    def test_hit_returns_dict_when_no_model_cls(self) -> None:
        """두 번째 호출은 캐시 HIT — 원함수 미실행. model_cls 없으면 dict."""
        cache_mod._reset_for_test()
        fake = _FakeRedis()
        cache_mod._redis_client = fake
        cache_mod._redis_probed = True

        calls = {"n": 0}

        @redis_cache(ttl_seconds=60, key_prefix="ut:")
        async def fn(x: int) -> dict:
            calls["n"] += 1
            return {"x": x, "doubled": x * 2}

        async def go():
            return await fn(7), await fn(7)

        r1, r2 = asyncio.run(go())
        self.assertEqual(r1, {"x": 7, "doubled": 14})
        self.assertEqual(r2, {"x": 7, "doubled": 14})
        self.assertEqual(calls["n"], 1, "두 번째 호출은 HIT → 원함수 미실행")
        self.assertEqual(fake.set_calls, 1, "MISS 1회 → SETEX 1회")
        self.assertGreaterEqual(fake.get_calls, 2, "GET 은 매 호출마다 시도")

    def test_hit_restores_basemodel(self) -> None:
        """model_cls 지정 시 HIT 응답은 BaseModel 인스턴스로 복원."""
        cache_mod._reset_for_test()
        fake = _FakeRedis()
        cache_mod._redis_client = fake
        cache_mod._redis_probed = True

        @redis_cache(ttl_seconds=60, key_prefix="ut:", model_cls=_SampleModel)
        async def fn(name: str) -> _SampleModel:
            return _SampleModel(name=name, n=42)

        async def go():
            return await fn("a"), await fn("a")

        r1, r2 = asyncio.run(go())
        self.assertIsInstance(r1, _SampleModel)
        self.assertIsInstance(r2, _SampleModel)
        self.assertEqual(r2.name, "a")
        self.assertEqual(r2.n, 42)
        # SETEX 가 model_dump(mode='json') 형태로 저장됐는지 확인
        stored = list(fake.store.values())[0]
        self.assertEqual(json.loads(stored), {"name": "a", "n": 42})

    def test_key_differs_by_args(self) -> None:
        """인자가 다르면 키가 다르고 모두 MISS — 둘 다 원함수 실행.

        bound method 가 아닌 일반 함수 → skip_self=False 로 설정해야
        첫 인자가 키에 반영된다.
        """
        cache_mod._reset_for_test()
        fake = _FakeRedis()
        cache_mod._redis_client = fake
        cache_mod._redis_probed = True

        calls = {"n": 0}

        @redis_cache(ttl_seconds=60, key_prefix="ut:", skip_self=False)
        async def fn(x: int) -> dict:
            calls["n"] += 1
            return {"x": x}

        async def go():
            await fn(1)
            await fn(2)
            await fn(1)  # HIT
            await fn(2)  # HIT

        asyncio.run(go())
        self.assertEqual(calls["n"], 2, "서로 다른 키 2개 → MISS 2회, 그 후 HIT")
        self.assertEqual(len(fake.store), 2)

    def test_skip_self_on_method(self) -> None:
        """bound method 의 self 는 키 계산에서 제외 — 다른 인스턴스 간 캐시 공유."""
        cache_mod._reset_for_test()
        fake = _FakeRedis()
        cache_mod._redis_client = fake
        cache_mod._redis_probed = True

        calls = {"n": 0}

        class Svc:
            @redis_cache(ttl_seconds=60, key_prefix="ut:")
            async def m(self, k: str) -> dict:
                calls["n"] += 1
                return {"k": k}

        async def go():
            s1 = Svc()
            s2 = Svc()
            r1 = await s1.m("alpha")
            r2 = await s2.m("alpha")  # 다른 self 지만 같은 key → HIT
            return r1, r2

        r1, r2 = asyncio.run(go())
        self.assertEqual(r1, {"k": "alpha"})
        self.assertEqual(r2, {"k": "alpha"})
        self.assertEqual(calls["n"], 1, "self 제외 시 두 인스턴스 간 캐시 공유")

    def test_zero_ttl_passthrough(self) -> None:
        """ttl_seconds=0 → 캐시 우회."""
        cache_mod._reset_for_test()
        fake = _FakeRedis()
        cache_mod._redis_client = fake
        cache_mod._redis_probed = True

        calls = {"n": 0}

        @redis_cache(ttl_seconds=0, key_prefix="ut:")
        async def fn() -> dict:
            calls["n"] += 1
            return {"ok": True}

        async def go():
            await fn()
            await fn()

        asyncio.run(go())
        self.assertEqual(calls["n"], 2)
        self.assertEqual(fake.set_calls, 0, "ttl=0 이면 SETEX 호출 없음")


if __name__ == "__main__":
    unittest.main()
