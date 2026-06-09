"""Stage 4.5 Y1 — 송신 측 양방향 자동 동기화 헬퍼.

`tasks.run_auto_sync_to_drive` 가 호출하는 순수 함수만 모음:

- ``newest_dump_meta(dump_dir, prefix)`` — backups/ 디렉터리에서 최신 dump
  파일 1개를 찾아 (path, sha256, size, mtime, voc_count_estimate) 메타를 만든다.
- ``write_latest_json(target, payload)`` — 원자적 쓰기 (tmp → rename).
- ``acquire_lock(path)`` / ``release_lock(handle)`` — flock 기반.  동일 호스트에서
  ``sync-to-drive.sh`` 가 수동으로 돌고 있을 때 중복 실행을 차단한다.

테스트가 Celery 부트스트랩 없이 import 할 수 있도록 본 모듈은 Celery /
crawler.tasks 에 의존하지 않는다.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class DumpMeta:
    path: str          # 절대경로
    name: str          # basename
    sha256: str        # 64자 hex
    size: int          # bytes
    mtime: float       # epoch seconds (UTC)


def _sha256_of(path: str, chunk: int = 1 << 20) -> str:
    """파일 sha256 계산.  50MB gzip 기준 ~250ms (1회/30분 → 무시 가능)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for buf in iter(lambda: f.read(chunk), b""):
            h.update(buf)
    return h.hexdigest()


def newest_dump_meta(dump_dir: str, prefix: str = "sf-db-") -> Optional[DumpMeta]:
    """`dump_dir` 안에서 prefix*.sql.gz 중 mtime 최신 1개 메타.

    - safety dump (`sf-db-safety-*`) 는 제외.  주기 백업만 대상.
    - 디렉터리가 없거나 매칭 0건이면 None.
    - 사이드카 .sha256 이 존재하면 그 값을 재사용 (재계산 회피).
    """
    if not os.path.isdir(dump_dir):
        return None

    candidates = []
    for name in os.listdir(dump_dir):
        if not name.startswith(prefix) or not name.endswith(".sql.gz"):
            continue
        # safety dump 는 별도 트랙 — 주기 sync 대상 아님
        if name.startswith(f"{prefix}safety-"):
            continue
        p = os.path.join(dump_dir, name)
        try:
            st = os.stat(p)
        except FileNotFoundError:
            continue
        candidates.append((st.st_mtime, p, name, st.st_size))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    mtime, path, name, size = candidates[0]

    # 사이드카 우선 (backup-to-drive.sh 가 항상 함께 생성)
    sha = ""
    sidecar = f"{path}.sha256"
    if os.path.isfile(sidecar):
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                first = f.readline().strip()
            if first:
                sha = first.split()[0]
        except Exception:
            sha = ""
    if not sha:
        sha = _sha256_of(path)

    return DumpMeta(path=path, name=name, sha256=sha, size=size, mtime=mtime)


def write_latest_json(target: str, payload: dict) -> str:
    """원자적 LATEST.json 쓰기 (tmp 동일 디렉터리 → rename)."""
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".latest.", suffix=".tmp", dir=os.path.dirname(target) or "."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, target)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    return target


@contextlib.contextmanager
def acquire_lock(lock_path: str):
    """비차단 flock — 잠금 실패 시 RuntimeError.  with 블록 종료 시 자동 해제."""
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fh = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            fh.close()
            raise RuntimeError(f"lock_held:{lock_path}") from exc
        fh.write(f"pid={os.getpid()} ts={time.time()}\n")
        fh.flush()
        yield fh
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        with contextlib.suppress(Exception):
            fh.close()


def build_latest_payload(
    *,
    dump: Optional[DumpMeta],
    voc_count: Optional[int],
    ts_iso: str,
    run_id: str,
    dry_run: bool,
) -> dict:
    """LATEST.json 본문 — 수신 측이 sha256 + last_dump 만 보고 delta 결정."""
    body: dict = {
        "schema": "signalforge.auto_sync.v1",
        "ts": ts_iso,
        "run_id": run_id,
        "dry_run": dry_run,
        "voc_count": voc_count,
    }
    if dump is not None:
        body["last_dump"] = {
            "name": dump.name,
            "sha256": dump.sha256,
            "size": dump.size,
            "mtime": int(dump.mtime),
        }
    else:
        body["last_dump"] = None
    return body


def dump_meta_to_dict(d: Optional[DumpMeta]) -> Optional[dict]:
    return None if d is None else asdict(d)
