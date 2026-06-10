"""내부 운영 endpoint — 외부 노출 금지.

`/api/v1/_internal/cache-stats` 는 Redis 캐시 hit/miss 카운터를 JSON 으로 반환한다.
`/api/v1/_internal/llm-status` 는 현재 fast/high tier 의 LLM provider 가용성과
선택 시 reachable(ping) 여부를 JSON 으로 반환한다 (P3.7).

운영 모니터링 (Track E quality_report) 이 매일 09:30 KST 호출하여 캐시·LLM
가용성을 추적한다.

보안:
- 인증 없음 (이번 단계).
- 단, `_LOCALHOST_ONLY=True` 면 X-Forwarded-For / client.host 가 127.0.0.1·::1 일 때만 허용.
  nginx 가 외부 차단 (location /api/v1/_internal/ deny all;) 하는 운영 정책과 함께 사용.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_cache_stats
from app.database import get_db
from app.services import export_service

router = APIRouter(prefix="/_internal", tags=["internal"])

_LOCALHOST_ONLY = True
_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _enforce_localhost(req: Request) -> None:
    if not _LOCALHOST_ONLY:
        return
    host = req.client.host if req.client else None
    if host not in _ALLOWED_HOSTS:
        raise HTTPException(status_code=403, detail="localhost only")


@router.get("/cache-stats")
def cache_stats(request: Request) -> dict:
    """Redis 캐시 누적 hit/miss/ratio."""
    _enforce_localhost(request)
    return get_cache_stats()


# ── LLM 상태 ────────────────────────────────────────────────────────────────
def _ensure_crawler_on_path() -> None:
    """crawler/ 가 backend 의 sys.path 에 없으면 추가."""
    # backend/app/api/_internal.py → repo_root/crawler
    repo_root = Path(__file__).resolve().parents[3]
    crawler_dir = repo_root / "crawler"
    if crawler_dir.is_dir() and str(crawler_dir) not in sys.path:
        sys.path.insert(0, str(crawler_dir))


def _probe_provider(prov: Any, timeout: float = 3.0) -> Optional[bool]:
    """provider 가 reachable 한지 1회 ping. timeout 3s.

    summarize 를 매우 짧은 프롬프트로 호출, 응답 텍스트가 있으면 True.
    예외/None 응답이면 False.  client 가 없으면 None.
    """
    if prov is None:
        return None
    try:
        # max_tokens 등은 provider 가 자기 기본값 사용 — ping 만 목적.
        out = prov.summarize("ping")
        return bool(out)
    except Exception:
        return False


def _build_tier_info(tier: str, do_ping: bool) -> Dict[str, Any]:
    """fast/high tier 의 provider 정보 dict 구성.

    schema: {provider, model, base_url, reachable, tier_label}
    provider 가 None 이면 모든 필드 None.
    """
    _ensure_crawler_on_path()
    try:
        from insight.llm_provider import get_provider  # type: ignore
    except Exception as e:
        return {
            "provider": None, "model": None, "base_url": None,
            "reachable": None, "error": f"llm_provider import 실패: {e}",
        }

    prov = get_provider(tier=tier)
    if prov is None:
        return {
            "provider": None, "model": None, "base_url": None,
            "reachable": None, "tier_label": None,
        }

    info: Dict[str, Any] = {
        "provider": getattr(prov, "name", None),
        "model": getattr(prov, "model", None),
        "base_url": getattr(prov, "base_url", None),
        "reachable": None,
        "tier_label": getattr(prov, "tier_label", None),
    }
    if do_ping:
        info["reachable"] = _probe_provider(prov, timeout=3.0)
    return info


def _read_grounding_history() -> List[Dict[str, Any]]:
    """reports/insight_grounding_history.json 을 읽어 list 반환. 실패 시 []."""
    repo_root = Path(__file__).resolve().parents[3]
    path = Path(os.getenv("REPORT_DIR", str(repo_root / "reports"))) / "insight_grounding_history.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _last_grounding_score(history: List[Dict[str, Any]]) -> Optional[float]:
    """마지막 7개 entry 의 평균 grounding_score (None 제외). 빈 경우 None."""
    if not history:
        return None
    scores = [
        float(h["grounding_score"])
        for h in history[-7:]
        if isinstance(h.get("grounding_score"), (int, float))
    ]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


# P4.2 비용 추정 — 일 1회 insight 보고서 1편 ≈ 3k input + 1k output token 기준.
# 월 30회 호출 가정. 단위: USD/월.
_COST_TABLE_MONTHLY_USD: Dict[str, float] = {
    # claude-sonnet-4-5: input 3.00 / output 15.00 per 1M tok.
    # (3000*3 + 1000*15) / 1e6 = 0.024 USD/편 × 30 = 0.72
    "high-anthropic": 0.72,
    # gpt-4o-mini: input 0.15 / output 0.60 per 1M tok.
    # (3000*0.15 + 1000*0.60) / 1e6 = 0.00105 USD/편 × 30 = 0.0315
    "high-openai": 0.03,
    # shared / ollama / fast-* : 로컬 GPU/CPU 시간만 소비, 클라우드 비용 0.
    "high-shared": 0.0,
    "fast-openai": 0.0,
    "fast-anthropic": 0.72,
    "fast-ollama": 0.0,
    # external: 외부 OpenAI 호환 서버 — 비용 모름 (free tier 또는 사용자 책임).
    "external": 0.0,
}


def _cost_estimate_for(tier_label: Optional[str]) -> Dict[str, Any]:
    """tier_label 로부터 월 비용 추정 dict 반환.  미지정/미등록 → 0.0.

    'high-shared:qwen2.5:14b' 같은 prefix:model 라벨은 base prefix 로 매칭.
    """
    if not tier_label:
        return {"tier": None, "monthly_usd_estimate": 0.0}
    base = tier_label.split(":", 1)[0]
    cost = _COST_TABLE_MONTHLY_USD.get(tier_label)
    if cost is None:
        cost = _COST_TABLE_MONTHLY_USD.get(base, 0.0)
    return {
        "tier": tier_label,
        "monthly_usd_estimate": float(cost),
    }


def _is_cloud_ready(tier_label: Optional[str], reachable: Optional[bool]) -> bool:
    """high tier 가 실제 클라우드(Anthropic/OpenAI) 키로 동작 가능한 상태인지.

    조건:
      - tier_label 이 'high-anthropic' 또는 'high-openai'
      - ping=true 경로에서 reachable=True (ping=false 면 키 존재만으로 True)
    """
    if tier_label not in ("high-anthropic", "high-openai"):
        return False
    if reachable is None:
        return True
    return bool(reachable)


@router.get("/llm-status")
def llm_status(request: Request, ping: bool = False) -> dict:
    """fast / high tier provider 상태.

    ping=false (기본): provider/model/base_url 만 반환 (즉시 응답).
    ping=true: 각 provider 에 'ping' 프롬프트 1회 호출, reachable bool 채움.
              호출 비용/지연이 발생하므로 모니터링 스케줄러만 ping=true 사용.

    P4.1 추가 필드:
      - shared: high 와 fast 가 같은 (provider, model, base_url) 일 때 True.
      - prompt_version: 현재 SYSTEM_PROMPT 버전 ("v3-fewshot-grounded").
      - last_grounding_score: insight_grounding_history.json 최근 7일 평균.

    P4.2 추가 필드:
      - fast.tier_label / high.tier_label  (이미 P4.1 부터 노출 — 명시적 보장)
      - high.cloud_ready: bool  (실 클라우드 키 있고, ping 시 reachable)
      - high.cost_estimate / fast.cost_estimate:
          { "tier": "high-anthropic", "monthly_usd_estimate": 0.72 }
    """
    _enforce_localhost(request)
    fast = _build_tier_info("fast", do_ping=ping)
    high = _build_tier_info("high", do_ping=ping)
    external = _build_tier_info("external", do_ping=ping)
    shared = bool(
        fast.get("provider") is not None
        and high.get("provider") is not None
        and fast.get("provider") == high.get("provider")
        and fast.get("model") == high.get("model")
        and fast.get("base_url") == high.get("base_url")
    )
    # 비용/cloud_ready 부가 정보.
    fast["cost_estimate"] = _cost_estimate_for(fast.get("tier_label"))
    high["cost_estimate"] = _cost_estimate_for(high.get("tier_label"))
    external["cost_estimate"] = _cost_estimate_for(external.get("tier_label"))
    high["cloud_ready"] = _is_cloud_ready(high.get("tier_label"), high.get("reachable"))
    external["configured"] = bool(
        os.getenv("EXTERNAL_API_KEY", "").strip()
        and os.getenv("EXTERNAL_BASE_URL", "").strip()
        and os.getenv("EXTERNAL_MODEL", "").strip()
    )
    # prompt_version 은 llm_provider 에서 import (실패 시 unknown).
    _ensure_crawler_on_path()
    try:
        from insight.llm_provider import PROMPT_VERSION  # type: ignore
        prompt_version = PROMPT_VERSION
    except Exception:
        prompt_version = "unknown"
    history = _read_grounding_history()
    last_score = _last_grounding_score(history)
    return {
        "fast": fast,
        "high": high,
        "external": external,
        "shared": shared,
        "prompt_version": prompt_version,
        "last_grounding_score": last_score,
        "grounding_history": history[-7:],
        "env": {
            "LLM_QUALITY_TIER": os.getenv("LLM_QUALITY_TIER") or "auto",
            "has_anthropic_key": (
                os.getenv("ANTHROPIC_API_KEY", "").strip().startswith("sk-ant-")
            ),
            "has_openai_sk_key": (
                os.getenv("OPENAI_API_KEY", "").strip().startswith("sk-")
                and not os.getenv("OPENAI_API_KEY", "").strip().startswith("sk-ant-")
            ),
            "has_external_key": bool(os.getenv("EXTERNAL_API_KEY", "").strip()),
            "external_base_url": os.getenv("EXTERNAL_BASE_URL", "").strip() or None,
            "external_model": os.getenv("EXTERNAL_MODEL", "").strip() or None,
        },
    }


# ── 노이즈 스캔 (Track E) ──────────────────────────────────────────────────
# 운영자가 정기 호출 → 동일 본문이 반복 등장하는 패턴을 자동 노출.
# Instiz 잠금 문구 필터 영구화 효과 측정 + 다른 사이트의 유사 노이즈 패턴 조기 발견.

_NOISE_SCAN_SQL = text(
    """
    SELECT pl.code AS platform,
           substr(v.content_original, 1, 80) AS preview,
           count(*) AS n
    FROM voc_active v
    JOIN platforms pl ON pl.id = v.platform_id
    WHERE v.collected_at >= now() - make_interval(hours => :hours)
      AND (CAST(:platform AS text) IS NULL OR pl.code = CAST(:platform AS text))
      AND v.content_original IS NOT NULL
      AND length(v.content_original) > 0
    GROUP BY pl.code, substr(v.content_original, 1, 80)
    HAVING count(*) >= :min_repeat
    ORDER BY count(*) DESC
    LIMIT :limit
    """
)


# ── 알림 운영 추이 (Track E2) ─────────────────────────────────────────────
# rule 35 (platforms_negative_share) 가 7일간 합리적으로 발화하는지 모니터링.
# 동시에 rule 3 처럼 cooldown 위반 의심 패턴도 자동 노출 — Celery beat 5분 주기와
# alert_rules.cooldown_sec 의 정합성 점검 (예: cooldown=3600s 룰이 5분마다 fire).
_ALERT_TRENDS_RULES_SQL = text(
    """
    SELECT r.id, r.name, r.metric_path, r.threshold, r.cooldown_sec, r.is_active,
           count(e.id) FILTER (WHERE e.fired_at >= now() - make_interval(days => :days)) AS fires_window,
           count(e.id) FILTER (WHERE e.fired_at >= now() - interval '24 hours') AS fires_24h,
           avg(e.value) FILTER (WHERE e.fired_at >= now() - make_interval(days => :days)) AS avg_value,
           max(e.value) FILTER (WHERE e.fired_at >= now() - make_interval(days => :days)) AS max_value,
           max(e.fired_at) AS last_fired_at
    FROM alert_rules r
    LEFT JOIN alert_events e ON e.rule_id = r.id
    WHERE r.is_active = TRUE
    GROUP BY r.id
    ORDER BY r.id
    """
)


# cooldown 위반 의심: 24h 내 동일 룰의 연속 두 fire 간 간격이 cooldown_sec 미만.
# `/alerts/test` 는 ignore_cooldown=True 이므로 직접 호출 시 의도적 위반은 정상이지만,
# Celery beat 의 정기 평가만 작동해도 cooldown_sec 이내 재발화가 누적되면 이상.
_ALERT_COOLDOWN_VIOLATIONS_SQL = text(
    """
    WITH gaps AS (
        SELECT e.rule_id,
               e.fired_at,
               lag(e.fired_at) OVER (PARTITION BY e.rule_id ORDER BY e.fired_at) AS prev_fired,
               r.cooldown_sec
        FROM alert_events e
        JOIN alert_rules r ON r.id = e.rule_id
        WHERE e.fired_at >= now() - interval '24 hours'
    )
    SELECT count(*) AS n
    FROM gaps
    WHERE prev_fired IS NOT NULL
      AND EXTRACT(EPOCH FROM (fired_at - prev_fired)) < cooldown_sec
    """
)


@router.get("/alert-trends")
async def alert_trends(
    request: Request,
    days: int = Query(7, ge=1, le=30, description="발화 추이 윈도우 (1~30일)."),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """활성 룰의 ``days`` 일 발화 추이 + cooldown 위반 의심 카운트.

    응답::

        {
          "days": 7,
          "generated_at": "2026-06-03T...",
          "cooldown_violations_24h": 309,
          "rules": [
            {
              "rule_id": 35,
              "name": "platforms_negative_share",
              "metric_path": "community.platforms_negative_pct",
              "threshold": 0.15,
              "cooldown_sec": 3600,
              "fires_window": 0,
              "fires_24h": 0,
              "avg_value": null,
              "max_value": null,
              "last_fired_at": null,
              "silent_window": true
            },
            ...
          ]
        }

    - ``silent_window`` 는 윈도우 내 0 발화 룰을 표식 — quality_report 의 임계 조정
      권고 트리거로 사용된다.
    - ``cooldown_violations_24h`` 는 ``alert_events`` lag() 기반 — 정상 운영이라면 0.
      양수면 ``cooldown_sec`` 가 과대평가됐거나 평가 주기가 너무 짧다는 신호.
    """
    _enforce_localhost(request)
    rows = (await db.execute(_ALERT_TRENDS_RULES_SQL, {"days": int(days)})).all()
    rules: List[Dict[str, Any]] = []
    for r in rows:
        fires_window = int(r.fires_window or 0)
        rules.append({
            "rule_id": int(r.id),
            "name": r.name,
            "metric_path": r.metric_path,
            "threshold": float(r.threshold),
            "cooldown_sec": int(r.cooldown_sec),
            "fires_window": fires_window,
            "fires_24h": int(r.fires_24h or 0),
            "avg_value": (round(float(r.avg_value), 4) if r.avg_value is not None else None),
            "max_value": (round(float(r.max_value), 4) if r.max_value is not None else None),
            "last_fired_at": (r.last_fired_at.isoformat() if r.last_fired_at else None),
            "silent_window": fires_window == 0,
        })
    viol_row = (await db.execute(_ALERT_COOLDOWN_VIOLATIONS_SQL)).first()
    violations = int(viol_row.n or 0) if viol_row else 0
    return {
        "days": int(days),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cooldown_violations_24h": violations,
        "rules": rules,
    }


# ── ops_status_violation (rule 80) 7일 효과 측정 (R22 Track C) ────────────
# R20 트랙 C 에서 도입된 ``ops_status_violation`` (rule 80, name = 동일) 의 7일
# 발화 패턴을 운영자 1회 호출로 진단한다.
#
# ``/alert-trends`` 는 모든 활성 룰을 한 줄로 요약하지만, 본 endpoint 는 rule 80
# 단일 룰에 대해
#   1. day × severity × metric 매트릭스 (어느 metric 이 며칠째 violation 인지)
#   2. metric 단위 cooldown 위반 의심 (3600s 미만 간격 재발화)
#   3. ``operations_monitor`` (rule 78, DB-direct) 와의 dedupe 비교 — 같은 metric 의
#      양쪽 발화 카운트 → "파일 기반 propagator 가 실제로 추가 가치가 있는가?"
# 를 제공해 임계·cooldown 조정 권고의 근거로 쓴다.
_OPS_ALERTS_HISTORY_SQL = text(
    """
    SELECT id, fired_at, severity, value, threshold,
           payload->>'metric' AS metric,
           payload->'violation'->>'reason' AS reason,
           payload->>'source_date' AS source_date
    FROM alert_events
    WHERE rule_id = (
        SELECT id FROM alert_rules WHERE name = 'ops_status_violation'
    )
      AND fired_at >= now() - make_interval(days => :days)
    ORDER BY fired_at DESC
    """
)


# rule 78 (operations_monitor) — payload 최상위 metric 키 사용 (rule 80 와 schema 다름).
_OPS_MONITOR_COMPARE_SQL = text(
    """
    SELECT payload->>'metric' AS metric, count(*) AS n
    FROM alert_events
    WHERE rule_id = (
        SELECT id FROM alert_rules WHERE name = 'operations_monitor'
    )
      AND fired_at >= now() - make_interval(days => :days)
    GROUP BY 1
    """
)


@router.get("/ops-alerts-history")
async def ops_alerts_history(
    request: Request,
    days: int = Query(7, ge=1, le=30, description="발화 추이 윈도우 (1~30일)."),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """rule 80 (``ops_status_violation``) 단일 룰 7일 발화 진단.

    응답::

        {
          "rule": {"id": 80, "name": "ops_status_violation",
                   "cooldown_sec": 3600, "threshold": 1.0, "is_active": true},
          "days": 7,
          "generated_at": "2026-06-05T...",
          "total_fires": 2,
          "severity_counts": {"critical": 1, "warning": 1, "info": 0},
          "by_day": [
            {"day": "2026-06-05", "critical": 1, "warning": 1, "info": 0, "n": 2}
          ],
          "by_metric": [
            {"metric": "voc_daily_drop_pct", "n": 1,
             "severity": {"critical": 0, "warning": 1, "info": 0},
             "first_fired_at": "...", "last_fired_at": "...",
             "max_value": 87.06, "min_value": 87.06},
            ...
          ],
          "cooldown_violations": [
            {"metric": "...", "gap_seconds": 1800, "first_fired_at": "...",
             "second_fired_at": "..."}
          ],
          "operations_monitor_compare": [
            {"metric": "voc_daily_drop_pct",
             "fires_rule80": 1, "fires_rule78": 9, "dedupe_ratio": 0.11},
            ...
          ],
          "recommendations": [
            "rule 80 발화 0건 (silent) — propagator 자체가 실효성 없음 ...",
            "metric=regression_ok_ratio critical 6+1=7회 동일 reason — ...",
          ]
        }

    - ``cooldown_violations`` 은 동일 metric 의 인접 두 fire 간격이
      ``cooldown_sec`` (기본 3600) 미만인 케이스를 모두 나열.
    - ``operations_monitor_compare`` 는 rule 78 (DB-direct, 매시 30분) 과 rule 80
      (파일 기반, 매시 35분) 의 metric 별 카운트 — propagator 가 실제로 dedupe
      이상을 더 잡아주는지 평가.  ``dedupe_ratio`` = fires_rule80 / fires_rule78.
    """
    _enforce_localhost(request)

    # 룰 메타 (없으면 404).
    rule_row = (await db.execute(text(
        "SELECT id, name, threshold, cooldown_sec, severity, is_active "
        "FROM alert_rules WHERE name = 'ops_status_violation'"
    ))).first()
    if rule_row is None:
        raise HTTPException(status_code=404, detail="rule ops_status_violation 미시드")

    rule_meta: Dict[str, Any] = {
        "id": int(rule_row.id),
        "name": rule_row.name,
        "threshold": float(rule_row.threshold),
        "cooldown_sec": int(rule_row.cooldown_sec),
        "severity_default": rule_row.severity,
        "is_active": bool(rule_row.is_active),
    }
    cooldown_sec = rule_meta["cooldown_sec"] or 3600

    # 1) 발화 raw fetch.
    rows = (await db.execute(_OPS_ALERTS_HISTORY_SQL, {"days": int(days)})).all()
    events: List[Dict[str, Any]] = []
    sev_counts = {"critical": 0, "warning": 0, "info": 0}
    by_day: Dict[str, Dict[str, int]] = {}
    by_metric: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        sev = str(r.severity or "info").lower()
        if sev not in sev_counts:
            sev_counts[sev] = 0
        sev_counts[sev] += 1
        d = r.fired_at.date().isoformat()
        slot = by_day.setdefault(d, {"day": d, "critical": 0, "warning": 0, "info": 0, "n": 0})
        slot[sev] = slot.get(sev, 0) + 1
        slot["n"] += 1

        metric = r.metric or "unknown"
        mslot = by_metric.setdefault(metric, {
            "metric": metric,
            "n": 0,
            "severity": {"critical": 0, "warning": 0, "info": 0},
            "first_fired_at": None,
            "last_fired_at": None,
            "max_value": None,
            "min_value": None,
            "fired_ats": [],
        })
        mslot["n"] += 1
        mslot["severity"][sev] = mslot["severity"].get(sev, 0) + 1
        ts = r.fired_at
        mslot["fired_ats"].append(ts)
        if mslot["first_fired_at"] is None or ts < mslot["first_fired_at"]:
            mslot["first_fired_at"] = ts
        if mslot["last_fired_at"] is None or ts > mslot["last_fired_at"]:
            mslot["last_fired_at"] = ts
        val = r.value
        if val is not None:
            if mslot["max_value"] is None or float(val) > mslot["max_value"]:
                mslot["max_value"] = float(val)
            if mslot["min_value"] is None or float(val) < mslot["min_value"]:
                mslot["min_value"] = float(val)
        events.append({
            "id": int(r.id),
            "fired_at": ts.isoformat(),
            "severity": sev,
            "metric": metric,
            "value": float(r.value) if r.value is not None else None,
            "threshold": float(r.threshold) if r.threshold is not None else None,
            "reason": r.reason,
        })

    # 2) metric 단위 cooldown 위반 감지.
    cooldown_violations: List[Dict[str, Any]] = []
    for metric, mslot in by_metric.items():
        ts_sorted = sorted(mslot["fired_ats"])
        for prev, cur in zip(ts_sorted, ts_sorted[1:]):
            gap = (cur - prev).total_seconds()
            if gap < cooldown_sec:
                cooldown_violations.append({
                    "metric": metric,
                    "gap_seconds": round(gap, 2),
                    "cooldown_sec": cooldown_sec,
                    "first_fired_at": prev.isoformat(),
                    "second_fired_at": cur.isoformat(),
                })

    # by_metric 응답에서 fired_ats 키 제거 (내부 임시).
    by_metric_list: List[Dict[str, Any]] = []
    for v in by_metric.values():
        out = {k: x for k, x in v.items() if k != "fired_ats"}
        if out["first_fired_at"] is not None:
            out["first_fired_at"] = out["first_fired_at"].isoformat()
        if out["last_fired_at"] is not None:
            out["last_fired_at"] = out["last_fired_at"].isoformat()
        by_metric_list.append(out)
    by_metric_list.sort(key=lambda x: (-x["n"], x["metric"]))

    by_day_list = sorted(by_day.values(), key=lambda x: x["day"], reverse=True)

    # 3) rule 78 (operations_monitor) 비교.
    cmp_rows = (await db.execute(_OPS_MONITOR_COMPARE_SQL, {"days": int(days)})).all()
    rule78_counts: Dict[str, int] = {(r.metric or "unknown"): int(r.n or 0) for r in cmp_rows}
    metrics_all = set(rule78_counts.keys()) | set(by_metric.keys())
    compare_list: List[Dict[str, Any]] = []
    for m in sorted(metrics_all):
        n80 = by_metric.get(m, {}).get("n", 0)
        n78 = rule78_counts.get(m, 0)
        ratio: Optional[float]
        if n78 > 0:
            ratio = round(n80 / n78, 3)
        else:
            ratio = None
        compare_list.append({
            "metric": m,
            "fires_rule80": int(n80),
            "fires_rule78": int(n78),
            "dedupe_ratio": ratio,
        })

    # 4) 권고 생성.
    recs: List[str] = []
    if not events:
        recs.append(
            f"rule 80 발화 0건 ({days}일) — propagator 가 ops_status JSON 의 "
            "violations 를 한 건도 처리하지 않음. (a) ops_status 파일 부재, "
            "(b) ops_status status=ok 지속, (c) celery task 미가동 중 점검."
        )
    else:
        # 동일 reason 반복 권고.
        for m, mslot in by_metric.items():
            if mslot["n"] >= 3:
                cur_critical = mslot["severity"].get("critical", 0)
                cur_warning = mslot["severity"].get("warning", 0)
                paired_78 = rule78_counts.get(m, 0)
                recs.append(
                    f"metric={m} rule80 {mslot['n']}회 (critical {cur_critical}/"
                    f"warning {cur_warning}) + rule78 {paired_78}회 — "
                    "동일 reason 반복일 가능성. metric 단위 cooldown 또는 reason hash "
                    "dedupe 도입 검토."
                )
        if cooldown_violations:
            recs.append(
                f"cooldown 위반 의심 {len(cooldown_violations)}건 — "
                f"cooldown_sec={cooldown_sec}s 보다 짧은 간격 재발화. "
                "metric 별 last_fired_per_metric 가드 점검."
            )
        # 다양성 부족.
        if len(by_metric_list) == 1 and events:
            recs.append(
                f"7일간 단일 metric ({by_metric_list[0]['metric']}) 만 발화 — "
                "다른 metric 임계(grounding_min/topic_drop_pct) 가 과도하게 느슨한지 점검."
            )
    if not recs:
        recs.append("이상 패턴 없음 — 임계·cooldown 현행 유지.")

    return {
        "rule": rule_meta,
        "days": int(days),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_fires": len(events),
        "severity_counts": sev_counts,
        "by_day": by_day_list,
        "by_metric": by_metric_list,
        "cooldown_violations": cooldown_violations,
        "operations_monitor_compare": compare_list,
        "recommendations": recs,
        "events": events,
    }


# ── 통합 검색 (Track E — CommandPalette) ──────────────────────────────────
# CommandPalette 가 client-side fuse 만으로는 신규 키워드/카테고리 매칭이 약해
# 백엔드 통합 인덱스로 정확도를 끌어올린다.
#
# 4 도메인을 단일 응답으로 반환:
#   - products       : ILIKE on code/name_en/name_ko
#   - platforms      : ILIKE on code/name
#   - categories     : ILIKE on code/name_ko/name_en  (voc_categories)
#   - keywords       : ILIKE on keyword + 빈도(count) (voc_keywords GROUP BY)
#
# score 정책:
#   exact match  = 1.0
#   prefix match = 0.8
#   contains     = 0.5
#   (대소문자/한영 무시. 빈도 가산은 keywords 만 score += min(count/100, 0.2))

_SEARCH_PRODUCTS_SQL = text(
    """
    SELECT code, name_en, name_ko,
           CASE
             WHEN lower(code)    = :q OR lower(name_en) = :q OR lower(coalesce(name_ko,'')) = :q THEN 1.0
             WHEN lower(code)    LIKE :prefix
               OR lower(name_en) LIKE :prefix
               OR lower(coalesce(name_ko,'')) LIKE :prefix THEN 0.8
             ELSE 0.5
           END AS score
    FROM products
    WHERE is_active = TRUE
      AND (lower(code)    LIKE :contains
        OR lower(name_en) LIKE :contains
        OR lower(coalesce(name_ko,'')) LIKE :contains)
    ORDER BY score DESC, code
    LIMIT :limit
    """
)

_SEARCH_PLATFORMS_SQL = text(
    """
    SELECT code, name, region,
           CASE
             WHEN lower(code) = :q OR lower(name) = :q THEN 1.0
             WHEN lower(code) LIKE :prefix OR lower(name) LIKE :prefix THEN 0.8
             ELSE 0.5
           END AS score
    FROM platforms
    WHERE is_active = TRUE
      AND (lower(code) LIKE :contains OR lower(name) LIKE :contains)
    ORDER BY score DESC, code
    LIMIT :limit
    """
)

_SEARCH_CATEGORIES_SQL = text(
    """
    SELECT code, name_ko, name_en,
           CASE
             WHEN lower(code) = :q
               OR lower(coalesce(name_ko,'')) = :q
               OR lower(coalesce(name_en,'')) = :q THEN 1.0
             WHEN lower(code) LIKE :prefix
               OR lower(coalesce(name_ko,'')) LIKE :prefix
               OR lower(coalesce(name_en,'')) LIKE :prefix THEN 0.8
             ELSE 0.5
           END AS score
    FROM voc_categories
    WHERE lower(code) LIKE :contains
       OR lower(coalesce(name_ko,'')) LIKE :contains
       OR lower(coalesce(name_en,'')) LIKE :contains
    ORDER BY score DESC, code
    LIMIT :limit
    """
)

# voc_keywords 는 row 가 voc_id 단위라 GROUP BY 후 count 집계.
# score 는 정확/prefix/contains + 빈도 정규화(min(count/100, 0.2)) 합산.
_SEARCH_KEYWORDS_SQL = text(
    """
    SELECT keyword, lang, count(*) AS cnt,
           CASE
             WHEN lower(keyword) = :q                 THEN 1.0
             WHEN lower(keyword) LIKE :prefix         THEN 0.8
             ELSE                                          0.5
           END AS base_score
    FROM voc_keywords
    WHERE lower(keyword) LIKE :contains
    GROUP BY keyword, lang
    ORDER BY base_score DESC, cnt DESC
    LIMIT :limit
    """
)


from app.core.cache import redis_cache  # noqa: E402  (모듈 하단 — 다른 endpoint 와 격리)


@redis_cache(ttl_seconds=60, key_prefix="search:")
async def _search_query(db: AsyncSession, q: str, limit: int) -> Dict[str, Any]:
    """SQL 4종 실행. _enforce_localhost 는 라우터에서 처리."""
    ql = q.strip().lower()
    params = {
        "q": ql,
        "prefix": f"{ql}%",
        "contains": f"%{ql}%",
        "limit": int(limit),
    }
    products_rows = (await db.execute(_SEARCH_PRODUCTS_SQL, params)).all()
    platforms_rows = (await db.execute(_SEARCH_PLATFORMS_SQL, params)).all()
    categories_rows = (await db.execute(_SEARCH_CATEGORIES_SQL, params)).all()
    keywords_rows = (await db.execute(_SEARCH_KEYWORDS_SQL, params)).all()
    products = [
        {
            "code": r.code,
            "name_ko": r.name_ko or r.name_en,
            "score": round(float(r.score), 3),
        }
        for r in products_rows
    ]
    platforms = [
        {
            "code": r.code,
            "name": r.name,
            "region": r.region,
            "score": round(float(r.score), 3),
        }
        for r in platforms_rows
    ]
    categories = [
        {
            "code": r.code,
            "name_ko": r.name_ko or r.name_en or r.code,
            "score": round(float(r.score), 3),
        }
        for r in categories_rows
    ]
    keywords = [
        {
            "keyword": r.keyword,
            "lang": r.lang,
            "count": int(r.cnt),
            "score": round(
                min(float(r.base_score) + min(int(r.cnt) / 100.0, 0.2), 1.0), 3
            ),
        }
        for r in keywords_rows
    ]
    return {
        "q": q,
        "products": products,
        "platforms": platforms,
        "categories": categories,
        "keywords": keywords,
    }


@router.get("/search")
async def global_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=80, description="검색어 (1~80자)."),
    limit: int = Query(15, ge=1, le=50, description="도메인별 결과 상한."),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """CommandPalette 통합 검색 — products / platforms / categories / keywords.

    - 4 도메인의 ILIKE 검색을 단일 응답으로 제공.
    - score 는 exact=1.0 / prefix=0.8 / contains=0.5. keywords 만 빈도 가산(+up to 0.2).
    - ``@redis_cache(ttl=60, prefix='search:')`` — 단축키 반복 입력 시 응답 ms 단위.
    - localhost only (nginx 가 외부 차단하는 _internal 경로).

    응답::

        {
          "q": "갤럭시",
          "products":   [{"code","name_ko","score"}],
          "platforms":  [{"code","name","region","score"}],
          "categories": [{"code","name_ko","score"}],
          "keywords":   [{"keyword","lang","count","score"}]
        }
    """
    _enforce_localhost(request)
    return await _search_query(db, q.strip(), int(limit))


# ── 알림 운영 모니터링 (Track A — alert-monitor) ──────────────────────────
# /alert-trends 의 상위 집계 + 룰별 health 판정 + metric 분포 + 권고 자동 생성.
# Frontend Alerts 페이지의 "운영 모니터링" 섹션과 daily quality_report 가 공통 사용.
#
# health 판정 룰:
#   - violating : 24h 내 cooldown 위반 1회 이상 (cooldown_sec 보다 짧은 간격으로 재발화)
#   - silent    : days 윈도우 내 0 발화
#   - noisy     : 24h fires_24h > max(20, 7d 평균*2)  → 임계 너무 낮음 의심
#   - normal    : 그 외
_ALERT_MONITOR_RULES_SQL = text(
    """
    SELECT r.id, r.name, r.metric_path, r.threshold, r.cooldown_sec, r.severity,
           count(e.id) FILTER (WHERE e.fired_at >= now() - make_interval(days => :days)) AS fires_window,
           count(e.id) FILTER (WHERE e.fired_at >= now() - interval '24 hours') AS fires_24h,
           avg(e.value) FILTER (WHERE e.fired_at >= now() - make_interval(days => :days)) AS avg_value,
           max(e.value) FILTER (WHERE e.fired_at >= now() - make_interval(days => :days)) AS max_value,
           max(e.fired_at) AS last_fired_at
    FROM alert_rules r
    LEFT JOIN alert_events e ON e.rule_id = r.id
    WHERE r.is_active = TRUE
    GROUP BY r.id
    ORDER BY r.id
    """
)

# 룰별 cooldown 위반 카운트 (24h) — 직전 발화와 간격이 cooldown_sec 미만.
_ALERT_MONITOR_VIOL_BY_RULE_SQL = text(
    """
    WITH gaps AS (
        SELECT e.rule_id,
               e.fired_at,
               lag(e.fired_at) OVER (PARTITION BY e.rule_id ORDER BY e.fired_at) AS prev_fired,
               r.cooldown_sec
        FROM alert_events e
        JOIN alert_rules r ON r.id = e.rule_id
        WHERE e.fired_at >= now() - interval '24 hours'
    )
    SELECT rule_id, count(*) AS n
    FROM gaps
    WHERE prev_fired IS NOT NULL
      AND EXTRACT(EPOCH FROM (fired_at - prev_fired)) < cooldown_sec
    GROUP BY rule_id
    """
)

# metric_path 별 alert_events.value percentile (윈도우 내).
# 같은 metric_path 를 공유하는 룰의 발화 분포를 통합 — 임계 재조정 근거.
_ALERT_MONITOR_METRIC_DIST_SQL = text(
    """
    SELECT r.metric_path,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY e.value) AS p50,
           percentile_cont(0.90) WITHIN GROUP (ORDER BY e.value) AS p90,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY e.value) AS p95,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY e.value) AS p99,
           count(*) AS n
    FROM alert_events e
    JOIN alert_rules r ON r.id = e.rule_id
    WHERE e.fired_at >= now() - make_interval(days => :days)
    GROUP BY r.metric_path
    """
)


def _classify_alert_health(
    fires_window: int,
    fires_24h: int,
    cooldown_violations_24h: int,
) -> str:
    """알림 룰별 health 판정.  우선순위: violating > silent > noisy > normal.

    이름이 ``_classify_health`` (수집 채널) 와 충돌하지 않도록 ``_alert_`` prefix.
    """
    if cooldown_violations_24h > 0:
        return "violating"
    if fires_window == 0:
        return "silent"
    avg_per_day = (fires_window / 7.0) if fires_window > 0 else 0.0
    noisy_threshold = max(20.0, avg_per_day * 2.0)
    if fires_24h > noisy_threshold:
        return "noisy"
    return "normal"


def _build_recommendations(
    rules: List[Dict[str, Any]],
    days: int,
    cooldown_violations_24h: int,
) -> List[str]:
    """운영자가 즉시 조치할 권고 문장 생성 (한국어)."""
    out: List[str] = []
    for r in rules:
        if r["health"] == "silent":
            out.append(
                f"rule {r['rule_id']} (`{r['name']}`) 임계 검토 — {days}d silent "
                f"(threshold={r['threshold']})"
            )
        elif r["health"] == "noisy":
            mx = r.get("max_value_7d")
            mx_str = f"{mx:.4f}" if isinstance(mx, (int, float)) else "?"
            out.append(
                f"rule {r['rule_id']} (`{r['name']}`) 임계 {r['threshold']} 검토 — "
                f"24h {r['fires_24h']}회 발화 (max {mx_str})"
            )
        elif r["health"] == "violating":
            out.append(
                f"rule {r['rule_id']} (`{r['name']}`) cooldown 위반 "
                f"{r['cooldown_violations_24h']}건 — cooldown_sec={r['cooldown_sec']}s "
                f"과대평가 또는 평가 주기 점검"
            )
    if cooldown_violations_24h > 0 and not any("cooldown" in s for s in out):
        out.append(
            f"24h cooldown 위반 의심 {cooldown_violations_24h}건 — Celery beat 주기 점검"
        )
    return out


@redis_cache(ttl_seconds=120, key_prefix="alert_monitor:")
async def _alert_monitor_payload(db: AsyncSession, days: int) -> Dict[str, Any]:
    """SQL 실행 + health/recommendations 합성.  _enforce_localhost 는 라우터에서 처리."""
    rule_rows = (await db.execute(_ALERT_MONITOR_RULES_SQL, {"days": days})).all()
    viol_rows = (await db.execute(_ALERT_MONITOR_VIOL_BY_RULE_SQL)).all()
    viol_by_rule: Dict[int, int] = {int(r.rule_id): int(r.n or 0) for r in viol_rows}
    dist_rows = (await db.execute(_ALERT_MONITOR_METRIC_DIST_SQL, {"days": days})).all()

    # 현재 metric 값 (best-effort — 실패해도 distribution 만 반환)
    current_by_metric: Dict[str, Optional[float]] = {}
    try:
        from app.api.alerts import collect_metrics  # 지연 import (순환 회피)
        current_by_metric = {
            k: (float(v) if v is not None else None)
            for k, v in (await collect_metrics(db)).items()
        }
    except Exception:
        current_by_metric = {}

    def _round_or_none(v: Any, n: int = 4) -> Optional[float]:
        if v is None:
            return None
        try:
            return round(float(v), n)
        except (TypeError, ValueError):
            return None

    rules: List[Dict[str, Any]] = []
    fires_24h_total = 0
    fires_7d_total = 0
    for r in rule_rows:
        rid = int(r.id)
        fires_window = int(r.fires_window or 0)
        fires_24h = int(r.fires_24h or 0)
        violations = int(viol_by_rule.get(rid, 0))
        avg_v = _round_or_none(r.avg_value, 4)
        max_v = _round_or_none(r.max_value, 4)
        health = _classify_alert_health(fires_window, fires_24h, violations)
        rules.append({
            "rule_id": rid,
            "name": r.name,
            "metric_path": r.metric_path,
            "threshold": float(r.threshold),
            "cooldown_sec": int(r.cooldown_sec),
            "severity": r.severity,
            "fires_24h": fires_24h,
            "fires_7d": fires_window,
            "avg_value_7d": avg_v,
            "max_value_7d": max_v,
            "last_fired_at": (r.last_fired_at.isoformat() if r.last_fired_at else None),
            "cooldown_violations_24h": violations,
            "silent_window": fires_window == 0,
            "health": health,
        })
        fires_24h_total += fires_24h
        fires_7d_total += fires_window

    metric_distribution: Dict[str, Dict[str, Any]] = {}
    for r in dist_rows:
        mp = r.metric_path
        metric_distribution[mp] = {
            "p50": _round_or_none(r.p50, 4),
            "p90": _round_or_none(r.p90, 4),
            "p95": _round_or_none(r.p95, 4),
            "p99": _round_or_none(r.p99, 4),
            "n": int(r.n or 0),
            "current": _round_or_none(current_by_metric.get(mp), 4),
        }
    # 활성 룰이 참조하는 metric_path 중 분포가 없는 것도 current 만이라도 채움
    for rule in rules:
        mp = rule["metric_path"]
        if mp not in metric_distribution:
            metric_distribution[mp] = {
                "p50": None, "p90": None, "p95": None, "p99": None, "n": 0,
                "current": _round_or_none(current_by_metric.get(mp), 4),
            }

    cooldown_violations_24h_total = sum(viol_by_rule.values())
    recommendations = _build_recommendations(
        rules, days=days, cooldown_violations_24h=cooldown_violations_24h_total,
    )

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "active_rules": len(rules),
            "fires_24h": fires_24h_total,
            "fires_7d": fires_7d_total,
            "cooldown_violations_24h": cooldown_violations_24h_total,
        },
        "rules": rules,
        "metric_distribution": metric_distribution,
        "recommendations": recommendations,
    }


@router.get("/alert-monitor")
async def alert_monitor(
    request: Request,
    days: int = Query(7, ge=1, le=30, description="발화 추이 윈도우 (1~30일)."),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """알림 운영 모니터링 — health 판정 + metric 분포 + 권고 자동 생성.

    /alert-trends 의 상위 집계로, 운영자 한 화면에서 다음을 확인:
      - summary: active_rules / fires_24h / fires_7d / cooldown_violations_24h
      - rules[]: 각 활성 룰의 fires / avg / max / last_fired + health (4종)
      - metric_distribution: metric_path 별 p50/p90/p95/p99 + 현재 값
      - recommendations: silent/noisy/violating 룰별 자동 권고 문장 (한국어)

    health 분류:
      - normal    : 정상 (24h 발화가 평균 ±2× 이내)
      - silent    : 윈도우 0 발화 → 임계 너무 높음 가능성
      - noisy     : 24h 발화가 7d 평균*2 초과 + 절대 20+ → 임계 너무 낮음
      - violating : cooldown_sec 보다 짧은 간격 재발화 1회 이상

    localhost only. ``@redis_cache(ttl=120, key_prefix='alert_monitor:')``.
    """
    _enforce_localhost(request)
    return await _alert_monitor_payload(db, int(days))


# ── Drive 백업 검증 상태 (Track E — backup verification) ──────────────────
# scripts/drive-sync/verify-backup.sh 가 매일 last_verified.json 을 남긴다.
# 본 endpoint 는 그 JSON 을 그대로 노출 — frontend 가 "백업 안전" 카드로 표시,
# Celery beat 의 verify_backup task 가 ok=false 면 alert_events INSERT.
_BACKUP_STATE_DEFAULT = Path("/home/koopark/claude/SignalForge/backups/last_verified.json")


def _backup_state_path() -> Path:
    """환경변수 ``BACKUP_STATE_FILE`` 우선, 없으면 ``backups/last_verified.json``."""
    env = os.getenv("BACKUP_STATE_FILE", "").strip()
    return Path(env) if env else _BACKUP_STATE_DEFAULT


@router.get("/backup-status")
def backup_status(request: Request) -> dict:
    """Drive 백업 최근 검증 결과.

    응답 (verify-backup.sh 출력 그대로 + ``available`` 플래그)::

        {
          "available": true,
          "ok": true,
          "verified_at": "2026-06-03T15:44:55Z",
          "reason": "ok",
          "drive_path": "ApptainerImages:SignalForge/db-dumps",
          "file": "sf-db-20260602-043001Z.sql.gz",
          "size_bytes": 23837479,
          "mtime": "2026-06-02T04:30:26.771Z",
          "age_hours": 35,
          "max_age_hours": 48,
          "min_size_bytes": 1048576,
          "sha256": "92bc4412..."
        }

    상태 파일이 아직 없으면 ``{"available": false, "ok": null}`` 만 반환
    (verify-backup.sh 가 한 번도 안 돌았다는 의미).
    """
    _enforce_localhost(request)
    path = _backup_state_path()
    if not path.exists():
        return {"available": False, "ok": None, "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"available": False, "ok": None, "error": "state not a JSON object"}
    except Exception as e:
        return {"available": False, "ok": None, "error": f"parse fail: {e}"}
    return {"available": True, **data}


# ── 통합 키 상태 (Harvest 3 Track E — key-status) ─────────────────────────
# Groq (external LLM) + Slack (alerts) 두 통합 키의 입력 여부와 도달 가능 여부를
# 한 응답으로 노출. crawler/scripts/key_health_check.py 가 cross-check 용으로 호출.
#
# 운영자 시각의 단순 모델:
#   slack:
#     configured: ALERT_WEBHOOK_URL 또는 SLACK_WEBHOOK_URL 둘 중 하나 비어있지 않음
#     enabled:    위 값이 https://hooks.slack.com/ 으로 시작 (형태 검증)
#     dry_run:    not enabled
#   groq (= external LLM tier):
#     configured: EXTERNAL_API_KEY + EXTERNAL_BASE_URL + EXTERNAL_MODEL 3 슬롯
#     reachable:  ping=true 일 때만 external provider 1회 호출 결과
#
# 보안: localhost only. 키 자체는 절대 노출 안 함 (redacted prefix/suffix 만).
def _redact_key(val: str) -> Optional[str]:
    """API 키를 첫 4글자 + 마지막 4글자만 남기고 마스킹. 8자 이하면 '***'."""
    if not val:
        return None
    v = val.strip()
    if len(v) <= 8:
        return "***"
    return f"{v[:4]}...{v[-4:]}"


def _slack_url_status() -> Dict[str, Any]:
    """ALERT_WEBHOOK_URL 우선, SLACK_WEBHOOK_URL fallback. 키 자체는 redact."""
    raw = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()
    source = "ALERT_WEBHOOK_URL"
    if not raw:
        raw = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()
        source = "SLACK_WEBHOOK_URL" if raw else "none"
    configured = bool(raw)
    # Slack incoming webhook 형식 (대략): https://hooks.slack.com/services/T.../B.../...
    enabled = configured and raw.startswith("https://hooks.slack.com/")
    return {
        "configured": configured,
        "enabled": bool(enabled),
        "dry_run": not bool(enabled),
        "source": source,
        "url_redacted": (f"{raw[:30]}...{raw[-4:]}" if len(raw) > 40 else ("***" if raw else None)),
        "provider": (os.getenv("ALERT_PROVIDER") or "slack").strip() or "slack",
        "channel": (os.getenv("SLACK_CHANNEL") or "").strip() or None,
    }


def _groq_status(do_ping: bool) -> Dict[str, Any]:
    """external LLM 3 슬롯 + 선택적 ping. external tier 가 곧 Groq 권장 경로."""
    api_key = (os.getenv("EXTERNAL_API_KEY") or "").strip()
    base_url = (os.getenv("EXTERNAL_BASE_URL") or "").strip()
    model = (os.getenv("EXTERNAL_MODEL") or "").strip()
    triplet_ok = bool(api_key and base_url and model)
    missing = [k for k, v in (
        ("EXTERNAL_API_KEY", api_key),
        ("EXTERNAL_BASE_URL", base_url),
        ("EXTERNAL_MODEL", model),
    ) if not v]
    out: Dict[str, Any] = {
        "configured": triplet_ok,
        "missing": missing,
        "api_key_redacted": _redact_key(api_key),
        "base_url": base_url or None,
        "model": model or None,
        "reachable": None,
    }
    if triplet_ok and do_ping:
        # external provider 를 통해 1회 ping — _build_tier_info(external) 의 reachable 재사용.
        ext = _build_tier_info("external", do_ping=True)
        out["reachable"] = ext.get("reachable")
        out["tier_label"] = ext.get("tier_label")
    return out


@router.get("/key-status")
def key_status(request: Request, ping: bool = False) -> dict:
    """Groq (external LLM) + Slack 통합 키 상태 — Harvest 3 Track E.

    한 응답에 두 통합 키의 입력/도달 가능 여부를 묶어 노출, 운영자가
    `.env` 한 번 편집 후 즉시 검증.  `crawler/scripts/key_health_check.py` 가
    이 endpoint 를 호출해 직접 외부 호출 결과와 cross-check 한다.

    ping=false (기본):
        - groq:   3 슬롯 채워졌는지만 검사 (외부 호출 없음)
        - slack:  ALERT_WEBHOOK_URL / SLACK_WEBHOOK_URL 형태만 검사
    ping=true:
        - groq:   external provider 로 'ping' 1회 (max_tokens 짧음) → reachable
        - slack:  네트워크 호출 안 함 — webhook 노이즈 방지.  Slack 측 ping 은
                  ``POST /api/v1/alerts/channels/slack/test`` 를 별도 호출.

    응답::

        {
          "generated_at": "2026-06-06T...",
          "ping": false,
          "groq": {
            "configured": true|false,
            "missing": ["EXTERNAL_MODEL"],   # 비었을 때만
            "api_key_redacted": "gsk_...ABCD" | null,
            "base_url": "https://api.groq.com/openai/v1" | null,
            "model": "llama-3.3-70b-versatile" | null,
            "reachable": true|false|null,    # ping=true 일 때만 채워짐
            "tier_label": "external:llama-3.3-70b-versatile"
          },
          "slack": {
            "configured": true|false,
            "enabled": true|false,
            "dry_run": true|false,
            "source": "ALERT_WEBHOOK_URL"|"SLACK_WEBHOOK_URL"|"none",
            "url_redacted": "https://hooks.slack.com/services/T.../...XYZ" | null,
            "provider": "slack",
            "channel": null
          },
          "summary": {
            "groq_ok": true|false,
            "slack_ok": true|false,
            "all_ok": true|false
          }
        }

    keys 자체는 절대 응답에 포함되지 않는다 (redacted 만).
    localhost only.
    """
    _enforce_localhost(request)
    slack = _slack_url_status()
    groq = _groq_status(do_ping=bool(ping))
    # ping=false 일 때는 'configured' 만으로 ok 판정 — 운영자가 일단 입력만 했는지 확인.
    # ping=true 일 때는 reachable 까지 통과해야 ok.
    groq_ok = bool(groq["configured"] and (groq["reachable"] is True if ping else True))
    slack_ok = bool(slack["enabled"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ping": bool(ping),
        "groq": groq,
        "slack": slack,
        "summary": {
            "groq_ok": groq_ok,
            "slack_ok": slack_ok,
            "all_ok": groq_ok and slack_ok,
        },
    }


@router.get("/noise-scan")
async def noise_scan(
    request: Request,
    platform: Optional[str] = Query(
        None, description="플랫폼 code 필터 (예: instiz, dcinside). 미지정 시 전체."
    ),
    hours: int = Query(24, ge=1, le=720, description="조회 시간 윈도우 (1~720h)."),
    min_repeat: int = Query(
        5, ge=2, le=10000, description="동일 본문 최소 반복 횟수."
    ),
    limit: int = Query(20, ge=1, le=200, description="결과 개수 상한."),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """동일 본문 반복 패턴 TOP-N 스캔.

    - 지난 ``hours`` 시간 윈도우 내 ``voc_records`` 를 본문 prefix(80자) 기준 GROUP BY.
    - ``min_repeat`` 회 이상 등장한 패턴만 반환.
    - 운영자가 신규 노이즈 패턴(템플릿 문구, 페이지네이션 UI 텍스트 등) 조기 발견용.
    - Instiz 필터 영구화 효과 측정: ``platform=instiz`` 로 호출 시
      '회원만 볼 수 있는 글입니다' / '1시간 내 작성된 글입니다' 가 결과에 없어야 정상.

    응답: ``{"hours", "min_repeat", "platform", "count", "patterns": [...]}``
      patterns[i] = ``{"platform", "preview", "n"}``
    """
    _enforce_localhost(request)
    result = await db.execute(
        _NOISE_SCAN_SQL,
        {
            "platform": platform,
            "hours": int(hours),
            "min_repeat": int(min_repeat),
            "limit": int(limit),
        },
    )
    patterns = [
        {"platform": row.platform, "preview": row.preview, "n": int(row.n)}
        for row in result
    ]
    return {
        "hours": int(hours),
        "min_repeat": int(min_repeat),
        "platform": platform,
        "count": len(patterns),
        "patterns": patterns,
    }


# ── 수집 채널 모니터링 (Track B) ──────────────────────────────────────────
# 60+ 사이트별 수집량/지연/실패를 한 화면에서 보기 위한 단일 endpoint.
# health 분류 — 운영자가 즉시 우선순위 판단 가능:
#   active : 직전 1h 신규 > 0
#   slow   : 24h 신규 < (7d 평균/일 × 0.5) — 떨어지는 중
#   stale  : 24h 0 but 7d 합계 > 0 — 잠시 멈춤
#   dead   : 7d 0 — 완전 중단 (비활성 플랫폼 포함)

_COLLECTION_STATUS_SQL = text(
    """
    WITH base AS (
        SELECT pl.id, pl.code, pl.name, pl.region, pl.is_active,
               count(v.id) FILTER (WHERE v.collected_at >= now() - make_interval(hours => :hours)) AS records_24h,
               count(v.id) FILTER (WHERE v.collected_at >= now() - interval '1 hour') AS records_1h,
               count(v.id) FILTER (WHERE v.collected_at >= now() - interval '7 days') AS records_7d,
               max(v.collected_at) AS last_collected
        FROM platforms pl
        LEFT JOIN voc_records v ON v.platform_id = pl.id
        GROUP BY pl.id
    )
    SELECT id, code, name, region, is_active,
           records_24h, records_1h, records_7d, last_collected,
           CASE WHEN last_collected IS NULL THEN NULL
                ELSE EXTRACT(EPOCH FROM (now() - last_collected)) / 3600.0
           END AS hours_since_last
    FROM base
    ORDER BY records_24h DESC, code
    """
)


def _classify_health(
    records_1h: int,
    records_24h: int,
    records_7d: int,
    avg_per_day_7d: float,
) -> str:
    """active / slow / stale / dead 분류 — endpoint 문서 규칙 그대로."""
    if records_7d <= 0:
        return "dead"
    if records_24h <= 0:
        return "stale"
    if records_1h > 0:
        # 1h 신규가 있고, 24h 도 평균의 50% 이상이면 active.
        if avg_per_day_7d > 0 and records_24h < avg_per_day_7d * 0.5:
            return "slow"
        return "active"
    # 1h 신규가 0 이지만 24h 가 살아있을 때 — 평균 절반 미만이면 slow.
    if avg_per_day_7d > 0 and records_24h < avg_per_day_7d * 0.5:
        return "slow"
    return "active"


@redis_cache(ttl_seconds=300, key_prefix="collection:")
async def _collection_status_query(db: AsyncSession, hours: int) -> Dict[str, Any]:
    rows = (await db.execute(_COLLECTION_STATUS_SQL, {"hours": int(hours)})).all()
    platforms: List[Dict[str, Any]] = []
    total_active = 0
    total_inactive = 0
    total_24h = 0
    total_1h = 0
    by_region: Dict[str, Dict[str, int]] = {}
    for r in rows:
        records_24h = int(r.records_24h or 0)
        records_1h = int(r.records_1h or 0)
        records_7d = int(r.records_7d or 0)
        avg_per_day_7d = round(records_7d / 7.0, 2)
        health = _classify_health(records_1h, records_24h, records_7d, avg_per_day_7d)
        last_collected = r.last_collected.isoformat() if r.last_collected else None
        hours_since_last = (
            round(float(r.hours_since_last), 2) if r.hours_since_last is not None else None
        )
        platforms.append({
            "code": r.code,
            "name": r.name,
            "region": r.region,
            "is_active": bool(r.is_active),
            "records_24h": records_24h,
            "records_1h": records_1h,
            "records_7d": records_7d,
            "last_collected": last_collected,
            "hours_since_last": hours_since_last,
            "avg_per_day_7d": avg_per_day_7d,
            "health": health,
        })
        if r.is_active:
            total_active += 1
        else:
            total_inactive += 1
        total_24h += records_24h
        total_1h += records_1h
        region = r.region or "UNKNOWN"
        if region not in by_region:
            by_region[region] = {"active": 0, "total": 0, "records_24h": 0}
        by_region[region]["total"] += 1
        if r.is_active:
            by_region[region]["active"] += 1
        by_region[region]["records_24h"] += records_24h
    return {
        "hours": int(hours),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "total_active": total_active,
            "total_inactive": total_inactive,
            "total_records_24h": total_24h,
            "total_records_1h": total_1h,
        },
        "platforms": platforms,
        "by_region": by_region,
    }


@router.get("/collection-status")
async def collection_status(
    request: Request,
    hours: int = Query(24, ge=1, le=168, description="24h 윈도우 (기본 24, 최대 168h)."),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """수집 채널 모니터링 — 60+ 플랫폼 한 화면 (Track B).

    응답::

        {
          "hours": 24,
          "generated_at": "2026-06-03T...",
          "summary": {
            "total_active": 62,
            "total_inactive": 11,
            "total_records_24h": 9239,
            "total_records_1h": 412
          },
          "platforms": [{
            "code","name","region","is_active",
            "records_24h","records_1h","records_7d",
            "last_collected","hours_since_last",
            "avg_per_day_7d",
            "health"  // "active" | "slow" | "stale" | "dead"
          }],
          "by_region": {"KR": {"active":..,"total":..,"records_24h":..}, ...}
        }

    - ``@redis_cache(ttl=300)`` — 5분 캐시 (대시보드 5초 폴링 대비).
    - health: active(1h>0) / slow(24h<평균/2) / stale(24h=0 & 7d>0) / dead(7d=0).
    """
    _enforce_localhost(request)
    return await _collection_status_query(db, int(hours))


# ── 커버리지 상태 (Track E — NULL % 정책 재정의) ─────────────────────────
# voc_records 의 매핑 커버리지를 unmapped_reason 별로 분리하여 보고.
# 진정한 "분석 가능" 비율 = linked + (unmapped 중 non_galaxy 제외 = 분석 가능했어야
# 했지만 모델명이 없어 매칭이 안 된 정상 후기).
#
# unmapped_reason 분류 (crawler/scripts/classify_unmapped.py):
#   - no_model_mention : 모델명 미언급 (정상 후기 — 분석 가능 풀에는 포함)
#   - noise            : 잠금/회원전용/삭제됨 (분석 불가)
#   - too_short        : <10자 (분석 불가)
#   - non_galaxy       : iPhone/Pixel 만 (Samsung 컨텍스트 부재 — 분석 대상 외)
#   - NULL (=unknown)  : 분류기 미실행 또는 매핑 실수 (분석 가능 풀에는 포함)
#
# 신정의 coverage:
#   analyzable = linked + no_model_mention + unknown
#   excluded   = noise + too_short + non_galaxy

_COVERAGE_STATUS_SQL = text(
    """
    SELECT
        count(*) FILTER (WHERE product_id IS NOT NULL) AS linked,
        count(*) FILTER (WHERE product_id IS NULL
                          AND unmapped_reason = 'no_model_mention') AS no_model_mention,
        count(*) FILTER (WHERE product_id IS NULL
                          AND unmapped_reason = 'noise') AS noise,
        count(*) FILTER (WHERE product_id IS NULL
                          AND unmapped_reason = 'too_short') AS too_short,
        count(*) FILTER (WHERE product_id IS NULL
                          AND unmapped_reason = 'non_galaxy') AS non_galaxy,
        count(*) FILTER (WHERE product_id IS NULL
                          AND unmapped_reason IS NULL) AS unknown,
        count(*) AS voc_total
    FROM voc_active
    """
)


@redis_cache(ttl_seconds=300, key_prefix="coverage_status:")
async def _coverage_status_query(db: AsyncSession) -> Dict[str, Any]:
    row = (await db.execute(_COVERAGE_STATUS_SQL)).first()
    if row is None:
        return {
            "voc_total": 0,
            "linked": 0,
            "unmapped": {
                "no_model_mention": 0,
                "noise": 0,
                "too_short": 0,
                "non_galaxy": 0,
                "unknown": 0,
            },
            "analyzable": 0,
            "analyzable_pct": 0.0,
            "linked_pct": 0.0,
            "excluded": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    voc_total = int(row.voc_total or 0)
    linked = int(row.linked or 0)
    no_model_mention = int(row.no_model_mention or 0)
    noise = int(row.noise or 0)
    too_short = int(row.too_short or 0)
    non_galaxy = int(row.non_galaxy or 0)
    unknown = int(row.unknown or 0)
    # analyzable = linked + no_model_mention + unknown (분석 가능 풀)
    analyzable = linked + no_model_mention + unknown
    excluded = noise + too_short + non_galaxy
    analyzable_pct = round(analyzable / voc_total * 100.0, 2) if voc_total > 0 else 0.0
    linked_pct = round(linked / voc_total * 100.0, 2) if voc_total > 0 else 0.0
    return {
        "voc_total": voc_total,
        "linked": linked,
        "unmapped": {
            "no_model_mention": no_model_mention,
            "noise": noise,
            "too_short": too_short,
            "non_galaxy": non_galaxy,
            "unknown": unknown,
        },
        "analyzable": analyzable,
        "analyzable_pct": analyzable_pct,
        "linked_pct": linked_pct,
        "excluded": excluded,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@router.get("/coverage-status")
async def coverage_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """voc_records 커버리지 상태 — Track E NULL % 정책 재정의.

    응답::

        {
          "voc_total": 161491,
          "linked": 32065,
          "unmapped": {
            "no_model_mention": 95000,
            "noise": 158,
            "too_short": 12189,
            "non_galaxy": 8000,
            "unknown": 14079
          },
          "analyzable": 141144,     // linked + no_model_mention + unknown
          "analyzable_pct": 87.40,  // 진정한 분석 가능 비율
          "linked_pct": 19.86,      // 모델 매핑 성공 비율
          "excluded": 20347,        // noise + too_short + non_galaxy
          "generated_at": "2026-06-04T..."
        }

    정책:
      - 기존 단일 metric "NULL %" 대신 5종 분리.
      - ``analyzable_pct`` = 진정한 분석 가능 데이터 비율 (excluded 제외).
      - ``linked_pct`` 는 모델 매핑 성공률 (사전 확장 효과 추적용).
      - ``unknown`` 은 ``classify_unmapped.py`` 미실행 행 — 분류 후 0 수렴 정상.

    localhost only.  5분 캐시.
    """
    _enforce_localhost(request)
    return await _coverage_status_query(db)


# ── 회귀 baseline (Track C — R6/R7/R8 핵심 수치 자동 검증) ───────────────────
# 매칭 회귀 (Note 7, Fold, S22, S25, Buds3) + HN 매칭률 + topic 분류 + products
# 가 다음 라운드에서 손실되지 않는지 자동 감시.
#
# baseline 은 R8 (2026-06-03) 시점 측정값을 코드 상수로 동결.
# threshold 는 자연 변동을 고려해 "절대 떨어지면 안 되는 하한" 으로 설정.
# quality_report (일 09:30 KST) 가 호출 → ok=False 시 alert.

_REGRESSION_SQL = text(
    """
    WITH key_products AS (
        SELECT p.code, p.name_en, count(v.id)::int AS voc
        FROM products p
        LEFT JOIN voc_records v ON v.product_id = p.id
        WHERE p.code IN ('GN7','GZF1','GS22','GS25','GB3')
        GROUP BY p.code, p.name_en
    ),
    hn AS (
        SELECT
            count(v.id)::int AS hn_total,
            count(v.product_id)::int AS hn_linked
        FROM voc_active v
        JOIN platforms pl ON pl.id = v.platform_id
        WHERE pl.code = 'hackernews'
    ),
    topics AS (
        SELECT count(*)::int AS topics_filled
        FROM voc_active
        WHERE array_length(topics, 1) > 0
    ),
    prods AS (
        SELECT count(*)::int AS products_total FROM products
    ),
    voc_tot AS (
        SELECT count(*)::int AS voc_total FROM voc_active
    ),
    hardware_fr AS (
        SELECT count(v.id)::int AS hardware_fr_voc
        FROM voc_active v
        JOIN platforms pl ON pl.id = v.platform_id
        WHERE pl.code = 'hardware_fr'
    ),
    -- R5 Data Grow Track L5 신설: MX 매칭/리치/archive 누계 효과 측정.
    -- mx_match: active voc 중 모바일/스마트워치 관련 키워드 포함 비율
    -- mx_rich:  추가로 content_original >= 100자 (리치 신호)
    -- archive_pct: 한국 5개 사이트 + 노이즈 정책으로 archived_at NOT NULL 처리된 비율
    quality AS (
        SELECT
            count(*) FILTER (WHERE archived_at IS NULL) AS active,
            count(*) FILTER (WHERE archived_at IS NULL AND content_original ~* :mx_pat) AS mx_match,
            count(*) FILTER (WHERE archived_at IS NULL AND content_original ~* :mx_pat AND length(content_original) >= 100) AS mx_rich,
            count(*) FILTER (WHERE archived_at IS NOT NULL) AS archived,
            count(*) AS voc_records_total
        FROM voc_records
    ),
    alembic AS (
        SELECT version_num FROM alembic_version LIMIT 1
    )
    SELECT
        (SELECT json_agg(json_build_object(
            'code', code, 'name_en', name_en, 'voc', voc
        )) FROM key_products) AS key_products_json,
        (SELECT hn_total FROM hn) AS hn_total,
        (SELECT hn_linked FROM hn) AS hn_linked,
        (SELECT topics_filled FROM topics) AS topics_filled,
        (SELECT products_total FROM prods) AS products_total,
        (SELECT voc_total FROM voc_tot) AS voc_total,
        (SELECT hardware_fr_voc FROM hardware_fr) AS hardware_fr_voc,
        (SELECT active FROM quality) AS quality_active,
        (SELECT mx_match FROM quality) AS quality_mx_match,
        (SELECT mx_rich FROM quality) AS quality_mx_rich,
        (SELECT archived FROM quality) AS quality_archived,
        (SELECT voc_records_total FROM quality) AS quality_voc_records_total,
        (SELECT version_num FROM alembic) AS alembic_head
    """
)

# baseline = R8 시점 측정값.  threshold = 절대 하한 (자연 변동 허용).
# R12 (2026-06-04) baseline 추가 — R11 메인 완료 후 갱신:
#   GN7=366  GZF1=308  GS22=427  GS25=2122  GB3=531
#   HN total=33,911  linked%=22.48
#   topics_filled=25,410 (R8 42,935 → 신규 backfill 25k 도래로 미분류 비율 증가)
#   products=389  voc_total=167,701  alembic=0014
# 일부 baseline (특히 topics_filled) 은 R8→R12 사이 데이터 모집단이 바뀌어
# 단순 비교가 무의미.  threshold 는 *절대 하한* 만 유지하고 baseline 은 *추이 표시*
# 용도로 둘 다 응답에 노출.
#
# R20 (2026-06-05) baseline 추가 — R14 dedup (168k → 113k) 이후 안정화된 *post-dedup*
# 모집단의 실 측정값. 핵심: R12 baseline 은 *dedup 전* 값이라 dedup 으로 진정한 중복이
# 제거된 현 corpus 와 직접 비교가 부정확. threshold 는 *R20 corpus* 의 절대 하한으로
# 갱신하여 정상 운영을 OK 로 표시. R8/R12 비교 정보는 추이 표시 용도로 유지.
#   GN7=387 GZF1=281 GS22=218 GS25=847 GB3=210
#   HN total=34,253 linked%=22.19
#   topics_filled=104,184 (R13 백필로 92%+ 분류율 회복)
#   products=389 voc_total=117,958 alembic=0018
# threshold 정책 (R20):
#   - GS22/GS25/GB3: dedup 으로 *수집 시점에 비해* 진정한 중복이 제거된 결과.
#     하한을 post-dedup 실측값 - ~10% 안전 마진으로 조정.
#   - voc_total: 110,000 (현 117,958, post-dedup 안전 하한)
_REGRESSION_PRODUCT_BASELINES = {
    "GN7":  {"threshold": 300,  "baseline_r8": 352,  "baseline_r12": 366,  "baseline_r20": 387, "label": "Galaxy Note 7"},
    "GZF1": {"threshold": 250,  "baseline_r8": 302,  "baseline_r12": 308,  "baseline_r20": 281, "label": "Galaxy Fold"},
    "GS22": {"threshold": 200,  "baseline_r8": 414,  "baseline_r12": 427,  "baseline_r20": 218, "label": "Galaxy S22"},
    "GS25": {"threshold": 800,  "baseline_r8": 2082, "baseline_r12": 2122, "baseline_r20": 847, "label": "Galaxy S25"},
    "GB3":  {"threshold": 200,  "baseline_r8": 511,  "baseline_r12": 531,  "baseline_r20": 210, "label": "Galaxy Buds3"},
}
_HN_LINKED_PCT_THRESHOLD = 15.0
_HN_LINKED_PCT_BASELINE_R8 = 22.67
_HN_LINKED_PCT_BASELINE_R12 = 22.48
_HN_LINKED_PCT_BASELINE_R20 = 22.19
# R12: HN backfill 으로 hn_total 33,911 까지 확장 — 절대 하한 추가
_HN_TOTAL_THRESHOLD = 30_000
_HN_TOTAL_BASELINE_R12 = 33_911
_HN_TOTAL_BASELINE_R20 = 34_253
# topics: R8 42,935 → R12 25,410 (분모 voc 가 증가했고 신규 25k 는 reprocess 전).
# R12 운영 정책: 임계 20,000 으로 *현실적 절대 하한*. R8 비교 정보는 baseline 으로 노출.
# R20: R13 backfill 로 topics_filled 104,184 (분류율 88.5%) 회복.
_TOPICS_FILLED_THRESHOLD = 20_000
_TOPICS_FILLED_BASELINE_R8 = 42_935
_TOPICS_FILLED_BASELINE_R12 = 25_410
_TOPICS_FILLED_BASELINE_R20 = 104_184
_PRODUCTS_THRESHOLD = 380
_PRODUCTS_BASELINE_R8 = 389
_PRODUCTS_BASELINE_R12 = 389
_PRODUCTS_BASELINE_R20 = 389
# R12: voc 전체 모집단 — 절대 하한.  R12 167,701 → R14 dedup → R20 117,958 (post-dedup 안정).
# R20 threshold: 110,000 (post-dedup corpus 의 현실적 하한).
_VOC_TOTAL_THRESHOLD = 110_000
_VOC_TOTAL_BASELINE_R12 = 167_701
_VOC_TOTAL_BASELINE_R20 = 117_958
# Harvest3 Polish (2026-06-06): Hardware.fr backfill 48 → 206 (4.3× 확장, +158 inserted).
# 신규 플랫폼 백필 성과를 회귀로부터 보호. threshold = 150 (post-harvest3p 안전 하한,
# 50건 자연 dedup/policy 정리 마진).  baseline_harvest3p = 206 (2026-06-06 실측).
_HARDWARE_FR_VOC_THRESHOLD = 150
_HARDWARE_FR_VOC_BASELINE_HARVEST3P = 206
# Data Grow R5 Track L5 (2026-06-09): MX 매칭/리치/archive 누계 효과 회귀 보호.
# 2026-06-09 실측: mx_match_pct=75.8 / mx_rich_pct=51.1 / archived_pct=59.8.
# 임계는 *절대 하한* — Data Clean 진행 누적분이 회귀로 무너지지 않도록 보호.
#   mx_match_pct  >= 50.0  (현 75.8)
#   mx_rich_pct   >= 40.0  (현 51.1)
#   archived_pct  >= 50.0  (현 59.8 — Data Clean R3~R4 누계 정리분 회귀 방지)
_MX_MATCH_PCT_THRESHOLD = 50.0
_MX_MATCH_PCT_BASELINE_DATA_GROW_R5 = 75.8
_MX_RICH_PCT_THRESHOLD = 40.0
_MX_RICH_PCT_BASELINE_DATA_GROW_R5 = 51.1
_ARCHIVE_PCT_THRESHOLD = 50.0
_ARCHIVE_PCT_BASELINE_DATA_GROW_R5 = 59.8
_ALEMBIC_MIN_HEAD = "0014"


@router.get("/regression-baseline")
async def regression_baseline(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """R6/R7/R8 핵심 매칭 회귀 자동 감시 (Track C).

    응답::

        {
          "generated_at": "2026-06-04T...",
          "checks": [
            {"name":"note7_voc","current":352,"baseline_r8":352,
             "threshold":300,"delta_vs_baseline":0,"ok":true},
            ...
          ],
          "summary": {"total":8, "ok":8, "failed":0},
          "alembic_head":"0013",
          "alembic_min_head":"0013"
        }

    검사 항목 (14 + alembic):
      1. Note 7 voc       >= 300   (R8 352,   R12 366)
      2. Fold 1 voc       >= 280   (R8 302,   R12 308)
      3. S22 voc          >= 350   (R8 414,   R12 427)
      4. S25 voc          >= 1800  (R8 2082,  R12 2122)
      5. Buds 3 voc       >= 450   (R8 511,   R12 531)
      6. HN linked %      >= 15.0% (R8 22.67%, R12 22.48%)
      7. topics_filled    >= 20,000 (R8 42,935; R12 25,410 — 모집단 변화로 R8 비교 무의미)
      8. products count   >= 380   (R8 389, R12 389)
      9. hn_total         >= 30,000 (R12 33,911) — R12 신설
     10. voc_total        >= 110,000 (R12 167,701; R20 117,958) — R12 신설
     11. hardware_fr_voc  >= 150   (Harvest3p 206, 4.3× 확장) — Harvest3 Polish 신설
     12. mx_match_pct     >= 50.0% (Data Grow R5 75.8%) — Data Clean 효과 회귀
     13. mx_rich_pct      >= 40.0% (Data Grow R5 51.1%) — 리치 신호 비율 회귀
     14. archive_pct      >= 50.0% (Data Grow R5 59.8%) — 누적 archive 정리분 회귀
      +  alembic head     >= 0014

    quality_report 가 일 1회 호출, ``summary.failed > 0`` 또는 alembic 비교
    실패 시 alert 발생 (운영 정책).  localhost only.
    """
    _enforce_localhost(request)
    row = (await db.execute(_REGRESSION_SQL, {"mx_pat": _MX_REGEX})).first()
    if row is None:
        raise HTTPException(status_code=500, detail="regression query returned no row")

    by_code: Dict[str, int] = {}
    for item in (row.key_products_json or []):
        by_code[item["code"]] = int(item["voc"] or 0)

    checks: List[Dict[str, Any]] = []

    # 1-5: 핵심 product 5종
    name_map = {
        "GN7": "note7_voc", "GZF1": "fold1_voc", "GS22": "s22_voc",
        "GS25": "s25_voc", "GB3": "buds3_voc",
    }
    for code, meta in _REGRESSION_PRODUCT_BASELINES.items():
        current = int(by_code.get(code, 0))
        threshold = int(meta["threshold"])
        baseline = int(meta["baseline_r8"])
        baseline_r12 = int(meta["baseline_r12"])
        baseline_r20 = int(meta["baseline_r20"])
        checks.append({
            "name": name_map[code],
            "label": meta["label"],
            "product_code": code,
            "current": current,
            "baseline_r8": baseline,
            "baseline_r12": baseline_r12,
            "baseline_r20": baseline_r20,
            "threshold": threshold,
            "delta_vs_baseline": current - baseline,
            "delta_vs_baseline_r12": current - baseline_r12,
            "delta_vs_baseline_r20": current - baseline_r20,
            "ok": current >= threshold,
        })

    # 6: HN linked %
    hn_total = int(row.hn_total or 0)
    hn_linked = int(row.hn_linked or 0)
    hn_pct = round(100.0 * hn_linked / hn_total, 2) if hn_total > 0 else 0.0
    checks.append({
        "name": "hn_linked_pct",
        "label": "HN 매칭률 (%)",
        "current": hn_pct,
        "baseline_r8": _HN_LINKED_PCT_BASELINE_R8,
        "baseline_r12": _HN_LINKED_PCT_BASELINE_R12,
        "baseline_r20": _HN_LINKED_PCT_BASELINE_R20,
        "threshold": _HN_LINKED_PCT_THRESHOLD,
        "delta_vs_baseline": round(hn_pct - _HN_LINKED_PCT_BASELINE_R8, 2),
        "delta_vs_baseline_r12": round(hn_pct - _HN_LINKED_PCT_BASELINE_R12, 2),
        "delta_vs_baseline_r20": round(hn_pct - _HN_LINKED_PCT_BASELINE_R20, 2),
        "ok": hn_pct >= _HN_LINKED_PCT_THRESHOLD,
        "hn_total": hn_total,
        "hn_linked": hn_linked,
    })

    # 7: topics 분류 채워진 행 수
    topics_filled = int(row.topics_filled or 0)
    checks.append({
        "name": "topics_filled",
        "label": "topic 분류 채워진 voc",
        "current": topics_filled,
        "baseline_r8": _TOPICS_FILLED_BASELINE_R8,
        "baseline_r12": _TOPICS_FILLED_BASELINE_R12,
        "baseline_r20": _TOPICS_FILLED_BASELINE_R20,
        "threshold": _TOPICS_FILLED_THRESHOLD,
        "delta_vs_baseline": topics_filled - _TOPICS_FILLED_BASELINE_R8,
        "delta_vs_baseline_r12": topics_filled - _TOPICS_FILLED_BASELINE_R12,
        "delta_vs_baseline_r20": topics_filled - _TOPICS_FILLED_BASELINE_R20,
        "ok": topics_filled >= _TOPICS_FILLED_THRESHOLD,
    })

    # 8: products count
    products_total = int(row.products_total or 0)
    checks.append({
        "name": "products_count",
        "label": "products 총 개수",
        "current": products_total,
        "baseline_r8": _PRODUCTS_BASELINE_R8,
        "baseline_r12": _PRODUCTS_BASELINE_R12,
        "baseline_r20": _PRODUCTS_BASELINE_R20,
        "threshold": _PRODUCTS_THRESHOLD,
        "delta_vs_baseline": products_total - _PRODUCTS_BASELINE_R8,
        "delta_vs_baseline_r12": products_total - _PRODUCTS_BASELINE_R12,
        "delta_vs_baseline_r20": products_total - _PRODUCTS_BASELINE_R20,
        "ok": products_total >= _PRODUCTS_THRESHOLD,
    })

    # 9 (R12 신설): HN total — backfill 성과 회귀 방지
    checks.append({
        "name": "hn_total",
        "label": "HackerNews 전체 voc",
        "current": hn_total,
        "baseline_r12": _HN_TOTAL_BASELINE_R12,
        "baseline_r20": _HN_TOTAL_BASELINE_R20,
        "threshold": _HN_TOTAL_THRESHOLD,
        "delta_vs_baseline_r12": hn_total - _HN_TOTAL_BASELINE_R12,
        "delta_vs_baseline_r20": hn_total - _HN_TOTAL_BASELINE_R20,
        "ok": hn_total >= _HN_TOTAL_THRESHOLD,
    })

    # 10 (R12 신설): voc_total — 전체 모집단 회귀 방지
    voc_total = int(row.voc_total or 0)
    checks.append({
        "name": "voc_total",
        "label": "voc_records 전체",
        "current": voc_total,
        "baseline_r12": _VOC_TOTAL_BASELINE_R12,
        "baseline_r20": _VOC_TOTAL_BASELINE_R20,
        "threshold": _VOC_TOTAL_THRESHOLD,
        "delta_vs_baseline_r12": voc_total - _VOC_TOTAL_BASELINE_R12,
        "delta_vs_baseline_r20": voc_total - _VOC_TOTAL_BASELINE_R20,
        "ok": voc_total >= _VOC_TOTAL_THRESHOLD,
    })

    # 11 (Harvest3p 신설): Hardware.fr voc — 신규 플랫폼 백필 4.3× 확장 회귀 방지.
    # Harvest3 Polish (2026-06-06) 에서 backfill 48 → 206 (delta=158) 성과 동결.
    hardware_fr_voc = int(row.hardware_fr_voc or 0)
    checks.append({
        "name": "hardware_fr_voc",
        "label": "Hardware.fr 전체 voc",
        "current": hardware_fr_voc,
        "baseline_harvest3p": _HARDWARE_FR_VOC_BASELINE_HARVEST3P,
        "threshold": _HARDWARE_FR_VOC_THRESHOLD,
        "delta_vs_baseline_harvest3p": hardware_fr_voc - _HARDWARE_FR_VOC_BASELINE_HARVEST3P,
        "ok": hardware_fr_voc >= _HARDWARE_FR_VOC_THRESHOLD,
    })

    # 12-14 (Data Grow R5 L5 신설): MX 매칭 / 리치 / archive 누계 효과 회귀 보호.
    # active = archived_at IS NULL 인 voc_records.  pct 분모는 active (mx) 또는 total (archive).
    quality_active = int(row.quality_active or 0)
    quality_voc_records_total = int(row.quality_voc_records_total or 0)
    quality_mx_match = int(row.quality_mx_match or 0)
    quality_mx_rich = int(row.quality_mx_rich or 0)
    quality_archived = int(row.quality_archived or 0)
    mx_match_pct = round(100.0 * quality_mx_match / quality_active, 1) if quality_active else 0.0
    mx_rich_pct = round(100.0 * quality_mx_rich / quality_active, 1) if quality_active else 0.0
    archive_pct = round(100.0 * quality_archived / quality_voc_records_total, 1) if quality_voc_records_total else 0.0

    # 12: MX 매칭률 — 진짜 모바일 관련 인사이트 비율 (Data Clean 효과)
    checks.append({
        "name": "mx_match_pct",
        "label": "MX 매칭률 (%)",
        "current": mx_match_pct,
        "baseline_data_grow_r5": _MX_MATCH_PCT_BASELINE_DATA_GROW_R5,
        "threshold": _MX_MATCH_PCT_THRESHOLD,
        "delta_vs_baseline_data_grow_r5": round(mx_match_pct - _MX_MATCH_PCT_BASELINE_DATA_GROW_R5, 2),
        "ok": mx_match_pct >= _MX_MATCH_PCT_THRESHOLD,
        "active": quality_active,
        "mx_match": quality_mx_match,
    })

    # 13: MX 리치률 — 100자+ 본문 + MX 매칭 (정성적 신호 비율)
    checks.append({
        "name": "mx_rich_pct",
        "label": "MX 리치률 (%)",
        "current": mx_rich_pct,
        "baseline_data_grow_r5": _MX_RICH_PCT_BASELINE_DATA_GROW_R5,
        "threshold": _MX_RICH_PCT_THRESHOLD,
        "delta_vs_baseline_data_grow_r5": round(mx_rich_pct - _MX_RICH_PCT_BASELINE_DATA_GROW_R5, 2),
        "ok": mx_rich_pct >= _MX_RICH_PCT_THRESHOLD,
        "active": quality_active,
        "mx_rich": quality_mx_rich,
    })

    # 14: archive 비율 — Data Clean R3~R4 누적 정리분 회귀 방지 (한국 5사 + 노이즈)
    checks.append({
        "name": "archive_pct",
        "label": "archive 비율 (%)",
        "current": archive_pct,
        "baseline_data_grow_r5": _ARCHIVE_PCT_BASELINE_DATA_GROW_R5,
        "threshold": _ARCHIVE_PCT_THRESHOLD,
        "delta_vs_baseline_data_grow_r5": round(archive_pct - _ARCHIVE_PCT_BASELINE_DATA_GROW_R5, 2),
        "ok": archive_pct >= _ARCHIVE_PCT_THRESHOLD,
        "archived": quality_archived,
        "voc_records_total": quality_voc_records_total,
    })

    # alembic head (>= "0014" 문자열 비교 — zero-padded 4 digit revision)
    alembic_head = str(row.alembic_head or "")
    alembic_ok = alembic_head >= _ALEMBIC_MIN_HEAD

    ok_count = sum(1 for c in checks if c["ok"])
    failed_count = len(checks) - ok_count
    if not alembic_ok:
        failed_count += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": checks,
        "alembic_head": alembic_head,
        "alembic_min_head": _ALEMBIC_MIN_HEAD,
        "alembic_ok": alembic_ok,
        "summary": {
            "total": len(checks) + 1,  # 14 checks + alembic
            "ok": ok_count + (1 if alembic_ok else 0),
            "failed": failed_count,
        },
    }


# ── 운영 1주 모니터링 (R10 Track D) ───────────────────────────────────────
# crawler/insight/weekly_monitor.py 가 매일 00:30 UTC 에 reports/weekly_monitor_
# YYYY-WW.json 을 생성. 이 endpoint 는 최근 ``weeks`` 주차 파일을 모아 반환한다.
#
# 응답 구조:
#   {
#     "weeks": 4,
#     "generated_at": "2026-06-04T...",
#     "available": ["2026-23", "2026-22", ...],
#     "snapshots": [ <weekly_monitor 파일 그대로> ],
#     "latest_alerts": [...],
#     "trend": {
#       "voc_total_per_week": [{"iso_week":"2026-23","total":...}],
#       "active_sites_per_week": [{"iso_week":"2026-23","active":62}],
#       "grounding_avg_per_week": [{"iso_week":"2026-23","avg":0.38}],
#       "regression_failed_per_week": [{"iso_week":"2026-23","failed":0}]
#     },
#     "baseline_delta": {
#       "regression_now_failed": 0,
#       "active_sites_now": 62
#     }
#   }
#
# 파일이 부족하면 사용 가능한 만큼만 반환하고 ``available`` 로 알려준다.
_WEEKLY_MONITOR_DIR = Path(__file__).resolve().parents[3] / "reports"


def _load_weekly_monitor_snapshots(weeks: int) -> List[Dict[str, Any]]:
    """reports/ 에서 weekly_monitor_*.json 을 최근 ``weeks`` 개만 신선 순 로드."""
    if not _WEEKLY_MONITOR_DIR.is_dir():
        return []
    files = sorted(
        _WEEKLY_MONITOR_DIR.glob("weekly_monitor_*.json"),
        key=lambda p: p.name,
        reverse=True,
    )[:weeks]
    snapshots: List[Dict[str, Any]] = []
    for fp in files:
        try:
            snapshots.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception as e:
            snapshots.append({"_error": str(e), "_file": fp.name})
    return snapshots


# ── 운영 실시간 상태 (R14 Track E) ────────────────────────────────────────
# crawler.insight.operations_monitor 가 매시 30분 호출되어 6 metric 점검 +
# alert_events INSERT 를 수행한다. 이 endpoint 는 동일한 평가를 *즉시* 1회 수행
# 하여 현재 운영 상태 (ok / warning / critical) 와 metric 표를 반환.
# INSERT 는 하지 않음 (Celery task 만 INSERT — 중복 발화 방지).
@router.get("/ops-status")
async def ops_status(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: ARG001 — 의존성 일관성 유지용
) -> dict:
    """R14 운영 SLO 6개 metric 실시간 점검 (read-only).

    응답::

        {
          "status": "ok" | "warning" | "critical",
          "generated_at": ISO8601,
          "thresholds": {...},
          "data_quality": {...},
          "regression":  {...},
          "voc":          {"days": [...]},
          "grounding_last": float | None,
          "violations":  [{"metric","severity","value","threshold","reason"}, ...]
        }

    localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.operations_monitor import collect_status  # type: ignore
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"operations_monitor import 실패: {exc}"
        )
    try:
        payload = await collect_status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"collect_status 실패: {exc}")
    return payload


# ── 운영 상태 일별 추세 (R18 Track D) ─────────────────────────────────────
# Celery beat `run_ops_history` 가 매일 09:30 KST 에 reports/ops_status_YYYY-MM-DD.json
# 슬림 요약을 누적 적재한다. 이 endpoint 는 최근 N일치를 묶어 일별 시계열 + 변화율
# + 7일 이동 평균을 반환. localhost only.
_OPS_HISTORY_DIR = Path(__file__).resolve().parents[3] / "reports"


def _load_ops_history(days: int) -> List[Dict[str, Any]]:
    """``reports/ops_status_*.json`` 최근 ``days`` 개 신선 순 로드."""
    if not _OPS_HISTORY_DIR.is_dir():
        return []
    files = sorted(
        _OPS_HISTORY_DIR.glob("ops_status_*.json"),
        key=lambda p: p.name,
        reverse=True,
    )[:days]
    out: List[Dict[str, Any]] = []
    for fp in files:
        try:
            out.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception as e:
            out.append({"_error": str(e), "_file": fp.name})
    return out


def _moving_avg(values: List[Optional[float]], window: int = 7) -> List[Optional[float]]:
    """단순 이동 평균 (앞 window-1 개는 None). None / NaN 항목은 평균 산출에서 제외.

    값이 1개도 없으면 None. 시계열 길이는 입력과 동일.
    """
    out: List[Optional[float]] = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        chunk = [v for v in values[start:i + 1] if isinstance(v, (int, float))]
        if i + 1 < window or not chunk:
            out.append(None)
        else:
            out.append(round(sum(chunk) / len(chunk), 4))
    return out


def _delta_pct(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    """전일 대비 변화율 (%). 둘 다 숫자이고 prev != 0 일 때만 산출."""
    if not isinstance(curr, (int, float)) or not isinstance(prev, (int, float)):
        return None
    if prev == 0:
        return None
    return round((curr - prev) / abs(prev) * 100.0, 2)


@router.get("/ops-trend")
def ops_trend(
    request: Request,
    days: int = Query(7, ge=1, le=90,
                      description="최근 N일 (기본 7, 최대 90)."),
) -> dict:
    """R18 Track D — 운영 상태 일별 추세.

    응답::

        {
          "days": 7,
          "generated_at": ISO8601,
          "available": ["2026-06-05", "2026-06-04", ...],   # 발견된 날짜
          "series": [                                       # 오래된 → 최근
            {
              "date": "2026-05-30",
              "status": "ok",
              "voc_last": 5234,
              "voc_delta_pct": -3.2,                        # 전일 대비
              "sentiment_null_rate": 0.02,
              "topic_rate": 0.89,
              "grounding_last": 0.42,
              "regression_failed": 0,
              "violations_count": 1
            }, ...
          ],
          "moving_avg_7d": {
            "voc_last":           [null, ..., 5800.0],     # series 와 같은 길이
            "grounding_last":     [null, ..., 0.41],
            "violations_count":   [null, ..., 0.71]
          },
          "summary": {
            "latest": {...},                                # 최신 1개 (series[-1])
            "voc_change_pct_7d": 5.2,                       # 시작 vs 끝
            "violations_total":  3
          }
        }

    localhost only. 적재된 파일이 0개면 빈 series + 빈 summary 반환.
    """
    _enforce_localhost(request)
    snapshots = _load_ops_history(int(days))

    # 사용 가능한 날짜 — 최신 우선 정렬 그대로
    available = [s.get("target_date") for s in snapshots if s.get("target_date")]

    # 시계열: 오래된 → 최신
    chrono = [s for s in reversed(snapshots) if not s.get("_error")]

    series: List[Dict[str, Any]] = []
    prev_voc: Optional[float] = None
    for snap in chrono:
        voc_last = snap.get("voc_last")
        entry = {
            "date": snap.get("target_date"),
            "status": snap.get("status"),
            "voc_last": voc_last,
            "voc_delta_pct": _delta_pct(
                voc_last if isinstance(voc_last, (int, float)) else None,
                prev_voc,
            ),
            "sentiment_null_rate": snap.get("sentiment_null_rate"),
            "topic_rate": snap.get("topic_rate"),
            "grounding_last": snap.get("grounding_last"),
            "regression_failed": snap.get("regression_failed"),
            "violations_count": snap.get("violations_count"),
        }
        series.append(entry)
        if isinstance(voc_last, (int, float)):
            prev_voc = voc_last

    # 7일 이동 평균 — 3개 metric
    moving_avg_7d = {
        "voc_last": _moving_avg(
            [e["voc_last"] if isinstance(e["voc_last"], (int, float)) else None
             for e in series], window=7),
        "grounding_last": _moving_avg(
            [e["grounding_last"] if isinstance(e["grounding_last"], (int, float)) else None
             for e in series], window=7),
        "violations_count": _moving_avg(
            [e["violations_count"] if isinstance(e["violations_count"], (int, float)) else None
             for e in series], window=7),
    }

    # 요약
    latest = series[-1] if series else {}
    voc_first = next(
        (e["voc_last"] for e in series if isinstance(e["voc_last"], (int, float))),
        None,
    )
    voc_last_val = next(
        (e["voc_last"] for e in reversed(series) if isinstance(e["voc_last"], (int, float))),
        None,
    )
    voc_change_pct_7d = _delta_pct(voc_last_val, voc_first)
    violations_total = sum(
        int(e["violations_count"] or 0) for e in series
        if isinstance(e.get("violations_count"), (int, float))
    )

    return {
        "days": int(days),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "available": available,
        "series": series,
        "moving_avg_7d": moving_avg_7d,
        "summary": {
            "latest": latest,
            "voc_change_pct_7d": voc_change_pct_7d,
            "violations_total": violations_total,
        },
    }


# ── 수집 자동 모니터링 (R29 Track D) ──────────────────────────────────────
# Celery beat `run_collection_health` 가 매시 50분에 활성 사이트의 24h voc 카운트를
# 직전 7일 일평균과 비교 → 위반(critical/warning) 시 alert_events INSERT 하고
# reports/collection_health_YYYY-MM-DD.json 으로 1일 1개 스냅샷 누적한다.
# 이 endpoint 는 최근 N일 스냅샷을 묶어 사이트별 24h vs baseline 트렌드를 반환.
_COLLECTION_HEALTH_DIR = Path(__file__).resolve().parents[3] / "reports"


def _load_collection_health(days: int) -> List[Dict[str, Any]]:
    """``reports/collection_health_*.json`` 최근 ``days`` 개 신선 순 로드."""
    if not _COLLECTION_HEALTH_DIR.is_dir():
        return []
    files = sorted(
        _COLLECTION_HEALTH_DIR.glob("collection_health_*.json"),
        key=lambda p: p.name,
        reverse=True,
    )[:days]
    out: List[Dict[str, Any]] = []
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            # 파일명에서 날짜 키 추출 (스냅샷 본문에는 generated_at만 있으므로)
            stem = fp.stem  # collection_health_YYYY-MM-DD
            date_part = stem.replace("collection_health_", "")
            data["_snapshot_date"] = date_part
            out.append(data)
        except Exception as e:
            out.append({"_error": str(e), "_file": fp.name})
    return out


@router.get("/collection-monitor-history")
def collection_monitor_history(
    request: Request,
    days: int = Query(7, ge=1, le=30,
                      description="최근 N일 (기본 7, 최대 30)."),
) -> dict:
    """R29 Track D — 수집 자동 모니터링 일별 추세.

    응답::

        {
          "days": 7,
          "generated_at": ISO8601,
          "available": ["2026-06-06", "2026-06-05", ...],
          "series": [                                  # 오래된 → 최근
            {
              "date": "2026-06-06",
              "status": "ok" | "warning" | "critical",
              "active_sites": 63,
              "critical_count": 18,
              "warning_count": 4,
              "violations_count": 22
            }, ...
          ],
          "latest": {
            "snapshot_date": "2026-06-06",
            "status": "...",
            "violations": [{"code","metric","severity","value","threshold","reason"}, ...]
          },
          "summary": {
            "violations_total_7d": 154,
            "critical_total_7d":   126,
            "always_critical_codes": [...],            # 모든 N일 critical 발생 사이트
            "worst_site": {"code": "...", "occurrences": 7}
          }
        }

    localhost only. 적재된 파일이 0개면 빈 series 반환.
    """
    _enforce_localhost(request)
    snapshots = _load_collection_health(int(days))

    available = [s.get("_snapshot_date") for s in snapshots
                 if s.get("_snapshot_date") and not s.get("_error")]

    # 시계열: 오래된 → 최신
    chrono = [s for s in reversed(snapshots) if not s.get("_error")]

    series: List[Dict[str, Any]] = []
    critical_occurrences: Dict[str, int] = {}
    warning_occurrences: Dict[str, int] = {}

    for snap in chrono:
        counts = snap.get("violation_counts") or {}
        critical_n = int(counts.get("critical") or 0)
        warning_n = int(counts.get("warning") or 0)
        series.append({
            "date": snap.get("_snapshot_date"),
            "status": snap.get("status"),
            "active_sites": int(snap.get("active_sites") or 0),
            "critical_count": critical_n,
            "warning_count": warning_n,
            "violations_count": critical_n + warning_n,
        })
        for v in snap.get("violations") or []:
            code = v.get("code") or "?"
            sev = v.get("severity")
            if sev == "critical":
                critical_occurrences[code] = critical_occurrences.get(code, 0) + 1
            elif sev == "warning":
                warning_occurrences[code] = warning_occurrences.get(code, 0) + 1

    # 최신 1개 (전체 violations 포함)
    latest_snap = chrono[-1] if chrono else {}
    latest = {
        "snapshot_date": latest_snap.get("_snapshot_date"),
        "status": latest_snap.get("status"),
        "active_sites": int(latest_snap.get("active_sites") or 0),
        "violations": latest_snap.get("violations") or [],
    } if latest_snap else {}

    # 요약 — N일 동안 critical 한 번이라도 발생한 사이트 / 가장 자주 발생한 사이트
    total_days = len([s for s in series if s.get("date")])
    always_critical = sorted(
        [c for c, n in critical_occurrences.items() if total_days > 0 and n >= total_days],
    )
    worst_site = None
    if critical_occurrences:
        worst_code = max(critical_occurrences, key=lambda k: critical_occurrences[k])
        worst_site = {"code": worst_code, "occurrences": critical_occurrences[worst_code]}

    return {
        "days": int(days),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "available": available,
        "series": series,
        "latest": latest,
        "summary": {
            "violations_total_7d": sum(e["violations_count"] for e in series),
            "critical_total_7d":   sum(e["critical_count"]    for e in series),
            "warning_total_7d":    sum(e["warning_count"]     for e in series),
            "always_critical_codes": always_critical,
            "worst_site": worst_site,
            "snapshots_loaded": len(chrono),
        },
    }


@router.get("/collection-trend")
async def collection_trend(
    request: Request,
    days: int = Query(7, ge=2, le=30,
                      description="누적 일수 (기본 7, 최대 30)."),
) -> dict:
    """Track F — 사이트별 N일 누적 수집 트렌드 + 변동 큰 사이트 자동 식별.

    ``collection-monitor-history`` 가 *위반(critical/warning) 카운트* 의 일별 series
    만 반환한다면, 이 endpoint 는 *각 사이트의 일별 voc 매트릭스* + 통계 + 변동
    분류를 한 번에 제공한다.

    응답::

        {
          "generated_at": ISO8601,
          "days": 7,
          "dates": ["2026-05-31", ..., "2026-06-06"],
          "active_sites": 65,
          "daily_totals": [{"date": ..., "total": ...}, ...],
          "site_stats": [
            {"code","total","mean_per_day","stddev","cv","max","min",
             "half_ratio_delta","series": [n_day0, ...]}, ...
          ],          # total desc 정렬
          "volatile_sites": [
            {"code","kind":"trend_down|trend_up|volatile_swing",
             "mean_per_day","cv","half_ratio_delta","reasons":[...]}, ...
          ],
          "thresholds": {"cv_volatile", "half_ratio_delta",
                         "min_mean_for_volatility"},
          "summary": {
            "total_voc", "mean_per_day", "volatile_count",
            "trend_down_count", "trend_up_count", "volatile_swing_count"
          }
        }

    localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    from insight.collection_trend import collect_payload  # type: ignore
    return await collect_payload(days=int(days))


def _load_collection_trend(days: int) -> List[Dict[str, Any]]:
    """``reports/collection_trend_*.json`` 최근 ``days`` 개 신선 순 로드."""
    if not _COLLECTION_HEALTH_DIR.is_dir():
        return []
    files = sorted(
        _COLLECTION_HEALTH_DIR.glob("collection_trend_*.json"),
        key=lambda p: p.name,
        reverse=True,
    )[:days]
    out: List[Dict[str, Any]] = []
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            stem = fp.stem  # collection_trend_YYYY-MM-DD
            date_part = stem.replace("collection_trend_", "")
            data["_snapshot_date"] = date_part
            out.append(data)
        except Exception as e:
            out.append({"_error": str(e), "_file": fp.name})
    return out


@router.get("/collection-trend-history")
def collection_trend_history(
    request: Request,
    days: int = Query(14, ge=1, le=30,
                      description="최근 N개 일별 스냅샷 (기본 14, 최대 30)."),
) -> dict:
    """Harvest 3 Track C — 수집 7일 트렌드 일별 누적 history.

    ``run_collection_trend`` task 가 매일 09:30 KST 에 적재한
    ``reports/collection_trend_YYYY-MM-DD.json`` 파일들을 최근 N개 모아
    series 형태로 반환한다.  분류 카운트(healthy/moderate/low/dying/dead),
    일별 총 voc, 변동 사이트 수 추이를 시계열로 시각화하기 위함.

    응답::

        {
          "days": 14,
          "generated_at": ISO8601,
          "available": ["2026-06-06", "2026-06-05", ...],   # 신선 순
          "series": [                                       # 오래된 → 최근
            {
              "date": "2026-05-24",
              "active_sites": 65,
              "total_voc": 12345,
              "mean_per_day": 1763.6,
              "class_counts": {"healthy": 10, "moderate": 15, ...},
              "volatile_count": 3,
              "trend_down_count": 1,
              "trend_up_count": 1,
              "volatile_swing_count": 1
            }, ...
          ],
          "latest": {<가장 최근 스냅샷의 summary 전부>}
        }

    localhost only.
    """
    _enforce_localhost(request)
    snapshots = _load_collection_trend(int(days))
    available = [s.get("_snapshot_date") for s in snapshots if s.get("_snapshot_date")]

    # 오래된 → 최근 (series)
    series: List[Dict[str, Any]] = []
    for snap in reversed(snapshots):
        if "_error" in snap:
            continue
        summary = snap.get("summary") or {}
        series.append({
            "date": snap.get("_snapshot_date"),
            "active_sites": int(snap.get("active_sites") or 0),
            "total_voc": int(summary.get("total_voc") or 0),
            "mean_per_day": float(summary.get("mean_per_day") or 0.0),
            "class_counts": summary.get("class_counts") or {},
            "volatile_count": int(summary.get("volatile_count") or 0),
            "trend_down_count": int(summary.get("trend_down_count") or 0),
            "trend_up_count": int(summary.get("trend_up_count") or 0),
            "volatile_swing_count": int(summary.get("volatile_swing_count") or 0),
        })

    latest = snapshots[0].get("summary") if snapshots and "_error" not in snapshots[0] else None

    return {
        "days": int(days),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "available": available,
        "series": series,
        "latest": latest,
    }


@router.get("/weekly-monitor")
def weekly_monitor(
    request: Request,
    weeks: int = Query(4, ge=1, le=12, description="최근 N주차 (기본 4, 최대 12)."),
) -> dict:
    """R10 Track D — 운영 1주 모니터링 누적 보고.

    crawler/insight/weekly_monitor.py 가 reports/weekly_monitor_YYYY-WW.json 으로
    매일 적재. 이 endpoint 는 최근 ``weeks`` 주차를 묶어 trend·latest_alerts 포함.
    localhost only.
    """
    _enforce_localhost(request)
    snapshots = _load_weekly_monitor_snapshots(int(weeks))
    available = [s.get("iso_year_week") for s in snapshots if s.get("iso_year_week")]

    # trend 4종 (오래된 → 최근)
    chrono = list(reversed(snapshots))
    voc_total_per_week: List[Dict[str, Any]] = []
    active_per_week: List[Dict[str, Any]] = []
    grounding_per_week: List[Dict[str, Any]] = []
    regression_per_week: List[Dict[str, Any]] = []
    for snap in chrono:
        wk = snap.get("iso_year_week")
        if not wk:
            continue
        voc_total = sum(int(v.get("voc_count") or 0) for v in (snap.get("voc_daily") or []))
        voc_total_per_week.append({"iso_week": wk, "total": voc_total})
        active = int((snap.get("collection_status") or {}).get("total_active", 0))
        active_per_week.append({"iso_week": wk, "active": active})
        ga = (snap.get("grounding") or {}).get("avg")
        grounding_per_week.append({"iso_week": wk, "avg": ga})
        failed = int(((snap.get("regression") or {}).get("summary") or {}).get("failed", 0))
        regression_per_week.append({"iso_week": wk, "failed": failed})

    latest = snapshots[0] if snapshots else {}
    return {
        "weeks": int(weeks),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "available": available,
        "snapshots": snapshots,
        "latest_alerts": (latest.get("alerts") or []),
        "trend": {
            "voc_total_per_week": voc_total_per_week,
            "active_sites_per_week": active_per_week,
            "grounding_avg_per_week": grounding_per_week,
            "regression_failed_per_week": regression_per_week,
        },
        "baseline_delta": {
            "regression_now_failed": (
                int(((latest.get("regression") or {}).get("summary") or {}).get("failed", 0))
                if latest else None
            ),
            "active_sites_now": (
                int((latest.get("collection_status") or {}).get("total_active", 0))
                if latest else None
            ),
        },
    }


# ── 데이터 export (Track E1/E2) ──────────────────────────────────────────
# CSV / Excel / PDF — 임원 보고용.  series 코드(GS25) 또는 시리즈 prefix(GS)
# 모두 허용.  period_days 기본 30, 최대 365.
#
# CSV/Excel 은 동일 GET endpoint (type 파라미터 분기). PDF 는 sections 선택을
# 위해 POST.

_MIME_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "pdf": "application/pdf",
}


@router.get("/export")
async def export_data(
    request: Request,
    type: str = Query("csv", pattern="^(csv|excel|xlsx)$",
                      description="csv | excel(=xlsx) | xlsx"),
    series: str = Query(..., min_length=1, max_length=10,
                        description="product code (GS25) 또는 series prefix (GS)."),
    period_days: int = Query(30, ge=1, le=365,
                             description="조회 기간 (일).  최대 365."),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """voc_records CSV/Excel 다운로드.

    - ``type=csv`` : UTF-8 BOM 포함 CSV (Excel 호환).  최대 5,000 row.
    - ``type=excel`` 또는 ``xlsx`` : 5 sheet (Summary/Timeline/Categories/Keywords/VOC).
    - ``series`` : 단일 product (예: ``GS25``) 또는 series prefix (예: ``GS``).
    - filename header 자동 부여: ``voc_<series>_<YYYY-MM-DD>.<ext>``.
    """
    _enforce_localhost(request)
    try:
        ctx = await export_service.build_context(db, series.strip(), period_days)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if type == "csv":
        data, fname = await export_service.export_csv(db, ctx)
        media = _MIME_TYPES["csv"]
    else:
        data, fname = await export_service.export_excel(db, ctx)
        media = _MIME_TYPES["xlsx"]
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


class _ExportPDFBody(BaseModel):
    product: str = Field(..., min_length=1, max_length=10,
                         description="product code 또는 series prefix")
    period_days: int = Field(30, ge=1, le=365)
    sections: List[str] = Field(
        default_factory=lambda: ["kpi", "timeline", "categories", "keywords"],
        description="kpi/timeline/categories/keywords 중 선택",
    )


@router.post("/export-pdf")
async def export_pdf_endpoint(
    request: Request,
    body: _ExportPDFBody,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """PDF 보고서 생성.

    body::

        {
          "product": "GS25",
          "period_days": 30,
          "sections": ["kpi","timeline","categories","keywords"]
        }

    응답: PDF 바이너리 (``attachment``).
    sections 미지정 시 4종 모두 포함.  fpdf2 core font 사용 (영문/숫자 위주).
    """
    _enforce_localhost(request)
    valid_sections = {"kpi", "timeline", "categories", "keywords"}
    sections = [s for s in body.sections if s in valid_sections]
    if not sections:
        sections = ["kpi", "timeline", "categories", "keywords"]
    try:
        ctx = await export_service.build_context(db, body.product.strip(),
                                                 body.period_days)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    data, fname = await export_service.export_pdf(db, ctx, sections)
    return Response(
        content=data,
        media_type=_MIME_TYPES["pdf"],
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── 외부 공유 토큰 (Track E3) ────────────────────────────────────────────
class _ShareTokenBody(BaseModel):
    resource: str = Field(..., min_length=1, max_length=200,
                          description="공유할 frontend path (예: '/insights')")
    expires_in: int = Field(7 * 86400, ge=60, le=30 * 86400,
                            description="유효 기간 (초).  최소 60s, 최대 30일.")


@router.post("/share-token")
def share_token_create(request: Request, body: _ShareTokenBody) -> dict:
    """외부 공유 토큰 발급.  in-memory TTL (재시작 시 초기화).

    응답::

        {
          "token": "abc123...",
          "url": "/shared/abc123...",
          "resource": "/insights",
          "expires_at": "2026-06-11T..."
        }

    검증은 ``GET /api/v1/shared/{token}`` — 외부 공유 link 로 직접 노출 가능.
    """
    _enforce_localhost(request)
    try:
        return export_service.create_share_token(body.resource, body.expires_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/share-token/stats")
def share_token_stats(request: Request) -> dict:
    """활성 토큰 개수 — 운영 모니터링용."""
    _enforce_localhost(request)
    return export_service.share_tokens_stats()


# ── 백필 실행 이력 (R19 트랙 E — backfill safety) ────────────────────────
# R18 사고 (topic 백필 재실행이 기존 분류 폭락 유발) 예방:
# 모든 백필 스크립트가 crawler/insight/backfill_audit.py 의 record_run() 으로
# JSONL 1줄을 남기고, 본 endpoint 가 최근 이력 + 스크립트별 집계를 노출한다.
#
# Frontend Ops 페이지의 "백필 안전" 카드, Celery beat 의 alert (실패 누적 감지)
# 가 공통 사용.

@router.get("/backfill-status")
def backfill_status(
    request: Request,
    limit: int = Query(20, ge=1, le=200, description="반환할 최근 실행 수."),
) -> dict:
    """최근 백필 실행 이력 — backfill_audit.jsonl 기반.

    응답::

        {
          "available": true,
          "audit_path": "/.../reports/backfill_audit.jsonl",
          "count": 12,
          "runs": [
            {
              "run_id": "abcd1234",
              "script": "topic_backfill",
              "mode": "dry_run",
              "status": "ok",
              "started_at": "2026-06-05T...",
              "finished_at": "2026-06-05T...",
              "counters": {"seen": 12345, "updated": 0, ...},
              "backup_path": null,
              "env": {...},
              "notes": [...]
            },
            ...
          ],
          "by_script": {
            "topic_backfill":     {"runs": 5, "ok": 5, "error": 0, "last_at": "..."},
            "sentiment_backfill": {"runs": 4, "ok": 4, "error": 0, "last_at": "..."},
            "dedup_voc":          {"runs": 3, "ok": 2, "error": 1, "last_at": "..."}
          }
        }

    ``available=false`` 는 감사 로그 파일이 아직 없다는 의미
    (백필이 한 번도 안 돌았음).

    localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.backfill_audit import list_recent, _audit_path  # type: ignore
    except Exception as e:
        return {
            "available": False, "error": f"backfill_audit import 실패: {e}",
            "runs": [], "by_script": {},
        }
    path = _audit_path()
    runs = list_recent(limit=int(limit))
    if not runs and not path.exists():
        return {
            "available": False, "audit_path": str(path),
            "count": 0, "runs": [], "by_script": {},
        }

    # 스크립트별 누적 — 최근 limit 만이 아닌 *전체* 로그 기반 집계는 비용이 클 수 있어
    # 일단 반환된 limit 윈도우에서만 집계 (운영 ops 페이지 용도엔 충분).
    by_script: Dict[str, Dict[str, Any]] = {}
    for r in runs:
        sc = r.get("script") or "unknown"
        slot = by_script.setdefault(sc, {
            "runs": 0, "ok": 0, "error": 0,
            "last_at": None, "last_status": None, "last_mode": None,
        })
        slot["runs"] += 1
        st = r.get("status") or "unknown"
        if st == "ok":
            slot["ok"] += 1
        elif st == "error":
            slot["error"] += 1
        # runs 는 최신순이므로 첫 등장이 last.
        if slot["last_at"] is None:
            slot["last_at"] = r.get("finished_at") or r.get("started_at")
            slot["last_status"] = st
            slot["last_mode"] = r.get("mode")
    return {
        "available": True,
        "audit_path": str(path),
        "count": len(runs),
        "runs": runs,
        "by_script": by_script,
    }


# ── 백필 감사 요약 (R20 트랙 E — backfill safety monitor) ────────────────
# /backfill-status 는 *원본 row* 를 반환하지만, 운영자는 "최근 7일에 위험 백필이
# 몇 건 있었나" 만 빠르게 보고 싶을 때가 많다.  이 endpoint 는
# crawler.insight.backfill_audit_monitor 가 적용하는 4종 규칙을 백엔드에서 즉시
# 1회 실행하여 alerts + by_script 집계만 슬림 dict 로 반환.
#
# Celery beat `run_backfill_audit_monitor` (매일 09:30 KST) 가 동일 로직으로 alert
# 출력하지만, frontend Ops 페이지 / 운영자 ad-hoc 확인을 위해 endpoint 도 제공.
@router.get("/backfill-audit-summary")
def backfill_audit_summary(
    request: Request,
    days: int = Query(7, ge=1, le=90,
                      description="최근 N일 윈도우 (기본 7, 최대 90)."),
) -> dict:
    """최근 ``days`` 일의 백필 감사 분석 — 위험 규칙 위반 alerts + 스크립트별 집계.

    응답::

        {
          "generated_at": "2026-06-05T...",
          "window_days": 7,
          "total_runs": 12,
          "audit_path": "/.../reports/backfill_audit.jsonl",
          "alerts": [
            {"run_id":"abcd1234", "script":"topic_backfill",
             "rule":"preserve_existing_off", "risk":"critical",
             "started_at":"...", "mode":"preserve_existing",
             "reason":"PRESERVE_EXISTING=False"},
            ...
          ],
          "alert_counts": {"critical": 2, "warning": 1, "info": 0},
          "by_script": {
            "topic_backfill": {"runs":5,"ok":5,"error":0,"violations":2},
            ...
          }
        }

    탐지 규칙 (crawler.insight.backfill_audit_monitor):
      - preserve_existing_off (critical) — 기존 분류 덮어쓸 위험 (R18 사고 원인).
      - backup_disabled       (critical) — 백업 없이 백필.
      - dry_run_off_full      (warning)  — DRY_RUN 우회 + 전체 적용.
      - status_error          (warning)  — 백필 실패 → 데이터 부정합 가능.

    감사 로그 파일 자체가 없으면 ``total_runs=0`` + ``alerts=[]`` 반환.

    localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.backfill_audit_monitor import run as audit_monitor_run  # type: ignore
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"backfill_audit_monitor import 실패: {e}"
        )
    return audit_monitor_run(window_days=int(days))


# ── R22 트랙 E — critical alerts 만 추출 + 원인 attribution ───────────────
# /backfill-audit-summary 는 모든 위반을 평탄하게 반환.  운영자가 *critical 만*
# 빠르게 보고 원인 (env 키 누락 / 진짜 위험) 을 식별하려면 추가 분류가 필요.
@router.get("/audit-critical")
def audit_critical(
    request: Request,
    days: int = Query(7, ge=1, le=90,
                      description="최근 N일 윈도우 (기본 7, 최대 90)."),
) -> dict:
    """critical alerts 집계 + 원인 attribution.

    응답::

        {
          "generated_at": "...",
          "window_days": 7,
          "total_runs": 6,
          "critical_count": 4,
          "by_script": {
            "crisis_platform_direct": {
              "critical": 4,
              "rules": {"preserve_existing_off": 2, "backup_disabled": 2},
              "root_cause": "collector_missing_standard_env_keys",
              "fix_status": "R22_track_E_emitted_DATA_TOUCHED_false"
            }
          },
          "alerts": [...]                # critical 만
        }

    ``root_cause`` 값:
      - ``collector_missing_standard_env_keys`` : insert-only 수집기인데 표준
        DRY_RUN/PRESERVE_EXISTING/BACKUP_BEFORE/DATA_TOUCHED 를 emit 안 함.
        R22 트랙 E 에서 fix.
      - ``true_risk_preserve_off`` : 실 재분류 백필이 PRESERVE 끔.  즉시 조치 요.
      - ``true_risk_backup_off``   : preserve_ok=False 인 실 재분류가 BACKUP 끔.
      - ``unknown``                : 위 패턴에 안 맞음 (사람 판단 필요).

    localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.backfill_audit_monitor import run as audit_monitor_run  # type: ignore
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"backfill_audit_monitor import 실패: {e}"
        )

    payload = audit_monitor_run(window_days=int(days))
    crits = [a for a in payload.get("alerts", []) if a.get("risk") == "critical"]

    # script 별 집계 + 원인 attribution.
    KNOWN_COLLECTORS = {"crisis_platform_direct"}  # R22 트랙 E.
    by_script: Dict[str, Dict[str, Any]] = {}
    for a in crits:
        sc = a.get("script", "unknown")
        slot = by_script.setdefault(sc, {
            "critical": 0,
            "rules": {},
            "root_cause": "unknown",
            "fix_status": None,
        })
        slot["critical"] += 1
        rule = a.get("rule", "unknown")
        slot["rules"][rule] = slot["rules"].get(rule, 0) + 1

        # attribution.
        if sc in KNOWN_COLLECTORS:
            slot["root_cause"] = "collector_missing_standard_env_keys"
            slot["fix_status"] = "R22_track_E_emitted_DATA_TOUCHED_false"
        elif rule == "preserve_existing_off":
            slot["root_cause"] = "true_risk_preserve_off"
            slot["fix_status"] = "operator_action_required"
        elif rule == "backup_disabled":
            slot["root_cause"] = "true_risk_backup_off"
            slot["fix_status"] = "operator_action_required"

    return {
        "generated_at": payload.get("generated_at"),
        "window_days": payload.get("window_days"),
        "total_runs": payload.get("total_runs"),
        "critical_count": len(crits),
        "by_script": by_script,
        "alerts": crits,
        "audit_path": payload.get("audit_path"),
        "thresholds": payload.get("thresholds"),
    }


# ── /dashboard/overview 캐시 워밍업 수동 트리거 (R19 트랙 B) ──────────────
# Celery beat 의 warm-dashboard-overview-5m 가 자동 5분 주기로 실행하지만,
# 운영자가 즉시 캐시를 채우고 싶을 때 (예: backend 재시작 직후) 사용.
# 동일 task 를 *현재 process 안에서* 동기 호출 → Redis 캐시가 곧장 채워진다.
@router.post("/cache-warm")
def cache_warm(request: Request) -> dict:
    """/dashboard/overview 8 case 캐시 워밍업 즉시 실행.

    Celery 워커가 아닌 backend 자신이 호출하는 패턴 — backend 의 Redis 클라이언트가
    그대로 캐시 SET 한다 (워커→백엔드 HTTP 호출보다 1 hop 짧음).

    응답: tasks.warm_dashboard_cache 와 동일 schema::

        {
          "status": "ok" | "partial",
          "warmed": 8,
          "failed": 0,
          "elapsed_ms_total": 245,
          "cases": [{"url","ms","rc"}, ...]
        }
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from tasks import warm_dashboard_cache  # type: ignore
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"warm_dashboard_cache import 실패: {e}")
    # task 함수 객체를 직접 호출 (Celery broker 우회) — 결과 dict 그대로 반환.
    return warm_dashboard_cache()


# ── LoC audit (R21 트랙 B) ───────────────────────────────────────────────
# 워크플로우 보고서의 LoC 표기 vs 실측 검증. R20 권고 2 "query 정합성" 후속.
@router.get("/loc-audit")
def loc_audit(
    request: Request,
    rounds: str = Query(
        "",
        description=(
            "콤마 구분 round 코드 (예: R18,R19,R20). 빈 값이면 모든 R*.md."
        ),
    ),
    threshold: float = Query(
        0.20, ge=0.0, le=1.0,
        description="|drift%| 임계치 (기본 0.20 = 20%).",
    ),
) -> dict:
    """워크플로우 보고서의 LoC 표기 vs 실측 비교 결과.

    응답::

        {
          "generated_at_utc": "...",
          "threshold": 0.20,
          "reports": [
            {
              "round": "R20",
              "path": "docs/dashboard/R20_STABILIZE_2026-06-05.md",
              "claims": [
                {"round":"R20","file":"...","reported":358,"actual":509,
                 "drift":151,"drift_pct":0.297,"source_lines":[12],
                 "alert":true},
                ...
              ]
            },
            ...
          ],
          "summary": {"total_claims":4,"alerts":2,"files_missing":0}
        }

    drift 가 threshold 를 초과하면 ``alert=true``. ``files_missing`` 은
    보고서가 인용했지만 repo 에 존재하지 않는 파일 수.

    localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from scripts.loc_validator import (  # type: ignore
            REPORTS_DIR_DEFAULT,
            _select_reports,
            validate,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"loc_validator import 실패: {e}"
        )
    round_list = [r.strip() for r in rounds.split(",") if r.strip()] or None
    paths = _select_reports(
        REPORTS_DIR_DEFAULT,
        rounds=round_list,
        all_reports=round_list is None,
    )
    if not paths:
        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "threshold": float(threshold),
            "reports": [],
            "summary": {"total_claims": 0, "alerts": 0, "files_missing": 0},
            "note": "no reports matched",
        }
    return validate(paths, threshold=float(threshold))


# ── Workflow validator (R22 트랙 B) ──────────────────────────────────────
# 워크플로우 보고서의 *수치* 표기 vs 실측 자동 동기화.
# loc_validator 가 코드 LoC 만 다루는 것과 달리, 본 endpoint 는 voc_total /
# linked / sentiment_pct / topic_pct / F1 / regression baseline 등 *데이터 지표*
# 의 정합성을 검증한다.  drift 임계 기본 ±10%.
@router.get("/workflow-validate")
def workflow_validate(
    request: Request,
    rounds: str = Query(
        "",
        description=(
            "콤마 구분 round 코드 (예: R20,R21). 빈 값이면 모든 R*.md."
        ),
    ),
    threshold: float = Query(
        0.10, ge=0.0, le=1.0,
        description="|drift%| 임계치 (기본 0.10 = 10%).",
    ),
    inject: bool = Query(
        False,
        description="True 면 보고서 파일 끝에 동기화 블록을 idempotent 하게 삽입.",
    ),
) -> dict:
    """워크플로우 보고서의 *수치* 표기 vs 실측 자동 동기화.

    응답::

        {
          "generated_at_utc": "...",
          "threshold": 0.10,
          "backend": "http://localhost:8000",
          "available": {"regression": true, "coverage": true, "topic_eval": true},
          "measurements": {"voc_total": 119981, "linked": 19534, ...},
          "sources": {"voc_total": "regression-baseline", ...},
          "reports": [
            {
              "round": "R20",
              "path": "docs/dashboard/R20_STABILIZE_2026-06-05.md",
              "alerts": 2,
              "claims": [
                {"round":"R20","metric":"voc_total","reported":150000,
                 "actual":119981,"drift":-30019,"drift_pct":-0.20,
                 "source_line":75,"alert":true,"note":""},
                ...
              ]
            }
          ],
          "summary": {"total_claims":23,"alerts":2,"files_with_alerts":1,
                      "rounds":["R20","R21"]}
        }

    drift 가 threshold 초과 시 ``alert=true``. ``inject=true`` 면 해당 라운드
    보고서 끝에 ``<!-- workflow-sync:* -->`` 블록을 *멱등* 갱신.

    localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.workflow_validator import (  # type: ignore
            REPORTS_DIR_DEFAULT as WF_REPORTS_DIR,
            _select_reports as wf_select_reports,
            validate as wf_validate,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"workflow_validator import 실패: {e}"
        )
    round_list = [r.strip() for r in rounds.split(",") if r.strip()] or None
    paths = wf_select_reports(
        WF_REPORTS_DIR,
        rounds=round_list,
        all_reports=round_list is None,
    )
    if not paths:
        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "threshold": float(threshold),
            "reports": [],
            "summary": {
                "total_claims": 0, "alerts": 0,
                "files_with_alerts": 0, "rounds": [],
            },
            "note": "no reports matched",
        }
    return wf_validate(
        paths,
        threshold=float(threshold),
        inject=bool(inject),
    )


# ── Workflow drift stats (R23 트랙 C) ────────────────────────────────────
# 라운드·트랙별 자기 보고 drift 정량화 + 차등 신뢰도 점수. R22 권고 5
# "workflow drift 자동 정량 → 차등 신뢰도 부여" 의 본체.  workflow_validate 가
# *현재 라운드* 의 보고 vs 실측 alert 를 다룬다면, 본 endpoint 는 *과거 라운드들*
# 의 누적 drift 통계로 *에이전트 트랙별 신뢰도* 를 산출한다.
@router.get("/workflow-drift-stats")
def workflow_drift_stats(
    request: Request,
    rounds: str = Query(
        "",
        description=(
            "콤마 구분 round 코드 (예: R20,R21,R22). 빈 값이면 모든 R*.md."
        ),
    ),
    include_self: bool = Query(
        False,
        description=(
            "True 면 self-report 표본도 통계 집계에 포함 (R23 이전 호환). "
            "기본 False — R24 트랙 C 재귀 편향 보정."
        ),
    ),
) -> dict:
    """워크플로우 자기 보고 drift 통계 + 트랙 신뢰도 점수.

    응답::

        {
          "generated_at_utc": "...",
          "rounds": [
            {"round":"R20","n":3,"mean_abs_pct":21.03,"std_abs_pct":6.36,
             "signed_mean_pct":+21.03,"max_abs_pct":29.67,
             "distribution":{"0-5":0,"5-10":0,"10-20":1,"20-50":2,">=50":0},
             "trust_score":76.5,"systematic_bias":"under_report"},
            ...
          ],
          "tracks": [
            {"round":"R20","track":"B","track_name":"Crisis 한국 ...",
             "n":1,"mean_abs_pct":29.67,"std_abs_pct":0.0,
             "signed_mean_pct":+29.67,"max_abs_pct":29.67,
             "trust_score":70.3,"systematic_bias":null},
            ...
          ],
          "overall": {"rounds_analyzed":3,"total_samples":12,
                      "mean_trust":66.8,
                      "weakest":{"round":"R22","trust_score":58.9},
                      "strongest":{"round":"R20","trust_score":76.5}},
          "available": true
        }

    trust_score 공식: ``100 * (1 - clamp(mean_abs,0,1)) *
    (1 - clamp(std_abs/2, 0, 0.5))``.  0~100 범위.

    localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.workflow_drift_stats import (  # type: ignore
            REPORTS_DIR_DEFAULT as DS_REPORTS_DIR,
            _select_reports as ds_select_reports,
            compute as ds_compute,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"workflow_drift_stats import 실패: {e}"
        )
    round_list = [r.strip() for r in rounds.split(",") if r.strip()] or None
    paths = ds_select_reports(
        DS_REPORTS_DIR,
        rounds=round_list,
        all_reports=round_list is None,
    )
    if not paths:
        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "rounds": [],
            "tracks": [],
            "samples": [],
            "self_samples": [],
            "self_drift": {"n": 0},
            "overall": {
                "rounds_analyzed": 0,
                "total_samples": 0,
                "self_samples_excluded": 0,
                "exclude_self": not include_self,
            },
            "available": False,
            "note": "no reports matched",
        }
    return ds_compute(paths, exclude_self=not include_self)


# ── Crisis 5 product VOC 합계 (R25 트랙 — drift 자동 cross-check) ────────────
#
# 본 endpoint 는 ``crawler/insight/workflow_validator.py`` 의 ``measure_live``
# 가 ``crisis_voc_sum`` 필드를 채우는 데 사용. R24 D 트랙 postmortem 의 결과:
# 워크플로우 보고서의 "변동 N건" claim 을 실측 합계와 자동 cross-check 하려면
# 5 product code (GN7 / GZF1 / GS22U / GZFL3 / GS20) 합계가 필요. regression-baseline
# 은 (GN7/GZF1/GS22/GS25/GB3) 라서 직접 재사용 불가 → 별도 SQL.
_CRISIS_PRODUCT_CODES = ("GN7", "GZF1", "GS22U", "GZFL3", "GS20")

_CRISIS_VOC_SQL = text(
    """
    SELECT p.code, count(v.id)::int AS voc
    FROM products p
    LEFT JOIN voc_records v ON v.product_id = p.id
    WHERE p.code IN ('GN7','GZF1','GS22U','GZFL3','GS20')
    GROUP BY p.code
    """
)


@router.get("/crisis-voc-sum")
async def crisis_voc_sum(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Crisis 5 product VOC 합계 — workflow_validator 의 "건" cross-check 입력.

    응답::

        {
          "by_code": {"GN7":529, "GZF1":381, "GS22U":168, "GZFL3":77, "GS20":298},
          "total": 1453,
          "generated_at": "2026-06-05T..."
        }

    누락된 코드는 응답에 등장하지 않음 (sum 에서 자연 0). localhost only.
    """
    _enforce_localhost(request)
    rows = (await db.execute(_CRISIS_VOC_SQL)).fetchall()
    by_code: Dict[str, int] = {}
    for r in rows:
        by_code[r.code] = int(r.voc or 0)
    return {
        "by_code": by_code,
        "total": sum(by_code.values()),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ── 보고서 drift 자동 캡처 audit (R25 트랙) ─────────────────────────────────
#
# `/_internal/report-drift-audit` 는 워크플로우 자기 보고서의 "N건" 패턴을
# 자동 추출하여 실측 (Crisis VOC 합계 delta) 과 cross-check 한 결과를 JSON 으로
# 반환한다. R24 D 트랙의 "변동 0건" 사고를 *사후 시뮬레이션*·*사전 감지* 모두에
# 사용.
#
# 알려진 라운드 baseline 표 (Crisis VOC 5 product 합계 시점 값).  과거 보고서에
# 명시되었거나 R25 컨텍스트에서 실측 확정된 값.
#   R23 = 373  (R24 보고 row77 의 R23 컬럼).
#   R24 = 373  (R24 보고가 0 변동을 claim → 실측은 +508 폭증 = 사고).
# Crisis 라운드 baseline 표가 갱신되면 라운드별로 추가.
_CRISIS_BASELINE_BY_ROUND: Dict[str, int] = {
    "R23": 373,
    "R24": 373,
}


@router.get("/report-drift-audit")
async def report_drift_audit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    round: str = Query(
        "",
        description=(
            "검증 대상 round 코드 (예: R24). 빈 값이면 docs/dashboard/ 의 최신 R*.md."
        ),
    ),
    crisis_baseline: Optional[int] = Query(
        None,
        description=(
            "Crisis VOC 5 product 합계 비교 baseline (정수). 미지정 시 round 별 "
            "기본 표 (_CRISIS_BASELINE_BY_ROUND) 사용. 표에 없으면 None — "
            "Crisis 관련 'N건' claim 은 cross-check skip."
        ),
    ),
    threshold: float = Query(
        0.10,
        description="|drift_pct| 임계치 (기본 0.10 = 10%). |Δ| 초과 시 alert.",
    ),
) -> dict:
    """워크플로우 보고서의 "N건" 패턴 자동 drift 캡처.

    응답::

        {
          "round": "R24",
          "report_path": "docs/dashboard/R24_EXTEND_2026-06-05.md",
          "threshold": 0.10,
          "crisis": {
            "by_code": {"GN7":529, ...},
            "total": 1453,
            "baseline": 373,
            "baseline_source": "default_table[R24]",
            "live_delta": 1080
          },
          "claims": [
            {"metric":"crisis_delta_geon", "source_line":40,
             "reported":0, "actual":1080, "drift_pct":1.0, "alert":true,
             "note":"..."},
            ...
          ],
          "summary": {"total":N, "alerts":M, "geon_claims":K},
          "generated_at": "2026-06-05T..."
        }

    drift 자동 캡처 정책:
      - parse_report 의 STRICT bold `**N건**` + CRISIS narrative
        (`crisis ... 변동/추가/신규/증감 N건`) 두 패턴을 동시 적용.
      - Crisis 컨텍스트가 있고 baseline 이 있는 경우에만 actual cross-check.
      - drift_pct |Δ| > threshold → alert=True.

    R24 D 사고 (변동 0건 claim vs 실측 +508 폭증) 가 이 endpoint 로 자동 감지된다.
    localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.workflow_validator import (  # type: ignore
            parse_report as wv_parse_report,
            REPORTS_DIR_DEFAULT as WV_REPORTS_DIR,
            _round_from_filename as wv_round_from_filename,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"workflow_validator import 실패: {e}"
        )

    # 1) 대상 보고서 선택
    target_path: Optional[Path] = None
    if round:
        round_uc = round.strip().upper()
        candidates = sorted(WV_REPORTS_DIR.glob(f"{round_uc}_*.md"))
        if candidates:
            target_path = candidates[-1]
    else:
        # 최신 R*.md (라운드 번호 내림차순)
        all_files = sorted(WV_REPORTS_DIR.glob("R*.md"))
        if all_files:
            target_path = all_files[-1]
    if target_path is None or not target_path.is_file():
        return {
            "round": round or None,
            "report_path": None,
            "available": False,
            "note": "no matching report",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    target_round = wv_round_from_filename(target_path)

    # 2) 라이브 Crisis 합계 + by_code
    rows = (await db.execute(_CRISIS_VOC_SQL)).fetchall()
    by_code: Dict[str, int] = {r.code: int(r.voc or 0) for r in rows}
    crisis_total = sum(by_code.values())

    # 3) baseline 결정 — 명시 인자 > 기본 표 > 매칭 round 표 > None
    baseline_value: Optional[int] = None
    baseline_source: str = "missing"
    if crisis_baseline is not None:
        baseline_value = int(crisis_baseline)
        baseline_source = "query_param"
    elif target_round in _CRISIS_BASELINE_BY_ROUND:
        baseline_value = _CRISIS_BASELINE_BY_ROUND[target_round]
        baseline_source = f"default_table[{target_round}]"

    live_delta = (
        crisis_total - baseline_value if baseline_value is not None else None
    )

    # 4) parse_report 에 live dict 주입 (Crisis baseline 포함).
    live = {
        "available": {
            "regression": False, "coverage": False, "topic_eval": False,
            "crisis": True,
        },
        "metrics": {
            "crisis_voc_sum": crisis_total,
        },
        "sources": {
            "crisis_voc_sum": "report-drift-audit (internal)",
        },
        "crisis_baseline": baseline_value,
        "backend": "internal",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    for code, val in by_code.items():
        live["metrics"][f"crisis_{code}"] = val

    claims = wv_parse_report(target_path, live, threshold=threshold)
    # 보고서가 만든 모든 claim 중 "N건" 계열만 추려 응답.  (기존 metric 풀과 분리.)
    geon_metric_set = {"crisis_delta_geon", "crisis_geon_bold", "geon_bold"}
    geon_claims = [
        {
            "metric": c.metric,
            "source_line": c.source_line,
            "reported": c.reported,
            "actual": c.actual,
            "drift": c.drift,
            "drift_pct": c.drift_pct,
            "alert": c.alert,
            "note": c.note,
        }
        for c in claims if c.metric in geon_metric_set
    ]
    alerts = sum(1 for x in geon_claims if x["alert"])

    rel_path = (
        str(target_path.relative_to(Path(__file__).resolve().parents[3]))
        if str(target_path).startswith(str(Path(__file__).resolve().parents[3]))
        else str(target_path)
    )
    return {
        "round": target_round,
        "report_path": rel_path,
        "threshold": threshold,
        "crisis": {
            "by_code": by_code,
            "total": crisis_total,
            "baseline": baseline_value,
            "baseline_source": baseline_source,
            "live_delta": live_delta,
        },
        "claims": geon_claims,
        "summary": {
            "total": len(geon_claims),
            "alerts": alerts,
            "geon_claims": len(geon_claims),
        },
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ── audit JSONL round 통계 (R25 트랙 C/E) ────────────────────────────────
# 운영 안정 임계 조정의 마지막 퍼즐 — backfill_audit.jsonl 을 round 라벨로 집계해
# 같은 round 내 run 성공률·violation 패턴을 한 endpoint 에 노출.
# workflow_drift_stats 가 *보고서* drift 의 trust 점수를 준다면, 본 endpoint 는
# *실 execute* (audit JSONL) 의 round 별 신뢰도를 보완한다.
#
# trust 점수 (round-level, 0~100):
#     ok_ratio   = ok_runs / total_runs           (성공률)
#     viol_ratio = violations / max(1, runs)      (위반 빈도, run 당)
#     trust      = 100 * ok_ratio * (1 - clamp(viol_ratio, 0, 1))
#
# 임계 (workflow_drift_stats 와 동일 — SIGNALFORGE_TRUST_WARNING/CRITICAL):
#     < critical (기본 60)  → 'critical' — 즉시 알림
#     < warning  (기본 80)  → 'warning'  — 다음 라운드 점검 권고
#     그 외                 → 'normal'
@router.get("/audit-round-stats")
def audit_round_stats(
    request: Request,
    days: int = Query(7, ge=1, le=90, description="최근 며칠 윈도우 (기본 7)."),
    audit_path: str = Query(
        "",
        description=(
            "audit JSONL 경로. 빈 값이면 ``reports/backfill_audit.jsonl`` 사용."
        ),
    ),
) -> dict:
    """audit JSONL round 별 통계 + trust 점수.

    응답::

        {
          "generated_at": "...",
          "window_days": 7,
          "audit_path": "/home/.../reports/backfill_audit.jsonl",
          "available": true,
          "total_runs": 32,
          "rounds": [
            {"round":"R24","runs":12,"ok":11,"error":1,"violations":2,
             "ok_ratio":0.917,"viol_ratio":0.167,
             "trust_score":76.4,"trust_level":"warning"},
            {"round":"unlabeled","runs":20,"ok":20,"error":0,"violations":0,
             "ok_ratio":1.0,"viol_ratio":0.0,
             "trust_score":100.0,"trust_level":"normal"}
          ],
          "overall": {
            "rounds_count": 2,
            "mean_trust": 88.2,
            "trust_thresholds": {"critical_below": 60.0, "warning_below": 80.0},
            "trust_level_counts": {"critical":0,"warning":1,"normal":1}
          }
        }

    임계 환경변수:
      - ``SIGNALFORGE_TRUST_WARNING``  (기본 80) — 이 미만 warning
      - ``SIGNALFORGE_TRUST_CRITICAL`` (기본 60) — 이 미만 critical

    Localhost only.  audit JSONL 부재 시 ``available=False`` (graceful).
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.backfill_audit_monitor import (  # type: ignore
            _load_jsonl as _audit_load_jsonl,
            summarize as _audit_summarize,
        )
        from insight.workflow_drift_stats import (  # type: ignore
            classify_trust as _classify_trust,
            _trust_thresholds as _drift_thresholds,
            compute_trust_7day_distribution as _compute_7day,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"audit/drift import 실패: {e}")

    # 경로 결정.  빈 값이면 repo_root/reports/backfill_audit.jsonl.
    if audit_path.strip():
        p = Path(audit_path.strip())
    else:
        repo_root = Path(__file__).resolve().parents[3]
        p = Path(os.getenv("BACKFILL_AUDIT_PATH", str(repo_root / "reports" / "backfill_audit.jsonl")))

    runs = _audit_load_jsonl(p)
    if not runs:
        crit_th, warn_th = _drift_thresholds()
        # R27 트랙 D — audit 비어도 drift 만으로 7일 분포 계산.
        trust_7day_empty: Optional[Dict[str, Any]] = None
        try:
            repo_root_e = Path(__file__).resolve().parents[3]
            trust_7day_empty = _compute_7day(
                reports_dir=repo_root_e / "docs" / "dashboard",
                audit_path=None,
                days=int(days),
            )
        except Exception:
            trust_7day_empty = None
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window_days": int(days),
            "audit_path": str(p),
            "available": False,
            "total_runs": 0,
            "rounds": [],
            "overall": {
                "rounds_count": 0,
                "mean_trust": None,
                "trust_thresholds": {
                    "critical_below": crit_th, "warning_below": warn_th,
                },
                "trust_level_counts": {"critical": 0, "warning": 0, "normal": 0},
            },
            "trust_7day_distribution": trust_7day_empty,
            "note": "audit JSONL 부재 또는 빈 파일",
        }

    payload = _audit_summarize(runs, window_days=int(days))
    by_round_raw = payload.get("by_round") or {}

    crit_th, warn_th = _drift_thresholds()

    # R26 트랙 C/E — round 정렬을 *시간순* (R7 < R20 < R21 ... < unlabeled) 으로
    # 통일.  R<숫자> 는 숫자 키로 sort, 'unlabeled' 는 항상 마지막.
    def _round_sort_key(name: str) -> Tuple[int, int, str]:
        if not name or name == "unlabeled":
            return (1, 0, name or "")
        try:
            num = int(name.lstrip("Rr"))
            return (0, num, name)
        except ValueError:
            return (0, 10**9, name)

    rounds_out: List[Dict[str, Any]] = []
    level_counts = {"critical": 0, "warning": 0, "normal": 0}
    trusts: List[float] = []
    prev_trust: Optional[float] = None

    for rnd in sorted(by_round_raw.keys(), key=_round_sort_key):
        slot = by_round_raw[rnd]
        runs_n = int(slot.get("runs") or 0)
        ok_n = int(slot.get("ok") or 0)
        err_n = int(slot.get("error") or 0)
        viol_n = int(slot.get("violations") or 0)
        ok_ratio = (ok_n / runs_n) if runs_n > 0 else 0.0
        viol_ratio = (viol_n / runs_n) if runs_n > 0 else 0.0
        viol_clamped = max(0.0, min(1.0, viol_ratio))
        trust = round(100.0 * ok_ratio * (1.0 - viol_clamped), 1)
        level = _classify_trust(trust)
        level_counts[level] = level_counts.get(level, 0) + 1
        trusts.append(trust)
        # R26 — round 간 trust 변화량 (직전 round 대비, 처음은 None).
        delta = round(trust - prev_trust, 1) if prev_trust is not None else None
        prev_trust = trust
        rounds_out.append({
            "round": rnd,
            "runs": runs_n,
            "ok": ok_n,
            "error": err_n,
            "violations": viol_n,
            "ok_ratio": round(ok_ratio, 3),
            "viol_ratio": round(viol_ratio, 3),
            "trust_score": trust,
            "trust_level": level,
            "trust_delta_vs_prev": delta,
        })

    mean_trust = round(sum(trusts) / len(trusts), 1) if trusts else None

    # R26 — round 간 안정성 지표.
    #   stability_index = max - min (작을수록 안정, 0 = 완전 평탄).
    #   trend            = 마지막 trust - 첫 trust (양수 = 개선 추세).
    stability_index: Optional[float] = None
    trend: Optional[float] = None
    weakest_round: Optional[Dict[str, Any]] = None
    strongest_round: Optional[Dict[str, Any]] = None
    if trusts:
        stability_index = round(max(trusts) - min(trusts), 1)
        if len(trusts) >= 2:
            trend = round(trusts[-1] - trusts[0], 1)
        # weakest/strongest (round 메타 포함).
        w_idx = trusts.index(min(trusts))
        s_idx = trusts.index(max(trusts))
        weakest_round = {
            "round": rounds_out[w_idx]["round"],
            "trust_score": rounds_out[w_idx]["trust_score"],
            "trust_level": rounds_out[w_idx]["trust_level"],
        }
        strongest_round = {
            "round": rounds_out[s_idx]["round"],
            "trust_score": rounds_out[s_idx]["trust_score"],
            "trust_level": rounds_out[s_idx]["trust_level"],
        }

    # R26 — 임계 정밀화 권고 (7일 분포 기반).
    #   data-driven: 현재 임계가 7일 trust 분포 (audit + drift) 를 자연 분리하는지
    #   를 정량 판정.  level_counts 가 모두 한 level 에 몰리거나 critical 만
    #   지속이면 권고 변경.
    recommendation: Optional[str] = None
    if trusts:
        n_total = len(trusts)
        # 모두 normal → 임계 너무 느슨 (warning 가시성 0).
        if level_counts["normal"] == n_total and n_total >= 2:
            recommendation = (
                "all_normal_consider_tightening: 모든 round normal → "
                "SIGNALFORGE_TRUST_WARNING 을 +2~+5 올려 warning 가시성 확보"
            )
        # 모두 critical → 임계 너무 빡세거나 실제 시스템 문제.
        elif level_counts["critical"] == n_total and n_total >= 2:
            recommendation = (
                "all_critical_investigate: 모든 round critical → "
                "audit 룰 정합성 점검 또는 SIGNALFORGE_TRUST_CRITICAL 재교정"
            )
        # 자연 분리 (critical 1+ AND warning/normal 1+).
        elif level_counts["critical"] >= 1 and (
            level_counts["warning"] + level_counts["normal"]
        ) >= 1:
            recommendation = "natural_separation_keep_60_80: 60/80 기본 유지 권고"
        else:
            recommendation = "monitor_one_more_round: 표본 부족, 1 round 추가 관측"

    # R27 트랙 D — 7일 trust 분포 (drift + audit 결합) + 임계 정밀화 권고.
    # graceful: 실패 시 None 으로 통과 (기존 응답 schema 유지).
    trust_7day: Optional[Dict[str, Any]] = None
    try:
        repo_root2 = Path(__file__).resolve().parents[3]
        reports_dir2 = repo_root2 / "docs" / "dashboard"
        trust_7day = _compute_7day(
            reports_dir=reports_dir2,
            audit_path=p,
            days=int(days),
        )
    except Exception:
        trust_7day = None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": int(days),
        "audit_path": str(p),
        "available": True,
        "total_runs": int(payload.get("total_runs") or 0),
        "rounds": rounds_out,
        "overall": {
            "rounds_count": len(rounds_out),
            "mean_trust": mean_trust,
            "trust_thresholds": {
                "critical_below": crit_th, "warning_below": warn_th,
            },
            "trust_level_counts": level_counts,
            "stability_index": stability_index,
            "trend": trend,
            "weakest_round": weakest_round,
            "strongest_round": strongest_round,
            "recommendation": recommendation,
        },
        "trust_7day_distribution": trust_7day,
    }


# ── Validator hook status (R26) ──────────────────────────────────────────
# 후크 가동 상태 + 최근 5회 검증 결과.  R25 회고 (archive 부재 self-report
# drift) 같은 사고를 작성 직후 자동 캡처하는 polling 후크의 운영 가시성.
# Celery beat `validator-hook-5m` 가 매 5분 ``tasks.run_validator_hook`` 을
# 호출하면 state file 이 갱신된다.  본 endpoint 는 *state file 만 읽고* 직접
# 후크를 trigger 하지 않는다 — operational read-only.
@router.get("/validator-hook-status")
def validator_hook_status(
    request: Request,
    trigger: bool = Query(
        False,
        description=(
            "True 면 endpoint 호출 즉시 후크를 1 회 실행 (강제 trigger). "
            "Celery 미가동 환경 디버깅 용.  기본 False (read-only)."
        ),
    ),
) -> dict:
    """validator 후크 가동 상태 + 최근 5회 검증 결과.

    응답::

        {
          "hook_active": true,
          "last_scan_utc": "2026-06-05T22:13:00+00:00",
          "scan_count": 42,
          "last_alerts_total": 1,
          "last_archive_drift_total": 1,
          "history": [
            {"round":"R25","report_path":"docs/dashboard/R25_DEEPEN_2026-06-05.md",
             "scanned_at_utc":"...","alerts":2,
             "archive_drift":["reports/archive/R25/ (missing)"]},
            ...
          ],
          "state_path": ".../reports/validator_hook_state.json"
        }

    Localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.workflow_validator_hook import (  # type: ignore
            run as hook_run,
            status as hook_status,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"workflow_validator_hook import 실패: {e}"
        )
    if trigger:
        # 1 회 강제 실행 후 최신 상태 반환.
        hook_run()
    return hook_status()


# ── Hook 1주 운영 모니터링 (R27 트랙 C) ───────────────────────────────────
# /validator-hook-status 는 현재 후크 상태 + 최근 5건 history 만 반환.
# /hook-history 는 그 history 위에 1주 운영 통계 (scan_count, alerts_total,
# archive_drift, false positive 분류, mean/max drift) + 권고를 합성.
# Frontend 운영 대시보드 및 daily quality_report 가 공통 사용.
@router.get("/hook-history")
def hook_history(
    request: Request,
    days: int = Query(7, ge=1, le=30, description="윈도우 (1~30일)."),
) -> dict:
    """validator hook 1주 운영 통계 + false positive 분석 + 권고.

    응답 schema 는 ``insight.hook_monitor.compute()`` 와 동일::

        {
          "generated_at_utc": "...",
          "days": 7,
          "available": true,
          "state": {hook_active, scan_count, last_scan_utc, ...},
          "summary": {
            "scan_count": 42, "alerts_total": 3,
            "archive_drift_total": 1, "mean_drift_pct": 18.5,
            "max_drift_pct": 55.0, "validate_reports": 3
          },
          "history": [...],          // 상태 파일의 history (최근 5)
          "validate_reports": [...], // workflow_validate_R*.md mtime 윈도우 내
          "archive_existing": ["R26"],
          "archive_drift_unresolved": ["R25"],
          "false_positive_analysis": {
            "persistent_unresolved": [...], "resolved": [...],
            "one_shot_with_alerts": [...], "clean": [...]
          },
          "recommendations": [...]
        }

    Localhost only.
    """
    _enforce_localhost(request)
    _ensure_crawler_on_path()
    try:
        from insight.hook_monitor import compute as hook_compute  # type: ignore
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"hook_monitor import 실패: {e}"
        )
    return hook_compute(days=int(days))


# ── 양방향 Drive 동기화 상태 (Stage 4.5 — Y5 트랙) ────────────────────────
# Stage 4.5 auto_sync 라운드:
#   - 송신 측 (원본 호스트): scripts/sync-to-drive.sh 가 주기 가동.
#   - 수신 측 (이관 서버):   scripts/sync-from-drive.sh 가 LATEST.json delta 감지.
# 두 측 모두 logs/audit/portal_deploy.jsonl 에 event 를 남기므로 (script 필드로
# 구분: 'sync-to-drive' vs 'sync-from-drive'), 본 endpoint 는 그 JSONL 을 tail 하여
# 양측의 가장 최근 성공 시각·운영 모드(dry-run)·산출물(파일/태그)을 한 응답으로 제공.
#
# 추가로 apptainer/sif/LATEST.json (manifest-driven delta 의 핵심) 이 있으면
# 그 내용을 그대로 노출 — 송신 측이 push 마다 갱신, 수신 측이 O(1) 비교에 사용.
#
# 보안: localhost only. 외부 노출 금지.

# 송신/수신 양측이 공유하는 audit JSONL 경로.  Y1-Y4 가 동일 파일에 기록.
_AUTO_SYNC_AUDIT_DEFAULT = Path(
    "/home/koopark/claude/SignalForge/logs/audit/portal_deploy.jsonl"
)

# sif manifest (push 측이 매 사이클마다 갱신, pull 측은 delta 판정 입력).
_AUTO_SYNC_LATEST_DEFAULT = Path(
    "/home/koopark/claude/SignalForge/apptainer/sif/LATEST.json"
)

# 성공으로 간주할 event 토큰 — 부정확한 잠금/실패 이벤트는 별도 카운트.
_AUTO_SYNC_OK_EVENTS = {"end", "db_ok", "sif_ok", "env_ok", "db_restored"}
_AUTO_SYNC_FAIL_EVENTS = {"fail", "sif_skip", "db_skip", "env_skip"}


def _auto_sync_audit_path() -> Path:
    """``AUTO_SYNC_AUDIT_FILE`` env 우선, 없으면 portal_deploy.jsonl 기본."""
    env = os.getenv("AUTO_SYNC_AUDIT_FILE", "").strip()
    return Path(env) if env else _AUTO_SYNC_AUDIT_DEFAULT


def _auto_sync_latest_path() -> Path:
    """``AUTO_SYNC_LATEST_FILE`` env 우선, 없으면 apptainer/sif/LATEST.json 기본."""
    env = os.getenv("AUTO_SYNC_LATEST_FILE", "").strip()
    return Path(env) if env else _AUTO_SYNC_LATEST_DEFAULT


def _tail_audit_lines(path: Path, limit: int = 2000) -> List[Dict[str, Any]]:
    """audit JSONL 마지막 ``limit`` 줄을 파싱.  파일 부재/파싱 실패 시 [].

    portal_deploy.jsonl 은 라운드 종료 후 ``logs/audit/archive/`` 로 회전되지 않고
    누적되므로, 메모리 제한 위해 tail 만 읽는다.
    """
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        out: List[Dict[str, Any]] = []
        for ln in lines[-int(limit):]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    out.append(obj)
            except Exception:
                # 손상 라인은 graceful skip.
                continue
        return out
    except Exception:
        return []


def _summarize_side(events: List[Dict[str, Any]], script_name: str) -> Dict[str, Any]:
    """주어진 script (sync-to-drive / sync-from-drive) 의 최근 활동 요약.

    반환::

        {
          "script": "sync-to-drive",
          "available": true,
          "last_event": {"ts": "...", "event": "end", "dry_run": 0, ...},
          "last_success": {"ts": "...", "event": "end", ...} | null,
          "last_run_id": "...",
          "last_run": {
            "run_id": "...", "started_at": "...", "ended_at": "...",
            "dry_run": 1, "events": ["start","sif_dryrun","env_dryrun","end"],
            "ok": true, "fail_events": []
          },
          "counters_24h": {"runs": 3, "ok": 3, "fail": 0, "dry_runs": 1},
          "counters_7d":  {"runs": 12, "ok": 11, "fail": 1, "dry_runs": 4}
        }

    events 가 비면 ``available: false`` (아직 한 번도 실행 안 됨).
    """
    side = [e for e in events if e.get("script") == script_name]
    if not side:
        return {
            "script": script_name,
            "available": False,
            "last_event": None,
            "last_success": None,
            "last_run_id": None,
            "last_run": None,
            "counters_24h": {"runs": 0, "ok": 0, "fail": 0, "dry_runs": 0},
            "counters_7d": {"runs": 0, "ok": 0, "fail": 0, "dry_runs": 0},
        }
    last_event = side[-1]
    last_success = None
    for ev in reversed(side):
        if ev.get("event") in _AUTO_SYNC_OK_EVENTS:
            last_success = ev
            break
    last_run_id = last_event.get("run_id")
    run_events: List[Dict[str, Any]] = [
        e for e in side if e.get("run_id") == last_run_id
    ]
    started_at = None
    ended_at = None
    fail_events: List[str] = []
    for ev in run_events:
        evname = ev.get("event")
        if evname == "start" and started_at is None:
            started_at = ev.get("ts")
        if evname == "end":
            ended_at = ev.get("ts")
        if evname in _AUTO_SYNC_FAIL_EVENTS:
            fail_events.append(evname)
    last_run = {
        "run_id": last_run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "dry_run": int(last_event.get("dry_run", 0)) if last_event else 0,
        "events": [e.get("event") for e in run_events],
        "ok": bool(ended_at) and not fail_events,
        "fail_events": fail_events,
    }
    now = datetime.now(timezone.utc)
    counters_24h = {"runs": 0, "ok": 0, "fail": 0, "dry_runs": 0}
    counters_7d = {"runs": 0, "ok": 0, "fail": 0, "dry_runs": 0}
    by_run: Dict[str, List[Dict[str, Any]]] = {}
    for ev in side:
        rid = ev.get("run_id") or ""
        by_run.setdefault(rid, []).append(ev)
    for rid, evs in by_run.items():
        start_ts_raw = next(
            (e.get("ts") for e in evs if e.get("event") == "start"),
            evs[0].get("ts"),
        )
        if not start_ts_raw:
            continue
        try:
            start_ts = datetime.fromisoformat(str(start_ts_raw).replace("Z", "+00:00"))
        except Exception:
            continue
        age_h = (now - start_ts).total_seconds() / 3600.0
        ok = any(e.get("event") == "end" for e in evs) and not any(
            e.get("event") in _AUTO_SYNC_FAIL_EVENTS for e in evs
        )
        fail = any(e.get("event") in _AUTO_SYNC_FAIL_EVENTS for e in evs)
        dry = any(int(e.get("dry_run", 0)) == 1 for e in evs)
        if age_h <= 24.0:
            counters_24h["runs"] += 1
            counters_24h["ok"] += int(ok)
            counters_24h["fail"] += int(fail)
            counters_24h["dry_runs"] += int(dry)
        if age_h <= 24.0 * 7:
            counters_7d["runs"] += 1
            counters_7d["ok"] += int(ok)
            counters_7d["fail"] += int(fail)
            counters_7d["dry_runs"] += int(dry)
    return {
        "script": script_name,
        "available": True,
        "last_event": last_event,
        "last_success": last_success,
        "last_run_id": last_run_id,
        "last_run": last_run,
        "counters_24h": counters_24h,
        "counters_7d": counters_7d,
    }


def _read_latest_manifest(path: Path) -> Dict[str, Any]:
    """LATEST.json 을 dict 으로 읽기.  없으면 ``{"available": false}``."""
    if not path.exists():
        return {"available": False, "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"available": False, "path": str(path), "error": f"parse fail: {e}"}
    if not isinstance(data, dict):
        return {"available": False, "path": str(path), "error": "not a JSON object"}
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except Exception:
        mtime = None
    out: Dict[str, Any] = {"available": True, "path": str(path), "mtime": mtime}
    out.update(data)
    return out


@router.get("/sync-status")
def sync_status(request: Request, tail: int = Query(2000, ge=10, le=10000)) -> dict:
    """양방향 Drive 동기화 운영 상태 (Stage 4.5 auto_sync Y5 트랙).

    portal_deploy.jsonl audit 로그를 tail 하여 송신·수신 양측의 최근 운영 상태를
    한 응답으로 제공.  LATEST.json (apptainer/sif manifest) 이 있으면 delta 판정
    입력도 함께 노출.

    응답::

        {
          "generated_at": "2026-06-07T...",
          "audit_path": "/home/.../logs/audit/portal_deploy.jsonl",
          "audit_available": true,
          "tail_lines": 2000,
          "send": {  # sync-to-drive
            "available": true,
            "last_event": {...}, "last_success": {...},
            "last_run": {...},
            "counters_24h": {"runs":3,"ok":3,"fail":0,"dry_runs":1},
            "counters_7d":  {"runs":12,...}
          },
          "recv": {  # sync-from-drive
            ...
          },
          "latest_manifest": {
            "available": true, "path": "...", "mtime": "...",
            "ts": "...", "db_sha256": "...", "sif_tag": "sif-20260607-231406Z",
            ...
          },
          "summary": {
            "send_ok_24h": true,
            "recv_ok_24h": true,
            "any_fail_24h": false,
            "latest_present": true
          }
        }

    - ``audit_available: false`` 면 JSONL 자체가 없음 (auto_sync 미가동).
    - ``send.available: false`` 면 송신 측 한 번도 안 돌았음.
    - ``recv.available: false`` 면 수신 측 한 번도 안 돌았음 (원본 호스트에선 정상).
    - localhost only. tail 은 portal_deploy.jsonl 의 최근 N 줄 (기본 2000).
    """
    _enforce_localhost(request)
    audit_path = _auto_sync_audit_path()
    events = _tail_audit_lines(audit_path, limit=int(tail))
    send = _summarize_side(events, "sync-to-drive")
    recv = _summarize_side(events, "sync-from-drive")
    latest = _read_latest_manifest(_auto_sync_latest_path())

    def _within_24h_ok(side_dict: Dict[str, Any]) -> bool:
        return int(side_dict.get("counters_24h", {}).get("ok", 0)) > 0

    summary = {
        "send_ok_24h": _within_24h_ok(send),
        "recv_ok_24h": _within_24h_ok(recv),
        "any_fail_24h": (
            int(send.get("counters_24h", {}).get("fail", 0)) > 0
            or int(recv.get("counters_24h", {}).get("fail", 0)) > 0
        ),
        "latest_present": bool(latest.get("available")),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audit_path": str(audit_path),
        "audit_available": audit_path.exists(),
        "tail_lines": int(tail),
        "send": send,
        "recv": recv,
        "latest_manifest": latest,
        "summary": summary,
    }


# 2026-06-08 C5: 데이터 진위 endpoint — 사용자 "진짜 데이터지 전부?" 신뢰 검증
_MX_REGEX = (
    r"(samsung|galaxy|갤럭시|삼성|폴드|플립|oneui|exynos|tab s|buds|"
    r"apple|iphone|아이폰|ios|airpods|애플|ipad|pixel|픽셀|"
    r"xiaomi|샤오미|huawei|화웨이|oppo|vivo|oneplus|원플러스|honor|아너|redmi|poco|"
    r"smartphone|스마트폰|핸드폰|휴대폰|foldable|폴더블|smartwatch|스마트워치)"
)


@router.get("/data-quality")
async def data_quality(db: AsyncSession = Depends(get_db)) -> dict:
    """진짜 인사이트 비율 + 사이트별 매칭율 (worst/best).

    archived_at 으로 정리한 한국 5개 (instiz/dcinside/ppomppu/dogdrip/danawa) 정책
    효과 확인용. C2 정리 후 활성 ≈ 76k, MX 매칭율 ≈ 95%+ 목표.
    """
    base_row = (await db.execute(text(
        """
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE archived_at IS NULL) AS active,
          count(*) FILTER (WHERE archived_at IS NOT NULL) AS archived,
          count(*) FILTER (WHERE archived_at IS NULL AND content_original ~* :pat) AS mx_match,
          count(*) FILTER (WHERE archived_at IS NULL AND content_original ~* :pat AND length(content_original) >= 100) AS mx_rich
        FROM voc_records
        """
    ), {"pat": _MX_REGEX})).mappings().first()

    # R6 M6: platform name/region 보강. 프론트에서 region 표시 + 한국/글로벌 그룹핑.
    by_site = (await db.execute(text(
        """
        SELECT pl.code, pl.name, pl.region, count(*) AS active,
               count(*) FILTER (WHERE v.content_original ~* :pat) AS mx_match,
               round(100.0 * count(*) FILTER (WHERE v.content_original ~* :pat) / count(*), 1) AS match_pct
        FROM voc_active v JOIN platforms pl ON v.platform_id=pl.id
        WHERE v.archived_at IS NULL
        GROUP BY pl.code, pl.name, pl.region HAVING count(*) > 30
        ORDER BY match_pct ASC LIMIT 20
        """
    ), {"pat": _MX_REGEX})).mappings().all()

    total = int(base_row["total"])
    active = int(base_row["active"])
    archived = int(base_row["archived"])
    mx_match = int(base_row["mx_match"])
    mx_rich = int(base_row["mx_rich"])

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": total,
        "active": active,
        "archived": archived,
        "archived_pct": round(100.0 * archived / total, 1) if total else 0,
        "mx_match_active": mx_match,
        "mx_match_pct": round(100.0 * mx_match / active, 1) if active else 0,
        "mx_rich_active": mx_rich,
        "mx_rich_pct": round(100.0 * mx_rich / active, 1) if active else 0,
        "by_site_worst": [dict(r) for r in by_site],
    }


# 2026-06-10 R6 M7: 24h 신규 INSERT 사이트별 통계 — beat cycle 검증·MX drift 추적용
@router.get("/collection-stats")
async def collection_stats(db: AsyncSession = Depends(get_db)) -> dict:
    """24h / 7d 신규 INSERT 사이트별 카운트 + 24h MX 매칭율.

    voc_records WHERE archived_at IS NULL AND collected_at >= now() - interval N.
    by_site 는 collected_at 기준으로 R6 자동 cycle (fourchan_g·misskey 등) 검증용.
    """
    h24_row = (await db.execute(text(
        """
        SELECT
          count(*) AS h24_total,
          count(*) FILTER (WHERE content_original ~* :pat) AS h24_mx_match
        FROM voc_records
        WHERE archived_at IS NULL
          AND collected_at >= now() - interval '24 hours'
        """
    ), {"pat": _MX_REGEX})).mappings().first()

    h7d_row = (await db.execute(text(
        """
        SELECT count(*) AS h7d_total
        FROM voc_records
        WHERE archived_at IS NULL
          AND collected_at >= now() - interval '7 days'
        """
    ))).mappings().first()

    h24_by_site = (await db.execute(text(
        """
        SELECT pl.code, pl.region, count(*) AS h24_new
        FROM voc_records v JOIN platforms pl ON v.platform_id = pl.id
        WHERE v.archived_at IS NULL
          AND v.collected_at >= now() - interval '24 hours'
        GROUP BY pl.code, pl.region
        ORDER BY h24_new DESC
        """
    ))).mappings().all()

    h7d_by_site = (await db.execute(text(
        """
        SELECT pl.code, pl.region, count(*) AS h7d_new
        FROM voc_records v JOIN platforms pl ON v.platform_id = pl.id
        WHERE v.archived_at IS NULL
          AND v.collected_at >= now() - interval '7 days'
        GROUP BY pl.code, pl.region
        ORDER BY h7d_new DESC
        """
    ))).mappings().all()

    h24_total = int(h24_row["h24_total"])
    h24_mx_match = int(h24_row["h24_mx_match"])

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "h24_total": h24_total,
        "h7d_total": int(h7d_row["h7d_total"]),
        "mx_match_h24": h24_mx_match,
        "mx_match_h24_pct": round(100.0 * h24_mx_match / h24_total, 1) if h24_total else 0,
        "h24_by_site": [dict(r) for r in h24_by_site],
        "h7d_by_site": [dict(r) for r in h7d_by_site],
    }
