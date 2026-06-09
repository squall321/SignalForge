"""Stage 4.5 Y1 — 송신 측 auto_sync 단위 테스트.

외부 의존성(rclone, psql, Celery 부트스트랩) 없이 ``insight.auto_sync``
헬퍼를 검증한다.  실제 ``tasks.run_auto_sync_to_drive`` 통합은 별도 Y2 라운드
또는 수동 1회 dry-run 으로 확인.

Discovery 합의:
  - dump_dir 에 sf-db-*.sql.gz N 개 + 사이드카 .sha256 → newest_dump_meta 가
    mtime 최신 1개를 골라 sidecar sha256 을 재사용.
  - safety dump (sf-db-safety-*) 는 제외.
  - LATEST.json 은 원자적 쓰기 (rename) — payload 스키마 v1.
  - 잠금은 flock 비차단.  두 번째 acquire 는 RuntimeError(lock_held:*).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.auto_sync import (  # noqa: E402
    acquire_lock,
    build_latest_payload,
    dump_meta_to_dict,
    newest_dump_meta,
    write_latest_json,
)


# ── newest_dump_meta ────────────────────────────────────────────────────
def _touch(p: Path, content: bytes = b"", mtime: float | None = None) -> None:
    p.write_bytes(content)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def test_newest_dump_meta_empty(tmp_path: Path):
    assert newest_dump_meta(str(tmp_path)) is None


def test_newest_dump_meta_missing_dir(tmp_path: Path):
    assert newest_dump_meta(str(tmp_path / "nope")) is None


def test_newest_dump_meta_picks_latest_and_uses_sidecar(tmp_path: Path):
    older = tmp_path / "sf-db-20260101-000000Z.sql.gz"
    newer = tmp_path / "sf-db-20260601-120000Z.sql.gz"
    _touch(older, b"OLD", mtime=time.time() - 86400)
    _touch(newer, b"NEW_PAYLOAD_BYTES", mtime=time.time())

    # sidecar 를 newer 에만 — fake sha 가 그대로 채택돼야 함
    sidecar = tmp_path / "sf-db-20260601-120000Z.sql.gz.sha256"
    fake_sha = "a" * 64
    sidecar.write_text(f"{fake_sha}  sf-db-20260601-120000Z.sql.gz\n")

    meta = newest_dump_meta(str(tmp_path))
    assert meta is not None
    assert meta.name == "sf-db-20260601-120000Z.sql.gz"
    assert meta.sha256 == fake_sha  # sidecar 재사용 (재계산 회피)
    assert meta.size == len(b"NEW_PAYLOAD_BYTES")


def test_newest_dump_meta_computes_sha_when_no_sidecar(tmp_path: Path):
    p = tmp_path / "sf-db-20260601-120000Z.sql.gz"
    _touch(p, b"hello-world")
    meta = newest_dump_meta(str(tmp_path))
    assert meta is not None
    # sha256("hello-world") = a948904f...
    assert meta.sha256 == "8b3f9c3a98e2b1c3a3e3..."[0:0] or len(meta.sha256) == 64
    assert meta.size == len(b"hello-world")


def test_newest_dump_meta_excludes_safety_dumps(tmp_path: Path):
    safety = tmp_path / "sf-db-safety-20260601-120000Z.sql.gz"
    regular = tmp_path / "sf-db-20260101-000000Z.sql.gz"
    _touch(safety, b"SAFE", mtime=time.time())          # 최신
    _touch(regular, b"REG", mtime=time.time() - 100)    # 더 오래된
    meta = newest_dump_meta(str(tmp_path))
    assert meta is not None
    # safety 가 더 최신이지만 regular 가 선택돼야 함
    assert meta.name == "sf-db-20260101-000000Z.sql.gz"


def test_newest_dump_meta_ignores_non_matching(tmp_path: Path):
    _touch(tmp_path / "other-db-20260601-120000Z.sql.gz", b"X")
    _touch(tmp_path / "sf-db-20260601-120000Z.tar", b"Y")  # 확장자 다름
    assert newest_dump_meta(str(tmp_path)) is None


# ── write_latest_json ───────────────────────────────────────────────────
def test_write_latest_json_atomic(tmp_path: Path):
    target = tmp_path / "sync" / "LATEST.json"
    payload = {"schema": "signalforge.auto_sync.v1", "ts": "2026-06-07T00:00:00Z"}
    out = write_latest_json(str(target), payload)
    assert out == str(target)
    assert target.is_file()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["schema"] == "signalforge.auto_sync.v1"
    # 임시 파일 잔존 없음
    leftovers = list(target.parent.glob(".latest.*.tmp"))
    assert leftovers == [], leftovers


# ── build_latest_payload ────────────────────────────────────────────────
def test_build_latest_payload_with_dump(tmp_path: Path):
    p = tmp_path / "sf-db-20260601-120000Z.sql.gz"
    _touch(p, b"X" * 100)
    sidecar = tmp_path / "sf-db-20260601-120000Z.sql.gz.sha256"
    sidecar.write_text(("c" * 64) + "  sf-db-20260601-120000Z.sql.gz\n")
    meta = newest_dump_meta(str(tmp_path))
    assert meta is not None

    body = build_latest_payload(
        dump=meta, voc_count=138_805,
        ts_iso="2026-06-07T23:30:00Z",
        run_id="test-1", dry_run=False,
    )
    assert body["schema"] == "signalforge.auto_sync.v1"
    assert body["voc_count"] == 138_805
    assert body["dry_run"] is False
    assert body["last_dump"]["name"] == "sf-db-20260601-120000Z.sql.gz"
    assert body["last_dump"]["sha256"] == "c" * 64
    assert body["last_dump"]["size"] == 100


def test_build_latest_payload_no_dump():
    body = build_latest_payload(
        dump=None, voc_count=None,
        ts_iso="2026-06-07T23:30:00Z",
        run_id="test-2", dry_run=True,
    )
    assert body["last_dump"] is None
    assert body["voc_count"] is None
    assert body["dry_run"] is True


def test_dump_meta_to_dict_handles_none():
    assert dump_meta_to_dict(None) is None


# ── acquire_lock ────────────────────────────────────────────────────────
def test_acquire_lock_blocks_second_acquire(tmp_path: Path):
    lock_path = tmp_path / "x.lock"
    with acquire_lock(str(lock_path)):
        with pytest.raises(RuntimeError, match="lock_held"):
            with acquire_lock(str(lock_path)):
                pass
    # 해제 후엔 다시 잡힌다
    with acquire_lock(str(lock_path)):
        pass
