"""Harvest 4 트랙 H1 — `audit_round` archive 폴더 자동 생성 단위 테스트.

R26 회고: harvest3p 라운드가 `audit_round()` 를 호출했음에도 affected_ids 가
비어있어 ``reports/archive/harvest3p/`` 폴더가 생성되지 않았고, Hook validator
(workflow_validator_hook) 가 archive_drift 로 *잘못* 캡처했다.

H1 의 해법:
1. ``audit_round`` 진입 시점에 ``reports/archive/<round>/`` 를 *항상* mkdir.
2. 폴더가 비어 있으면 종료 시점에 ``.sentinel.json`` 를 작성 — Hook validator
   가 ``p.exists()`` 만 보면 sentinel 만 있어도 archive_drift 가 사라진다.
3. ``BackfillAudit._archive_dir`` 와 동일 레이아웃을 공유 — 큰 ID list 가
   있으면 그쪽이 채우고, 없으면 sentinel 이 채운다 (충돌 없음).

세 케이스:
- normal:  정상 종료 → archive/<round>/ + .sentinel.json 존재.
- exception: 예외 발생해도 mkdir + sentinel 보장 (try/finally).
- nested: 부모/자식 라운드 각각 별도 archive 폴더 + 각각 sentinel.

실행::

    cd crawler && /home/koopark/claude/SignalForge/.venv/bin/python \\
        -m pytest tests/test_audit_archive_auto.py -v
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


from base.audit import audit_round  # noqa: E402


def _archive_path(audit_path: Path, round_label: str) -> Path:
    return audit_path.parent / "archive" / round_label


def test_archive_dir_created_with_sentinel_on_normal_close(tmp_path: Path, monkeypatch):
    """정상 종료: archive/<round>/ 자동 mkdir + sentinel.json 작성."""
    audit = tmp_path / "audit.jsonl"
    monkeypatch.delenv("ROUND", raising=False)

    with audit_round(
        "harvest4",
        track="H1",
        script="unit_test_archive_auto",
        path=audit,
        extra={"dry_run": True},
    ) as a:
        a.update(saved=0, fetched=0)

    archive = _archive_path(audit, "harvest4")
    assert archive.is_dir(), f"archive dir not created: {archive}"

    sentinel = archive / ".sentinel.json"
    assert sentinel.is_file(), f"sentinel not written: {sentinel}"

    body = json.loads(sentinel.read_text(encoding="utf-8"))
    assert body["round"] == "harvest4"
    assert body["track"] == "H1"
    assert body["script"] == "unit_test_archive_auto"
    assert body["status"] == "ok"
    assert body["kind"] == "sentinel"
    # run_id 는 12자 hex.
    assert isinstance(body["run_id"], str) and len(body["run_id"]) == 12


def test_archive_dir_created_with_sentinel_on_exception(tmp_path: Path, monkeypatch):
    """예외 발생: archive/<round>/ + sentinel 이 try/finally 로 보장.

    sentinel 의 status 는 'fail' 로 기록되어 후속 디버깅에 단서 제공.
    """
    audit = tmp_path / "audit.jsonl"
    monkeypatch.delenv("ROUND", raising=False)

    with pytest.raises(RuntimeError, match="boom"):
        with audit_round(
            "harvest4-exc",
            track="H1",
            script="unit_test_exc",
            path=audit,
        ) as a:
            a.update(saved=1)
            raise RuntimeError("boom")

    archive = _archive_path(audit, "harvest4-exc")
    assert archive.is_dir(), f"archive dir not created on exception: {archive}"

    sentinel = archive / ".sentinel.json"
    assert sentinel.is_file(), "sentinel must be written even on exception"
    body = json.loads(sentinel.read_text(encoding="utf-8"))
    assert body["status"] == "fail"
    assert body["round"] == "harvest4-exc"


def test_archive_dir_nested_rounds_each_has_own_folder(tmp_path: Path, monkeypatch):
    """중첩 round: 부모/자식이 *각각 다른* archive 폴더 + sentinel 을 가진다.

    추가 검증: 자식이 affected_ids 대용으로 외부 파일을 archive 폴더에 쓰면
    sentinel 은 *작성되지 않는다* — 의미있는 파일이 있을 때 노이즈 방지.
    """
    audit = tmp_path / "audit.jsonl"
    monkeypatch.delenv("ROUND", raising=False)

    with audit_round("harvest4-parent", track="H1", script="parent",
                     path=audit) as parent:
        parent.update(step=1)
        with audit_round("harvest4-child", track="H1.sub", script="child",
                         path=audit) as child:
            child.update(step=1)
            # 자식 archive 폴더에 *의미있는* 결과물을 직접 쓴다 (e.g. backfill_audit
            # 의 archive_paths 모방). 이 경우 sentinel 은 작성되지 않아야 한다.
            child_archive = _archive_path(audit, "harvest4-child")
            (child_archive / "real_payload.json").write_text(
                json.dumps({"ids": [1, 2, 3]}), encoding="utf-8"
            )
        parent.update(step=2)

    p_arch = _archive_path(audit, "harvest4-parent")
    c_arch = _archive_path(audit, "harvest4-child")

    assert p_arch.is_dir() and c_arch.is_dir()
    assert p_arch != c_arch, "nested rounds must have distinct archive dirs"

    # 부모는 빈 폴더 → sentinel 존재.
    assert (p_arch / ".sentinel.json").is_file()
    # 자식은 의미있는 파일이 있으므로 sentinel *없음*.
    assert not (c_arch / ".sentinel.json").exists()
    # 의미있는 파일은 그대로 보존.
    assert (c_arch / "real_payload.json").is_file()
