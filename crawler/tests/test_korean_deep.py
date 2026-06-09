"""korean_pagination_deep.py 단위 테스트 (트랙 B).

DB / 네트워크 의존 0 — 핵심 안전장치 1 케이스:

1. _selected_sites + BACKFILL_PAGES → 사이트별 LIST_PAGES env 가 import 시점에
   정확히 BACKFILL_PAGES 값으로 set 되는지 + DRY_RUN=1 분기에서 crawler 가 실행되지
   않고 audit JSONL 에 baseline 만 1줄 추가되는지 확인.

실행:
    cd crawler && /home/koopark/claude/SignalForge/.venv/bin/python \\
        -m pytest tests/test_korean_deep.py -v
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock


HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)


def test_dry_run_sets_backfill_env_and_writes_audit_only(tmp_path: Path, monkeypatch):
    """BACKFILL_PAGES=37 + DRY_RUN=1 →
       1) 모든 사이트의 *_BACKFILL_PAGES env 가 '37' 로 set
       2) crawler.run() 미호출 (mocked)
       3) audit JSONL 에 start + dry_run_end 2 줄 기록
    """
    audit = tmp_path / "audit.jsonl"

    # 깨끗한 env 로 시작 (이전 테스트/실행 잔재 제거)
    for k in (
        "CLIEN_BACKFILL_PAGES", "DCINSIDE_BACKFILL_PAGES", "PPOMPPU_BACKFILL_PAGES",
        "FMKOREA_BACKFILL_PAGES", "DOGDRIP_BACKFILL_PAGES",
        "CLIEN_MAX_POSTS", "DCINSIDE_MAX_POSTS", "PPOMPPU_MAX_POSTS",
        "FMKOREA_MAX_POSTS", "DOGDRIP_MAX_POSTS",
        "BACKFILL_PAGES", "BACKFILL_MAX_POSTS",
    ):
        monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("BACKFILL_PAGES", "37")
    monkeypatch.setenv("BACKFILL_MAX_POSTS", "111")
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
    monkeypatch.setenv("AUDIT_PATH", str(audit))
    monkeypatch.setenv("ROUND", "test-R28")

    # _site_counts 는 psql 호출 → 네트워크/DB 의존이라 stub
    def _fake_counts(code: str) -> dict:
        return {"total": "0", "old_90d": "0"}

    # 깊이 스크립트는 import 시점에 env 를 set 하므로 reload 필요
    if "scripts.korean_pagination_deep" in sys.modules:
        del sys.modules["scripts.korean_pagination_deep"]
    mod = importlib.import_module("scripts.korean_pagination_deep")

    # 1) import 직후 사이트별 env 가 '37' 로 set 되었는지
    for site in ("CLIEN", "DCINSIDE", "PPOMPPU", "FMKOREA", "DOGDRIP"):
        assert os.environ.get(f"{site}_BACKFILL_PAGES") == "37", \
            f"{site}_BACKFILL_PAGES 미설정 — import 시 env propagation 실패"
        assert os.environ.get(f"{site}_MAX_POSTS") == "111"

    # 2) DRY_RUN → crawler 인스턴스가 .run() 을 호출하면 안 됨
    run_called: list = []

    class _StubCrawler:
        def __init__(self, *a, **kw):
            pass

        async def run(self):
            run_called.append("YES")
            return {"items_collected": 0}

    fake_sites = [("clien", _StubCrawler), ("dcinside", _StubCrawler)]
    with mock.patch.object(mod, "_load_crawlers", return_value=fake_sites), \
         mock.patch.object(mod, "_site_counts", side_effect=_fake_counts):
        mod.main()

    assert run_called == [], "DRY_RUN=1 인데 crawler.run() 이 호출됨"

    # 3) audit JSONL — Harvest3 트랙 P2: start + end (status=ok) 2 줄
    # (이전: start + dry_run_end. audit_round 가 종료 시 end 를 자동 보장하여
    #  verify D "harvest2 audit end 부재" 결함이 영구 해결됨.)
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, f"audit 줄 수 기대 2, 실제 {len(lines)}: {lines}"
    events = [json.loads(ln) for ln in lines]
    assert events[0]["event"] == "start"
    assert events[0]["pages"] == 37
    assert events[0]["max_posts"] == 111
    assert events[0]["dry_run"] is True
    assert events[0]["round"] == "test-R28"
    assert events[1]["event"] == "end"
    assert events[1]["status"] == "ok"
    # start 와 end 가 동일 run_id 로 페어링되어야 한다.
    assert events[0]["run_id"] == events[1]["run_id"]
