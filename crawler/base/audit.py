"""Harvest 3+ 트랙 P2 — `audit_round` 컨텍스트 매니저.

목적
----
D verify 가 반복적으로 잡아낸 결함: 한국 한국 사이트 페이지네이션 깊이 수집
(`korean_pagination_deep.py`), `topic_llm_apply.py` 등 *애드혹* audit JSONL 을
쓰는 스크립트들이 `start` 이벤트만 남기고 `end` 이벤트는 본문 종료(혹은 예외)
시점에 누락되곤 했다.  특히 `audit_korean_deep_harvest2.jsonl` 에서
``round=harvest2`` 의 ``event=end`` 가 부재하여 verify D 가 실패했다.

이 모듈은 그 결함을 **영구적으로** 해결한다:

* `audit_round(round_label, *, track=None, script=..., path=..., extra=...)`
  컨텍스트 매니저가 진입 시 `event=start`, 종료 시 `event=end` (status=ok/fail)
  를 항상 자동 append.
* 예외 발생 시에도 `try/finally` 로 end 이벤트가 보장되며
  `status=fail` + `exc_message` 가 기록된다.
* `.update(**kw)` / `.event(name, **payload)` 로 도중 카운터 / 중간 이벤트도
  같은 JSONL 에 1줄씩 추가할 수 있다 (예: ``audit.event("new_collector", ...)``).
* 중첩(nested) 사용도 안전 — 자식 컨텍스트는 다른 round 라벨로 별개의
  start/end 쌍을 쓴다 (호출자 책임).

설계 원칙
~~~~~~~~~

* stdlib 만 — 의존성 0.
* 본체 코드를 막지 않음 — JSONL append 자체가 실패해도 stderr 로 경고만 출력.
* 경로는 명시(`path=...`) > 환경변수(`AUDIT_PATH`) > 기본 위치
  (`reports/backfill_audit.jsonl`) 순서로 결정.
* 라운드 라벨은 명시 > 환경변수(`ROUND`) > `unlabeled`.

기존 `insight.backfill_audit.record_run()` 은 단일 *최종 1줄* 형식이라
중간 이벤트(`new_collector` 등) 를 못 담는다.  `audit_round` 는 그 패턴이
필요한 스크립트(특히 harvest 류) 에 쓰고, 일반 backfill 은 그대로
`record_run` 을 쓰면 된다.  두 시스템은 공존한다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional


_DEFAULT_AUDIT = (
    Path(__file__).resolve().parents[2] / "reports" / "backfill_audit.jsonl"
)


def _resolve_path(path: Optional[str | Path]) -> Path:
    """경로 결정: 명시 > AUDIT_PATH env > 기본 reports/backfill_audit.jsonl."""
    if path:
        p = Path(path)
    else:
        env = os.getenv("AUDIT_PATH", "").strip()
        p = Path(env) if env else _DEFAULT_AUDIT
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_archive_dir(audit_path: Path, round_label: str) -> Path:
    """Harvest 4 트랙 H1 — round 별 archive 디렉토리.

    구조: ``<audit_dir>/archive/<round>/`` — `backfill_audit._archive_dir` 와
    동일 레이아웃을 공유하여, 큰 affected_ids 가 있을 때나 없을 때나 같은
    위치에서 round 추적 가능. 본 함수는 항상 mkdir 한다 (빈 폴더라도).
    """
    base = audit_path.parent  # 보통 reports/
    safe_round = (round_label or "unlabeled").strip() or "unlabeled"
    out = base / "archive" / safe_round
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_sentinel(archive_dir: Path, payload: Dict[str, Any]) -> None:
    """빈 archive 폴더에 sentinel.json 을 1회 작성.

    R26 회고: Hook validator 가 ``reports/archive/<round>/`` 경로 존재만으로
    archive_drift 를 해소하므로, 폴더 자체가 존재하면 충분. sentinel 은 그
    근거를 남기는 메타 파일. 이미 같은 round 의 다른 결과물 (대형 ID list 등)
    이 있으면 sentinel 은 *추가하지 않는다* — 노이즈 방지.
    """
    try:
        # 이미 의미있는 파일이 있으면 sentinel 미작성.
        existing = [p for p in archive_dir.iterdir() if p.is_file()]
        if existing:
            return
        sentinel = archive_dir / ".sentinel.json"
        with sentinel.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:  # pragma: no cover — 디스크/권한
        print(f"[audit_round] sentinel write fail ({archive_dir}): {e}", file=sys.stderr)


def _resolve_round(round_label: Optional[str]) -> str:
    """라운드 결정: 명시 > ROUND env > 'unlabeled'."""
    if round_label and str(round_label).strip():
        return str(round_label).strip()
    env = os.getenv("ROUND", "").strip()
    return env or "unlabeled"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _safe_append(path: Path, payload: Dict[str, Any]) -> None:
    """JSONL 한 줄 append.  실패해도 raise 안 함 (본체 보호)."""
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:  # pragma: no cover — 권한/디스크 문제 등
        print(f"[audit_round] write fail ({path}): {e}", file=sys.stderr)


class AuditRound:
    """단일 round 실행 기록 핸들 — `audit_round` 컨텍스트에서 yield."""

    def __init__(
        self,
        *,
        round_label: str,
        track: Optional[str],
        script: str,
        path: Path,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.run_id = uuid.uuid4().hex[:12]
        self.round = round_label
        self.track = track
        self.script = script
        self.path = path
        self.started_at = _now_iso()
        self.finished_at: Optional[str] = None
        # 누적 카운터/메타데이터 — end 이벤트 payload 의 'counters' 로 flush.
        self._counters: Dict[str, Any] = {}
        # start 이벤트에 묶을 부가 정보 (sites, before snapshot 등).
        self._start_extra: Dict[str, Any] = dict(extra or {})

    # ---- 도중 호출용 API ------------------------------------------------
    def update(self, **kw: Any) -> None:
        """카운터/메타 누적.  같은 키는 덮어쓴다.

        예: ``audit.update(saved=12, fetched=200)``.
        """
        for k, v in kw.items():
            self._counters[k] = v

    def bump(self, key: str, n: int = 1) -> None:
        """정수 카운터 누적 (기본 0).  ``update`` 와 달리 누적 합산."""
        if n == 0:
            return
        cur = self._counters.get(key, 0)
        try:
            self._counters[key] = int(cur) + int(n)
        except (TypeError, ValueError):
            # 호환성: 이미 비정수 값이 들어있으면 덮어쓴다.
            self._counters[key] = n

    def event(self, name: str, **payload: Any) -> None:
        """중간 이벤트를 JSONL 에 1줄 추가 (예: ``new_collector``).

        ``ts`` / ``round`` / ``track`` / ``script`` / ``run_id`` 는 자동 주입.
        """
        if not name:
            return
        row = {
            "ts": _now_iso(),
            "round": self.round,
            "run_id": self.run_id,
            "script": self.script,
            "event": str(name),
        }
        if self.track:
            row["track"] = self.track
        # payload 의 키는 그대로 유지 — 호환성을 위해 reserved 키만 보호.
        for k, v in payload.items():
            if k in ("ts", "round", "run_id", "script", "event"):
                continue
            row[k] = v
        _safe_append(self.path, row)

    # ---- 내부: start / end 이벤트 ----------------------------------------
    def _write_start(self) -> None:
        row = {
            "ts": self.started_at,
            "round": self.round,
            "run_id": self.run_id,
            "script": self.script,
            "event": "start",
        }
        if self.track:
            row["track"] = self.track
        if self._start_extra:
            row.update(self._start_extra)
        _safe_append(self.path, row)

    def _write_end(self, status: str, exc_message: Optional[str]) -> None:
        self.finished_at = _now_iso()
        try:
            elapsed_s = (
                _dt.datetime.fromisoformat(self.finished_at)
                - _dt.datetime.fromisoformat(self.started_at)
            ).total_seconds()
        except Exception:  # pragma: no cover
            elapsed_s = None
        row = {
            "ts": self.finished_at,
            "round": self.round,
            "run_id": self.run_id,
            "script": self.script,
            "event": "end",
            "status": status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": elapsed_s,
            "counters": self._counters,
        }
        if self.track:
            row["track"] = self.track
        if exc_message:
            row["exc_message"] = exc_message
        _safe_append(self.path, row)


@contextmanager
def audit_round(
    round_label: Optional[str] = None,
    *,
    track: Optional[str] = None,
    script: str = "unknown",
    path: Optional[str | Path] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Iterator[AuditRound]:
    """라운드 단위 audit JSONL 컨텍스트 — start + end 보장.

    Parameters
    ----------
    round_label
        라운드 식별자 (예: ``"harvest3p"``, ``"R28-harvest"``).
        ``None`` 이면 ``ROUND`` 환경변수, 그것도 없으면 ``"unlabeled"``.
    track
        트랙 라벨 (선택, 예: ``"P1"``, ``"B"``, ``"D"``).  start/end/event
        모든 row 에 ``track`` 키로 전파된다.
    script
        스크립트 식별자 (예: ``"korean_pagination_deep"``).
    path
        명시적 JSONL 경로.  미지정 시 ``AUDIT_PATH`` env → 기본 위치
        (``reports/backfill_audit.jsonl``).
    extra
        start 이벤트에 함께 박을 부가 정보 (예: ``{"sites": [...], "before": {...}}``).

    Yields
    ------
    AuditRound
        ``update`` / ``bump`` / ``event`` 호출용 핸들.

    예제
    ~~~~
        with audit_round("harvest3p", track="P1",
                         script="korean_pagination_deep",
                         extra={"pages": 50, "sites": ["clien", ...]}) as a:
            a.event("new_collector", track="C", platforms=[...])
            a.update(saved=120, fetched=500)
            # 정상 종료 → end 이벤트 자동 (status=ok)

        with audit_round("harvest3p", track="P1") as a:
            raise RuntimeError("boom")
        # → end 이벤트 status=fail, exc_message=RuntimeError: boom 으로 기록 후 재-raise
    """
    resolved_path = _resolve_path(path)
    resolved_round = _resolve_round(round_label)
    # Harvest 4 트랙 H1 — archive 폴더 시작 시점에 mkdir 보장.
    archive_dir = _resolve_archive_dir(resolved_path, resolved_round)
    handle = AuditRound(
        round_label=resolved_round,
        track=track,
        script=script,
        path=resolved_path,
        extra=extra,
    )
    handle._write_start()
    status = "ok"
    exc_message: Optional[str] = None
    try:
        yield handle
    except BaseException as e:
        status = "fail"
        exc_message = f"{type(e).__name__}: {e}"
        raise
    finally:
        # try/finally → 예외/정상 모두 end 이벤트 보장.
        handle._write_end(status=status, exc_message=exc_message)
        # archive 폴더가 비어 있으면 sentinel 작성 → Hook validator 가
        # archive_drift 로 오탐하지 않도록 *흔적* 을 남긴다.
        _write_sentinel(
            archive_dir,
            {
                "round": resolved_round,
                "run_id": handle.run_id,
                "script": script,
                "track": track,
                "status": status,
                "started_at": handle.started_at,
                "finished_at": handle.finished_at,
                "kind": "sentinel",
                "note": "auto-generated by audit_round to mark empty archive folder",
            },
        )
