"""R26 트랙 D — archive 디렉토리 *실측* 단위 테스트.

R25 보고서가 "/reports/archive/R25/*.jsonl 영구 기록" 을 주장했으나
실측 시 디렉토리가 존재하지 않았음 (self-report drift).  R26 트랙 D 는
backfill_audit.record_run() 이 over-cap affected_ids 를 만났을 때
*실제 파일시스템 디렉토리* 와 *실제 파일* 을 생성하는지 검증한다.

2 케이스 (모두 실 ``reports/archive/<round>/`` 경로 — tmp_path 미사용):

1. 디렉토리 생성 보장 — record_run() 종료 시 _archive_dir() 가 mkdir -p 로
   상위 ``reports/archive/`` 와 round 하위 ``R26_TEST/`` 모두 보장.

2. 파일 작성 — over-cap (>100) affected_ids 를 add 한 run 에서
   ``reports/archive/R26_TEST/<script>_<run_id>.json`` 이 실제 생성되고,
   payload 가 round / run_id / affected_ids 전체를 보존한다.

테스트는 자체 round 라벨 ``R26_TEST`` 를 사용하여 운영 R26 archive 와
충돌하지 않도록 격리하고, 종료 시 생성된 파일을 정리한다.

실행:
    cd crawler && /home/koopark/claude/SignalForge/backend/.venv/bin/python \\
        -m pytest tests/test_archive_real.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from insight import backfill_audit  # noqa: E402

# repo_root = crawler/../
_REPO_ROOT = Path(CRAWLER_ROOT).parent
_REAL_REPORTS = _REPO_ROOT / "reports"
_TEST_ROUND = "R26_TEST"
_REAL_ARCHIVE_ROUND_DIR = _REAL_REPORTS / "archive" / _TEST_ROUND


@pytest.fixture()
def isolate_test_round_dir():
    """테스트 라운드 디렉토리 사전/사후 격리.

    - 사전: 잔여 디렉토리 제거 (이전 실패 흔적).
    - 사후: 테스트가 만든 파일/디렉토리 제거하여 실 환경 오염 방지.
    - reports/archive/ 상위 디렉토리는 *남겨둠* (track D2 의 본질).
    """
    if _REAL_ARCHIVE_ROUND_DIR.exists():
        shutil.rmtree(_REAL_ARCHIVE_ROUND_DIR)
    yield _REAL_ARCHIVE_ROUND_DIR
    if _REAL_ARCHIVE_ROUND_DIR.exists():
        shutil.rmtree(_REAL_ARCHIVE_ROUND_DIR)
    # backfill_audit.jsonl 에 추가된 테스트 라운드 줄은 그대로 둔다 —
    # JSONL 의 append-only 성격을 존중 (보고서가 잔여 줄을 보고 의심하지 않도록
    # round 라벨 R26_TEST 로 명확히 구분되어 있음).


# ─────────────────────────────────────────────────────────────
# 케이스 1: archive 디렉토리가 실제로 생성된다
# ─────────────────────────────────────────────────────────────
def test_archive_directory_is_created_on_overflow(isolate_test_round_dir):
    """over-cap (>100) ID 를 add 하면 reports/archive/<round>/ 가 mkdir 됨."""
    target_dir = isolate_test_round_dir
    assert not target_dir.exists(), "fixture 사전 정리 실패"

    big_ids = list(range(20000, 20150))  # 150개 → cap(100) 초과
    with backfill_audit.record_run(
        script="track_d_smoke",
        mode="execute",
        env={"round": _TEST_ROUND},
    ) as audit:
        audit.add_affected_ids("smoke.voc_inserted", big_ids)

    # 실 파일시스템에 디렉토리가 *반드시* 존재.
    assert target_dir.exists(), f"archive 디렉토리 미생성: {target_dir}"
    assert target_dir.is_dir()
    # 상위 reports/archive/ 도 함께 보장 (mkdir -p 동작 확인).
    assert (_REAL_REPORTS / "archive").is_dir()


# ─────────────────────────────────────────────────────────────
# 케이스 2: archive 파일이 실제로 작성되고 payload 가 보존된다
# ─────────────────────────────────────────────────────────────
def test_archive_file_is_written_with_full_payload(isolate_test_round_dir):
    """over-cap run 직후 reports/archive/<round>/<script>_<run_id>.json 작성 확인."""
    target_dir = isolate_test_round_dir
    big_ids = list(range(30000, 30200))  # 200개

    with backfill_audit.record_run(
        script="track_d_archive_smoke",
        mode="preserve",
        env={"round": _TEST_ROUND},
    ) as audit:
        audit.add_affected_ids("smoke.topics_updated", big_ids)
        run_id = audit.run_id  # 이름 cross-check 용

    # 디렉토리 + 파일이 실제 있어야 한다.
    assert target_dir.exists()
    archived = list(target_dir.glob(f"track_d_archive_smoke_{run_id}.json"))
    assert len(archived) == 1, (
        f"archive 파일 미생성 또는 이름 mismatch: dir={list(target_dir.iterdir())}"
    )

    # payload 무결성: round + run_id + 200개 전부 보관.
    payload = json.loads(archived[0].read_text(encoding="utf-8"))
    assert payload["round"] == _TEST_ROUND
    assert payload["run_id"] == run_id
    assert payload["script"] == "track_d_archive_smoke"
    assert payload["affected_ids"]["smoke.topics_updated"] == big_ids
    assert payload["affected_ids_total"]["smoke.topics_updated"] == 200
