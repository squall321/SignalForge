"""R25 트랙 D — archive 모드 단위 테스트.

2 케이스 (DB 미사용 — backfill_audit 단독 검증):

1. add_affected_ids() 가 100개 이하면 JSONL 한 줄에 inline 보관.
   archive 파일은 생성되지 않음.

2. 100개 초과 시 JSONL inline 은 첫 100개로 절단되고,
   reports/archive/<round>/<script>_<run_id>.json 에 전체 ID 저장.
   archive_paths / affected_ids_total 가 entry 에 채워짐.

실행:
    cd crawler && /home/koopark/claude/SignalForge/.venv/bin/python \\
        -m pytest tests/test_archive_mode.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from insight import backfill_audit  # noqa: E402


@pytest.fixture()
def audit_tmp(tmp_path, monkeypatch):
    """BACKFILL_AUDIT_DIR 을 tmp 디렉토리로 강제."""
    monkeypatch.setenv("BACKFILL_AUDIT_DIR", str(tmp_path))
    return tmp_path


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]


# ─────────────────────────────────────────────────────────────
# 케이스 1: inline 보관 (≤100)
# ─────────────────────────────────────────────────────────────
def test_archive_mode_inline_under_cap(audit_tmp):
    """50개 ID — JSONL inline 만, archive 파일 미생성."""
    with backfill_audit.record_run(
        script="dedup_voc", mode="execute", env={"round": "R25"}
    ) as audit:
        audit.add_affected_ids("voc_deleted", list(range(1, 51)))

    jsonl = _read_jsonl(audit_tmp / "backfill_audit.jsonl")
    assert len(jsonl) == 1
    entry = jsonl[0]

    # inline 으로 50개 모두 보관.
    assert entry["affected_ids"] == {"voc_deleted": list(range(1, 51))}
    assert entry["affected_ids_total"] == {"voc_deleted": 50}
    # archive 파일 미생성 → archive_paths 비어 있어야 함.
    assert entry["archive_paths"] == {}
    archive_dir = audit_tmp / "archive" / "R25"
    assert not archive_dir.exists() or not any(archive_dir.iterdir())


# ─────────────────────────────────────────────────────────────
# 케이스 2: overflow → archive 파일 분리
# ─────────────────────────────────────────────────────────────
def test_archive_mode_overflow_writes_archive_file(audit_tmp):
    """250개 ID — JSONL inline 첫 100개 + archive 파일에 250개 전부."""
    big_ids = list(range(1000, 1250))  # 250개
    with backfill_audit.record_run(
        script="crisis_platform_direct", mode="preserve", env={"round": "R25"}
    ) as audit:
        audit.add_affected_ids("9to5google.voc_inserted", big_ids)

    jsonl = _read_jsonl(audit_tmp / "backfill_audit.jsonl")
    assert len(jsonl) == 1
    entry = jsonl[0]

    # JSONL inline 은 첫 100개로 절단.
    inline = entry["affected_ids"]["9to5google.voc_inserted"]
    assert len(inline) == 100
    assert inline == big_ids[:100]

    # 총 개수 보존.
    assert entry["affected_ids_total"]["9to5google.voc_inserted"] == 250

    # archive 파일 경로 entry 에 기록되어 있어야 함.
    arc_path_str = entry["archive_paths"]["9to5google.voc_inserted"]
    assert arc_path_str, "archive_paths 비어 있으면 안 됨"
    arc_path = Path(arc_path_str)
    assert arc_path.exists(), f"archive 파일 없음: {arc_path}"
    # 경로 구조 검증: archive/R25/<script>_<run_id>.json
    assert arc_path.parent.name == "R25"
    assert arc_path.parent.parent.name == "archive"
    assert arc_path.name.startswith("crisis_platform_direct_")
    assert arc_path.name.endswith(".json")

    # archive payload 무결성 — 250개 전부 보관.
    payload = json.loads(arc_path.read_text(encoding="utf-8"))
    assert payload["round"] == "R25"
    assert payload["script"] == "crisis_platform_direct"
    assert payload["affected_ids"]["9to5google.voc_inserted"] == big_ids
    assert payload["affected_ids_total"]["9to5google.voc_inserted"] == 250
    # run_id 가 JSONL entry 와 일치 → cross-reference 가능.
    assert payload["run_id"] == entry["run_id"]
