"""백필 감사 로그 모듈 (R19 트랙 E).

R18 사고(topic 백필 재실행이 기존 분류 폭락 유발) 예방을 위해
모든 백필 스크립트가 *공통 감사 로그* 에 1줄(JSONL) 을 남기도록 강제.

설계:
- 파일: ``reports/backfill_audit.jsonl`` (append-only, 1줄 1실행).
- 키: run_id, script, started_at, finished_at, mode (dry/preserve/full),
       env (관련 환경변수 snapshot), counters (시도/UPDATE/skip/error),
       backup_path (있을 때만), notes.
- 동시성: 같은 호스트 단일 실행 가정. fcntl/lock 없이 append open 사용.
- ``record_run()`` 컨텍스트 매니저가 표준 패턴:

    with record_run(script="topic_backfill", mode="dry_run", env={...}) as audit:
        audit.note("대상 12,345건")
        audit.bump("seen", 12345)
        audit.bump("updated", 0)  # dry → UPDATE 안 함
        audit.set_backup_path(Path("reports/topic_backup_2026-06-05.json"))

종료 시 자동으로 finished_at + 1줄 append.  예외 발생 시 status=error
+ exc_message 까지 기록 후 재-raise.

설계 원칙:
- 의존성 최소화 — stdlib 만.
- 실패해도 백필 본체는 막지 않음 (audit_write 실패는 stderr 로 경고).
- ``BACKFILL_AUDIT_DIR`` 환경변수로 경로 오버라이드 가능 (테스트용).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional


_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "reports"
_AUDIT_FILENAME = "backfill_audit.jsonl"

# R25 트랙 D — JSONL 한 줄에 박는 affected_ids 상한.
# 그 이상은 별도 archive 파일로 분리하여 JSONL 가독성/크기 보존.
_INLINE_IDS_CAP = 100


def _audit_path() -> Path:
    """환경변수 ``BACKFILL_AUDIT_DIR`` 우선, 없으면 repo_root/reports."""
    env = os.getenv("BACKFILL_AUDIT_DIR", "").strip()
    base = Path(env) if env else _DEFAULT_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / _AUDIT_FILENAME


def _archive_dir(round_label: str) -> Path:
    """R25 트랙 D — 큰 ID list 보관용 archive 디렉토리.

    구조: ``<audit_dir>/archive/<round>/`` — round 별로 sharding.
    """
    env = os.getenv("BACKFILL_AUDIT_DIR", "").strip()
    base = Path(env) if env else _DEFAULT_DIR
    safe_round = (round_label or "unlabeled").strip() or "unlabeled"
    out = base / "archive" / safe_round
    out.mkdir(parents=True, exist_ok=True)
    return out


class AuditEntry:
    """단일 백필 실행 기록을 누적하는 객체.

    공개 메서드는 모두 *합성* 만 — 실제 write 는 컨텍스트 종료 시 1회.
    """

    def __init__(self, script: str, mode: str, env: Dict[str, Any]):
        self.run_id = uuid.uuid4().hex[:12]
        self.script = script
        self.mode = mode
        self.env = dict(env or {})
        # R24 트랙 E — 라운드 라벨 자동 주입.
        # 호출자가 env 에 명시했으면 우선, 없으면 ROUND 환경변수, 둘 다 없으면 'unlabeled'.
        if "round" not in self.env:
            self.env["round"] = os.getenv("ROUND", "").strip() or "unlabeled"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.finished_at: Optional[str] = None
        self.status: str = "running"
        self.exc_message: Optional[str] = None
        self.counters: Dict[str, int] = {}
        self.backup_path: Optional[str] = None
        self.notes: list[str] = []
        # R25 트랙 D — archive 모드: 영향받은 row ID 누적.
        # key = 자유 라벨 (예: 'voc_inserted', 'topics_updated', 'voc_deleted')
        self.affected_ids: Dict[str, list[int]] = {}
        # 큰 list 가 archive 파일로 분리될 때 채워지는 path 매핑.
        self.archive_paths: Dict[str, str] = {}

    def bump(self, key: str, n: int = 1) -> None:
        """카운터 누적 — 예: ``bump("updated", len(ups))``."""
        if n == 0:
            return
        self.counters[key] = self.counters.get(key, 0) + int(n)

    def set_backup_path(self, path: Path | str) -> None:
        self.backup_path = str(path)

    def note(self, msg: str) -> None:
        """짧은 자유 텍스트 — 디버깅용.  여러 줄도 허용 (list 누적)."""
        if msg:
            self.notes.append(str(msg))

    def add_affected_ids(self, key: str, ids) -> None:
        """R25 트랙 D — 특정 액션이 INSERT/UPDATE/DELETE 한 row PK 누적.

        Parameters
        ----------
        key
            액션 식별자.  스크립트 자유 라벨 권장: 'voc_inserted',
            'topics_updated', 'voc_deleted' 등.  여러번 호출 시 누적.
        ids
            iterable of int.  None / 빈 시퀀스 무시.
        """
        if not ids:
            return
        bucket = self.affected_ids.setdefault(key, [])
        for i in ids:
            try:
                bucket.append(int(i))
            except (TypeError, ValueError):
                continue

    def to_dict(self) -> Dict[str, Any]:
        # R25 트랙 D — inline 한도 초과 ID list 는 archive 파일로 분리.
        # JSONL 한 줄에는 첫 _INLINE_IDS_CAP 개 + 총 개수 + archive 경로만 남김.
        inline_ids: Dict[str, list[int]] = {}
        counts: Dict[str, int] = {}
        archive_refs: Dict[str, str] = dict(self.archive_paths)
        for key, ids in self.affected_ids.items():
            counts[key] = len(ids)
            if len(ids) <= _INLINE_IDS_CAP:
                inline_ids[key] = list(ids)
            else:
                inline_ids[key] = list(ids[:_INLINE_IDS_CAP])
                # archive 경로는 외부에서 _flush_archive 가 채움 — to_dict 시점에 미존재 가능
                if key not in archive_refs:
                    archive_refs[key] = ""  # placeholder
        return {
            "run_id": self.run_id,
            "script": self.script,
            "mode": self.mode,
            "env": self.env,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "exc_message": self.exc_message,
            "counters": self.counters,
            "backup_path": self.backup_path,
            "notes": self.notes,
            # R25 트랙 D
            "affected_ids": inline_ids,
            "affected_ids_total": counts,
            "archive_paths": archive_refs,
        }


def _safe_append(entry_dict: Dict[str, Any]) -> None:
    """JSONL 1줄 append.  실패해도 raise 안 함 (백필 본체 보호)."""
    try:
        path = _audit_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry_dict, ensure_ascii=False) + "\n")
    except Exception as e:  # pragma: no cover — 권한 문제 등
        print(f"[backfill_audit] write fail: {e}", file=sys.stderr)


def _flush_archive(entry: "AuditEntry") -> None:
    """R25 트랙 D — affected_ids 중 inline cap 초과 키를 archive 파일로 저장.

    archive 경로 구조::

        <audit_dir>/archive/<round>/<script>_<run_id>.json

    한 run 의 모든 over-cap 키를 단일 JSON 으로 묶음.  실패해도 raise 안 함.
    """
    overflows = {
        k: v for k, v in entry.affected_ids.items() if len(v) > _INLINE_IDS_CAP
    }
    if not overflows:
        return
    try:
        round_label = str(entry.env.get("round", "unlabeled") or "unlabeled")
        out_dir = _archive_dir(round_label)
        fname = f"{entry.script}_{entry.run_id}.json"
        out_path = out_dir / fname
        payload = {
            "run_id": entry.run_id,
            "script": entry.script,
            "mode": entry.mode,
            "round": round_label,
            "started_at": entry.started_at,
            "finished_at": entry.finished_at,
            "affected_ids": overflows,
            "affected_ids_total": {k: len(v) for k, v in overflows.items()},
        }
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        for k in overflows:
            entry.archive_paths[k] = str(out_path)
    except Exception as e:  # pragma: no cover
        print(f"[backfill_audit] archive write fail: {e}", file=sys.stderr)


@contextmanager
def record_run(
    *,
    script: str,
    mode: str,
    env: Optional[Dict[str, Any]] = None,
) -> Iterator[AuditEntry]:
    """백필 실행을 감사 로그에 기록하는 컨텍스트 매니저.

    Parameters
    ----------
    script
        스크립트 식별자 (예: "topic_backfill", "sentiment_backfill", "dedup_voc").
    mode
        실행 모드 — "dry_run" / "preserve_existing" / "full_reclassify" 등 자유 문자열.
    env
        관련 환경변수 snapshot (DATABASE_URL 같은 비밀은 *제외하고* 호출자 책임).

    Yields
    ------
    AuditEntry
        ``bump`` / ``note`` / ``set_backup_path`` 호출용 객체.
    """
    entry = AuditEntry(script=script, mode=mode, env=env or {})
    try:
        yield entry
        entry.status = "ok"
    except BaseException as e:
        entry.status = "error"
        entry.exc_message = f"{type(e).__name__}: {e}"
        raise
    finally:
        entry.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # R25 트랙 D — 큰 ID list 를 archive 파일에 먼저 flush (archive_paths 채움),
        # 그 다음 JSONL append (archive 경로가 entry dict 에 포함되도록).
        _flush_archive(entry)
        _safe_append(entry.to_dict())


def list_recent(limit: int = 20) -> list[Dict[str, Any]]:
    """감사 로그 최근 ``limit`` 행 반환 (최신순).

    파일이 없으면 [].  파싱 실패 줄은 skip.
    """
    path = _audit_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[Dict[str, Any]] = []
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out
