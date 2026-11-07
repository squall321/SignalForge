# MV 갱신이 성공한 시각을 MV 마다 작은 파일로 남기고 읽는다 — 품질 보고서의 MV 신선도는 이 기록을 먼저 본다
"""
MV 갱신 기록.

PostgreSQL 은 MV 를 마지막으로 갱신한 시각을 어디에도 남기지 않는다. 품질 보고서는 그동안
pg_stat_user_tables 의 analyze · vacuum 시각으로 짐작했는데, REFRESH ... CONCURRENTLY 는 바뀐 행만
고쳐 쓰므로 바뀐 행이 적으면 autoanalyze 가 돌지 않는다. 그래서 30분마다 잘 갱신되던
kg_edges_daily · country_daily 가 2~3일 묵은 것으로 보였다(2026-11-07 보고서 — 3,440분 · 2,481분).

_refresh_mv 가 성공할 때마다 여기 남긴다. 남기는 시각은 갱신을 **시작한** 시각이다 — MV 내용은
그 문장이 시작할 때의 데이터이고, 갱신은 수십 초 걸리기도 한다(country_daily 42초 실측).
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "mv_refresh"


def record(view: str, refreshed_at: datetime, mode: str, elapsed_ms: int, base: Optional[Path] = None) -> None:
    """한 MV 의 마지막 성공 갱신을 덮어쓴다. 임시 파일 → rename 이라 읽는 쪽이 반쪽 파일을 보지 않는다."""
    d = base or _dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{view}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"view": view, "refreshed_at": refreshed_at.isoformat(),
                               "mode": mode, "elapsed_ms": elapsed_ms}), encoding="utf-8")
    os.replace(tmp, d / f"{view}.json")


def last_refreshed(view: str, base: Optional[Path] = None) -> Optional[datetime]:
    """기록된 마지막 성공 갱신 시각(UTC). 기록이 없거나 깨졌으면 None — 부르는 쪽이 다른 근거로 물러선다."""
    try:
        data = json.loads(((base or _dir()) / f"{view}.json").read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data["refreshed_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
