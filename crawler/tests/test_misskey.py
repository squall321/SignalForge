"""Misskey collector 단위 테스트.

검증:
  1) note_to_rawvoc 가 평문 text 를 그대로 사용하고 메타에
     instance/query/note_id/user_host/username 을 채워 RawVOC 를 만든다.
  2) crawl() 이 모킹된 _fetch_query 응답을 모아 중복 url 을 제거하고
     MX 키워드 필터가 통과되는 항목만 반환한다 (MX 필터 import 실패해도 무해).
  3) HTTP 4xx 가 발생해도 다른 instance/query 는 계속 시도한다.

외부 네트워크 호출 없음 — _fetch_query 를 monkeypatch.

실행:
  cd crawler && python -m pytest tests/test_misskey.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms import misskey as misskey_mod
from platforms.misskey import (
    INSTANCES,
    MisskeyCrawler,
    QUERIES,
    note_to_rawvoc,
)


def _mk_note(nid: str, text: str, username: str = "alice", host=None) -> dict:
    return {
        "id": nid,
        "createdAt": "2026-06-09T10:05:14.339Z",
        "user": {
            "id": "u_" + nid,
            "username": username,
            "name": f"Display {username}",
            "host": host,
        },
        "text": text,
        "renoteCount": 2,
        "repliesCount": 1,
        "reactionCount": 5,
    }


# --------------------------------------------------------------------------
# 1) note_to_rawvoc 단위 검증
# --------------------------------------------------------------------------

def test_note_to_rawvoc_basic():
    nt = _mk_note("ana1ft", "Galaxy Fold3 を 65,000 円 で売却 完了")
    voc = note_to_rawvoc(nt, "misskey.io", "Galaxy Fold")
    assert voc is not None
    # 평문 그대로 보존
    assert "Galaxy Fold3" in voc.content
    # url 합성
    assert voc.source_url == "https://misskey.io/notes/ana1ft"
    # 메타·통계 채워졌는지
    assert voc.meta["instance"] == "misskey.io"
    assert voc.meta["query"] == "Galaxy Fold"
    assert voc.meta["note_id"] == "ana1ft"
    assert voc.meta["username"] == "alice"
    assert voc.meta["user_host"] is None
    assert voc.likes_count == 5
    assert voc.comments_count == 1
    assert voc.shares_count == 2
    assert voc.author_name == "Display alice"
    assert voc.published_at is not None
    assert voc.published_at.year == 2026
    # external_id 는 md5 16 hex
    assert len(voc.external_id) == 16


def test_note_to_rawvoc_skips_empty_or_missing_id():
    """text 가 공백이거나 id 가 없으면 None."""
    assert note_to_rawvoc(_mk_note("111", ""), "misskey.io", "galaxy") is None
    assert note_to_rawvoc(_mk_note("111", "   "), "misskey.io", "galaxy") is None
    bad = _mk_note("111", "Galaxy S25 sample")
    bad["id"] = ""
    assert note_to_rawvoc(bad, "misskey.io", "galaxy") is None


def test_note_to_rawvoc_instance_id_isolation():
    """동일 nid 라도 instance 가 다르면 external_id 가 달라야 한다 (인스턴스 간 dedup 회피)."""
    nt = _mk_note("same_id", "Galaxy Note camera review")
    v1 = note_to_rawvoc(nt, "misskey.io", "galaxy")
    v2 = note_to_rawvoc(nt, "misskey.design", "galaxy")
    assert v1 is not None and v2 is not None
    assert v1.external_id != v2.external_id
    # source_url 도 인스턴스별로 분리
    assert v1.source_url != v2.source_url


# --------------------------------------------------------------------------
# 2) crawl() — fan-out + dedup + MX filter
# --------------------------------------------------------------------------

def test_crawl_fanout_and_dedup(monkeypatch):
    monkeypatch.setattr(misskey_mod, "INSTANCES", ["misskey.io"])
    monkeypatch.setattr(misskey_mod, "QUERIES", ["galaxy", "samsung"])

    async def _no_delay(self):
        return None

    monkeypatch.setattr(MisskeyCrawler, "_random_delay", _no_delay)

    galaxy_payload = [
        _mk_note("aa1", "Galaxy S25 Ultra camera review impressive"),
        _mk_note("dup", "Galaxy Z Fold7 hinge feels solid"),  # source_url 키로 dedup
    ]
    samsung_payload = [
        # 같은 nid="dup" → 같은 url → dedup 으로 제거되어야 함
        _mk_note("dup", "Samsung One UI 7 gestures preview"),
        _mk_note("nomx", "Just had pizza for lunch today"),  # mx 무관
    ]

    async def fake_fetch(self, client, instance, query):
        if query == "galaxy":
            return galaxy_payload
        if query == "samsung":
            return samsung_payload
        return []

    monkeypatch.setattr(MisskeyCrawler, "_fetch_query", fake_fetch)

    crawler = MisskeyCrawler()
    vocs = asyncio.run(crawler.crawl())

    urls = sorted(v.source_url for v in vocs)
    assert "https://misskey.io/notes/aa1" in urls
    assert "https://misskey.io/notes/dup" in urls
    # MX 매칭 2건은 반드시 (필터 import 실패해도 무해 — graceful)
    assert len(vocs) >= 2
    assert all(
        ("Galaxy" in v.content or "Samsung" in v.content) for v in vocs
    )

    # stats per_query 기록 확인
    assert crawler.stats["per_query"].get("misskey.io/galaxy") == 2
    # samsung query 는 dup 이 dedup 되어 신규는 nomx 만 1건
    assert crawler.stats["per_query"].get("misskey.io/samsung") == 1


# --------------------------------------------------------------------------
# 3) HTTP 4xx 발생 → 다른 query 는 계속 시도
# --------------------------------------------------------------------------

def test_crawl_handles_http_error(monkeypatch):
    monkeypatch.setattr(misskey_mod, "INSTANCES", ["misskey.io"])
    monkeypatch.setattr(misskey_mod, "QUERIES", ["galaxy", "samsung"])

    async def _no_delay(self):
        return None

    monkeypatch.setattr(MisskeyCrawler, "_random_delay", _no_delay)

    async def fake_fetch(self, client, instance, query):
        if query == "galaxy":
            req = httpx.Request("POST", f"https://{instance}/api/notes/search")
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("429 rate limit", request=req, response=resp)
        return [_mk_note("ok1", "Samsung Galaxy S25 quick take")]

    monkeypatch.setattr(MisskeyCrawler, "_fetch_query", fake_fetch)

    crawler = MisskeyCrawler()
    vocs = asyncio.run(crawler.crawl())

    assert any("galaxy" in b for b in crawler.stats["blocked"])
    assert len(vocs) == 1
    assert "Galaxy S25" in vocs[0].content
