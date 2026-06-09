"""백필 안전장치 단위 테스트 (R19 트랙 E).

5 케이스 (DB 미사용 — 외부 의존 0):

1. record_run() 정상 종료 → status="ok" 1줄 append + counters/notes/backup_path 보존.
2. record_run() 예외 → status="error" + exc_message 기록 + raise 보존.
3. list_recent() 가 최신순 / limit 정확히 반영.
4. topic_backfill._validate_flags() 의 위험 조합 (RECLASSIFY + ¬PRESERVE + ¬DRY)
   에서 CONFIRM 없으면 SystemExit, CONFIRM=I_KNOW 면 통과.
5. dedup_voc._resolve_mode() 안전 기본값 검증
   (플래그 없음 → dry=True / --execute --yes → dry=False / --execute 단독 → SystemExit).

실행:
    cd crawler && /home/koopark/claude/SignalForge/.venv/bin/python \\
        -m pytest tests/test_backfill_safety.py -v
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from insight import backfill_audit  # noqa: E402


# ── 케이스 1 / 2 / 3 : backfill_audit 동작 ────────────────────────────────


@pytest.fixture()
def audit_tmp(tmp_path, monkeypatch):
    """BACKFILL_AUDIT_DIR 을 tmp 디렉토리로 강제."""
    monkeypatch.setenv("BACKFILL_AUDIT_DIR", str(tmp_path))
    return tmp_path


def _read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


def test_record_run_ok_writes_jsonl(audit_tmp: Path) -> None:
    """[1] 정상 종료 — status=ok, counters/notes/backup_path 직렬화."""
    with backfill_audit.record_run(
        script="topic_backfill",
        mode="dry_run",
        env={"DRY_RUN": True, "PRESERVE_EXISTING": True},
    ) as audit:
        audit.bump("seen", 12345)
        audit.bump("seen", 5)        # 누적
        audit.bump("updated", 0)     # 0 은 무시
        audit.bump("matched", 3000)
        audit.note("배치 1 완료")
        audit.note("배치 2 완료")
        audit.set_backup_path(audit_tmp / "topic_backup_x.json")

    path = audit_tmp / "backfill_audit.jsonl"
    assert path.exists(), "감사 로그 파일이 생성되어야 함"
    rows = _read_jsonl(path)
    assert len(rows) == 1
    r = rows[0]
    assert r["script"] == "topic_backfill"
    assert r["mode"] == "dry_run"
    assert r["status"] == "ok"
    assert r["counters"] == {"seen": 12350, "matched": 3000}, r["counters"]
    assert r["notes"] == ["배치 1 완료", "배치 2 완료"]
    assert r["backup_path"].endswith("topic_backup_x.json")
    assert r["env"]["DRY_RUN"] is True
    assert r["finished_at"] >= r["started_at"]


def test_record_run_exception_status_error(audit_tmp: Path) -> None:
    """[2] 컨텍스트 안에서 예외 → status=error + exc_message + raise 보존."""
    with pytest.raises(ValueError, match="boom"):
        with backfill_audit.record_run(
            script="sentiment_backfill",
            mode="dry_run",
            env={},
        ) as audit:
            audit.bump("seen", 100)
            raise ValueError("boom")

    rows = _read_jsonl(audit_tmp / "backfill_audit.jsonl")
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "error"
    assert "boom" in r["exc_message"]
    assert r["exc_message"].startswith("ValueError:")
    assert r["counters"]["seen"] == 100


def test_list_recent_orders_and_limits(audit_tmp: Path) -> None:
    """[3] 3회 실행 후 list_recent(limit=2) 가 최신 2개를 최신순으로 반환."""
    for name in ("a", "b", "c"):
        with backfill_audit.record_run(script=name, mode="dry_run", env={}) as au:
            au.bump("n", 1)

    out = backfill_audit.list_recent(limit=2)
    assert len(out) == 2
    # 최신순: c → b
    assert out[0]["script"] == "c"
    assert out[1]["script"] == "b"
    # limit=None 동등 (큰 값) → 전체 3
    out_all = backfill_audit.list_recent(limit=50)
    assert [r["script"] for r in out_all] == ["c", "b", "a"]


# ── 케이스 4 : topic_backfill._validate_flags ────────────────────────────


def test_topic_backfill_validate_flags_dangerous_combo(monkeypatch) -> None:
    """[4] RECLASSIFY + ¬PRESERVE + ¬DRY 위험 조합:
    CONFIRM 미설정 → SystemExit(3), CONFIRM=I_KNOW → 통과.
    """
    # 환경변수 명시적으로 세팅 후 모듈을 *재import* 해야 모듈 레벨 상수가 반영된다.
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("PRESERVE_EXISTING", "false")
    monkeypatch.setenv("TOPIC_RECLASSIFY_ALL", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://dummy")
    monkeypatch.delenv("CONFIRM", raising=False)

    # 매 호출마다 모듈 재로드 (sys.modules 에서 제거 후 import).
    sys.modules.pop("scripts.topic_backfill", None)
    import importlib
    mod = importlib.import_module("scripts.topic_backfill")
    importlib.reload(mod)

    # CONFIRM 없음 → exit(3)
    with pytest.raises(SystemExit) as ei:
        mod._validate_flags()
    assert ei.value.code == 3

    # CONFIRM=I_KNOW → 통과 (예외 없음)
    monkeypatch.setenv("CONFIRM", "I_KNOW")
    sys.modules.pop("scripts.topic_backfill", None)
    mod2 = importlib.import_module("scripts.topic_backfill")
    importlib.reload(mod2)
    mod2._validate_flags()  # 통과해야 함


# ── 케이스 5 : dedup_voc._resolve_mode CLI 안전 ───────────────────────────


def test_dedup_voc_resolve_mode_safety_defaults(monkeypatch) -> None:
    """[5] dedup_voc._resolve_mode():
      - 플래그 없음 → dry=True (안전 기본)
      - --execute 단독 → SystemExit (CONFIRM/--yes 필요)
      - --execute --yes → dry=False, backup=True (기본 켜짐)
      - --dry --backup → dry=True, backup=True
    """
    monkeypatch.delenv("CONFIRM", raising=False)
    monkeypatch.delenv("BACKUP_BEFORE", raising=False)
    sys.modules.pop("scripts.dedup_voc", None)
    import importlib
    mod = importlib.import_module("scripts.dedup_voc")

    def ns(**kw):
        d = {"dry": False, "execute": False, "backup": False, "yes": False}
        d.update(kw)
        return argparse.Namespace(**d)

    # 플래그 없음 → dry=True
    assert mod._resolve_mode(ns()) == (True, False)

    # --execute 단독 → SystemExit
    with pytest.raises(SystemExit) as ei:
        mod._resolve_mode(ns(execute=True))
    assert ei.value.code == 3

    # --execute --yes → dry=False, backup=True (기본 권장)
    dry, backup = mod._resolve_mode(ns(execute=True, yes=True))
    assert (dry, backup) == (False, True)

    # --dry --backup → dry=True, backup=True
    dry, backup = mod._resolve_mode(ns(dry=True, backup=True))
    assert (dry, backup) == (True, True)

    # CONFIRM=I_KNOW 도 --yes 와 동치
    monkeypatch.setenv("CONFIRM", "I_KNOW")
    dry, backup = mod._resolve_mode(ns(execute=True))
    assert dry is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
