"""R24 트랙 E — audit JSONL round 라벨링 단위 테스트.

검증 항목
~~~~~~~~~
1. ``ROUND`` 환경변수 미설정 시 ``env.round == 'unlabeled'`` 자동 주입.
2. ``ROUND=R24`` 설정 시 ``env.round == 'R24'`` 주입 + monitor.by_round 집계.

DB/네트워크 의존성 없음 — backfill_audit.record_run() 컨텍스트와
backfill_audit_monitor.summarize() 의 순수 로직만 검증한다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.backfill_audit import record_run  # noqa: E402
from insight.backfill_audit_monitor import summarize  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_round_default_unlabeled(tmp_path, monkeypatch):
    """ROUND 환경변수 미설정 시 audit JSONL 의 env.round == 'unlabeled'."""
    monkeypatch.setenv("BACKFILL_AUDIT_DIR", str(tmp_path))
    monkeypatch.delenv("ROUND", raising=False)

    with record_run(
        script="topic_backfill",
        mode="dry_run",
        env={"DRY_RUN": True, "PRESERVE_EXISTING": True},
    ) as audit:
        audit.note("default-round-test")

    rows = _read_jsonl(tmp_path / "backfill_audit.jsonl")
    assert len(rows) == 1
    assert rows[0]["env"]["round"] == "unlabeled"
    # 호출자가 넘긴 기존 키는 그대로 보존되어야 함.
    assert rows[0]["env"]["DRY_RUN"] is True
    assert rows[0]["env"]["PRESERVE_EXISTING"] is True


def test_round_env_injected_and_monitor_aggregates(tmp_path, monkeypatch):
    """ROUND=R24 시 env.round=='R24' + monitor.by_round 에 R24 슬롯 생성."""
    monkeypatch.setenv("BACKFILL_AUDIT_DIR", str(tmp_path))
    monkeypatch.setenv("ROUND", "R24")

    # R24 라운드로 3건 (dry_run / preserve / sentiment).
    for script, mode in [
        ("topic_backfill", "dry_run"),
        ("sentiment_backfill", "preserve_existing"),
        ("dedup_voc", "dry_run"),
    ]:
        with record_run(
            script=script,
            mode=mode,
            env={"DRY_RUN": (mode == "dry_run"), "PRESERVE_EXISTING": True,
                 "BACKUP_BEFORE": True},
        ) as audit:
            audit.note(f"{script}-r24")

    # 별도로 ROUND 제거 후 라벨 미설정 1건 (unlabeled 슬롯).
    monkeypatch.delenv("ROUND", raising=False)
    with record_run(
        script="topic_llm_apply",
        mode="dry_run",
        env={"DRY_RUN": True, "PRESERVE_EXISTING": True, "BACKUP_BEFORE": True},
    ) as audit:
        audit.note("unlabeled-control")

    rows = _read_jsonl(tmp_path / "backfill_audit.jsonl")
    assert len(rows) == 4
    rounds = [r["env"].get("round") for r in rows]
    assert rounds.count("R24") == 3
    assert rounds.count("unlabeled") == 1

    # monitor.summarize() 가 by_round 슬롯을 정확히 채워야 함.
    now = datetime.now(timezone.utc)
    payload = summarize(rows, window_days=7, now=now)
    assert "by_round" in payload
    assert payload["by_round"]["R24"]["runs"] == 3
    assert payload["by_round"]["R24"]["ok"] == 3
    assert payload["by_round"]["unlabeled"]["runs"] == 1
