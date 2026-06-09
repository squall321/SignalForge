"""P4 트랙 A — 실시간 알림 API.

prefix: /api/v1/alerts

엔드포인트:
- WS  /api/v1/alerts/ws         : 알림 라이브 스트림 (서버 → 클라이언트)
- GET /api/v1/alerts/rules      : 활성 룰 목록
- POST /api/v1/alerts/rules     : 룰 생성 (DB 영구화 + RuleEngine 즉시 반영)
- DELETE /api/v1/alerts/rules/{id}
- POST /api/v1/alerts/test      : 모든 룰을 라이브 metrics 로 1회 평가 + dry-run 전송
- GET /api/v1/alerts/recent     : 최근 발화 이력 (limit=50)

설계 결정:
- AlertConnectionManager 는 모듈 레벨 싱글톤. WebsocketChannel.set_manager() 로 wire.
- 룰 메모리 캐시 (RuleEngine) 는 _engine() 으로 lazy load. POST/DELETE 후 reload.
- /test 는 cooldown 무시 + alert_events INSERT 함 + payload = dry-run 표기.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.alerts import (
    DEFAULT_CHANNELS,
    Rule,
    RuleEngine,
    WebsocketChannel,
)
from app.core.cache import redis_cache
from app.database import AsyncSessionLocal, get_db
from app.schemas.alert_presets import DEFAULT_PRESETS, PRESETS_BY_KEY

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/alerts", tags=["alerts"])


# ─────────────────────────────────────────────────────────────
# Connection manager — alerts 전용 (websocket.py 의 voc 채널과 분리)
# ─────────────────────────────────────────────────────────────
class AlertConnectionManager:
    """단일 프로세스 in-memory broadcast.

    분산환경에서는 Redis pubsub 으로 교체. 지금은 단일 worker.
    """

    def __init__(self) -> None:
        self.active: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        data = json.dumps(message, ensure_ascii=False, default=str)
        dead: List[WebSocket] = []
        # snapshot — 락 안에서 send 하면 데드락 위험
        async with self._lock:
            snapshot = list(self.active)
        for ws in snapshot:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self.active:
                        self.active.remove(ws)


manager = AlertConnectionManager()
# WebsocketChannel 에 manager wire — Celery worker 와 API 가 같은 DEFAULT_CHANNELS 를
# 공유하지만 manager 는 API 프로세스에만 존재 (Celery 는 ws dry-run).
DEFAULT_CHANNELS["websocket"].set_manager(manager)


# ─────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────
class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    metric_path: str = Field(..., min_length=1, max_length=128)
    op: str = Field(..., pattern=r"^(>|<|>=|<=|==)$")
    threshold: float
    severity: str = Field(default="warning", pattern=r"^(critical|warning|info)$")
    cooldown_sec: int = Field(default=900, ge=10, le=86400)
    description: Optional[str] = None
    is_active: bool = True


class RuleOut(RuleIn):
    id: int
    created_at: Optional[str] = None


class RulePatch(BaseModel):
    """부분 업데이트. 누락 필드는 변경 없음.

    허용 필드: is_active, threshold, cooldown_sec, severity, description.
    name/metric_path/op 은 의미가 강해 별도 룰을 만드는 편이 안전 — 여기선 제외.
    """
    is_active: Optional[bool] = None
    threshold: Optional[float] = None
    cooldown_sec: Optional[int] = Field(default=None, ge=10, le=86400)
    severity: Optional[str] = Field(default=None, pattern=r"^(critical|warning|info)$")
    description: Optional[str] = None


class ChannelStatus(BaseModel):
    slack: Dict[str, Any]
    websocket: Dict[str, Any]


class AlertEventOut(BaseModel):
    id: int
    rule_id: int
    rule_name: str
    fired_at: str
    severity: str
    value: float
    threshold: float
    payload: Dict[str, Any]
    dispatched_channels: List[str]


class TestResult(BaseModel):
    evaluated: int
    fired: int
    metrics: Dict[str, float]
    events: List[AlertEventOut]


class PresetOut(BaseModel):
    """프리셋 카탈로그 항목 — UI 카드 렌더용."""
    key: str
    name: str
    metric_path: str
    op: str
    threshold: float
    severity: str
    cooldown_sec: int
    description: Optional[str] = None


class PresetApplyIn(BaseModel):
    keys: List[str] = Field(..., min_length=1, max_length=len(DEFAULT_PRESETS))


class PresetApplyOut(BaseModel):
    requested: int
    created: int
    skipped: List[str]
    created_rules: List[RuleOut]


# ─────────────────────────────────────────────────────────────
# 메트릭 수집 — RuleEngine 이 평가할 metric_path → value
# ─────────────────────────────────────────────────────────────
# alerts 전용 new-term-spike 카운트 SQL.
#  - InsightsService.new_terms 는 ranked LIMIT 50 출력을 위해 90d anti-join 을
#    실행해 cold ~22s. alerts 룰은 "spike 개수" 만 필요하므로 단일 CTE 로 충분.
#  - 윈도우: 최근 7d (recent) / 직전 30d (history). 90d 까지 보지 않아도
#    "신조어" 판정에 영향 없음 (실제 트래픽은 최근 60일 내 집중).
_NEW_TERM_SPIKE_SQL = """
    WITH recent AS (
        SELECT k.keyword, k.lang, COUNT(*) AS c
        FROM voc_keywords k
        JOIN voc_records v ON v.id = k.voc_id
        WHERE v.published_at >= now() - interval '7 days'
          AND v.published_at <= now()
        GROUP BY k.keyword, k.lang
        HAVING COUNT(*) >= 20
    )
    SELECT count(*) AS n
    FROM recent r
    WHERE NOT EXISTS (
        SELECT 1
        FROM voc_keywords k2
        JOIN voc_records v2 ON v2.id = k2.voc_id
        WHERE k2.keyword = r.keyword
          AND COALESCE(k2.lang, '') = COALESCE(r.lang, '')
          AND v2.published_at <  now() - interval '7 days'
          AND v2.published_at >= now() - interval '37 days'
    )
"""


# alerts 전용 community 메트릭 SQL — CommunityService.anomalies() 우회.
#  - anomalies() 는 4 종 룰을 모두 계산해 List[AnomalyEntry] 를 만든 뒤 Python 에서
#    extreme_negative_7d 만 필터링한다. alerts 룰엔 *집계값 두 개* 만 필요해 over-fetch.
#  - 동등성 보장: anomalies() 는 status=='dead' 면 continue 로 다른 룰 건너뜀.
#    → SQL 도 status != 'dead' 인 행만 카운트.
#  - negative_rate proxy 매핑: est = max(0.0, min(1.0, -sent_avg_7d + 0.3))
#    Python 의 round(est, 4) 와 동일하게 round(·, 4) 적용.
_COMMUNITY_METRICS_SQL = """
    WITH ph AS (
        SELECT sent_avg_7d
        FROM platform_health
        WHERE sent_avg_7d IS NOT NULL
          AND status <> 'dead'
    ),
    extreme AS (
        SELECT sent_avg_7d FROM ph WHERE sent_avg_7d <= -0.3
    )
    SELECT
        coalesce((SELECT count(*) FROM extreme), 0)                                                       AS extreme_neg_count,
        coalesce((SELECT round(max(GREATEST(0.0, LEAST(1.0, -sent_avg_7d + 0.3)))::numeric, 4) FROM extreme), 0.0) AS neg_rate_max,
        coalesce(round((count(*) FILTER (WHERE sent_avg_7d < 0.0))::numeric
                       / NULLIF(count(*),0), 4), 0.0)                                                     AS neg_plats_pct
    FROM ph
"""


async def _community_metrics(db: AsyncSession) -> Dict[str, float]:
    """platform_health 기반 community.* 두 metric — 인라인 SQL 한 번에.

    P4 E4: CommunityService.anomalies() 호출 우회. 1 query, 동일 결과 보장.
    """
    out: Dict[str, float] = {
        "community.extreme_negative_count": 0.0,
        "community.negative_rate_max": 0.0,
        "community.platforms_negative_pct": 0.0,
    }
    try:
        row = (await db.execute(text(_COMMUNITY_METRICS_SQL))).first()
    except Exception as exc:  # noqa: BLE001
        logger.exception("[alerts.collect] community metrics 실패: %s", exc)
        return out
    if row is None:
        return out
    out["community.extreme_negative_count"] = float(int(row.extreme_neg_count or 0))
    out["community.negative_rate_max"] = float(row.neg_rate_max or 0.0)
    out["community.platforms_negative_pct"] = float(row.neg_plats_pct or 0.0)
    return out


async def _new_term_spike_count(db: AsyncSession) -> Dict[str, float]:
    """voc_keywords 직접 조회 — InsightsService.new_terms 우회."""
    try:
        row = (await db.execute(text(_NEW_TERM_SPIKE_SQL))).first()
        n = float(int(row.n) if row else 0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[alerts.collect] new_term_spike 실패: %s", exc)
        n = 0.0
    return {"insights.new_term_spike_count": n}


@redis_cache(ttl_seconds=30, key_prefix='alerts:')
async def collect_metrics(db: AsyncSession) -> Dict[str, float]:
    """현 시점 모든 metric_path 값을 한 번에 계산.

    metric_path 정의 (alembic 0005 seed 와 일치):
      community.extreme_negative_count : platform_health 7d 평균 감성 <= -0.3 플랫폼 수
      community.negative_rate_max      : 위 플랫폼들의 부정 비율 추정치 최대
      insights.new_term_spike_count    : 최근 7d 처음 등장 + 누적 >= 20 인 키워드 개수

    note: 동일 AsyncSession 이라 gather 가 직렬 실행되지만 트랜잭션 비용은 없고,
    향후 세션 분리 시 즉시 병렬화 가능한 구조로 유지.
    """
    community, spike = await asyncio.gather(
        _community_metrics(db),
        _new_term_spike_count(db),
    )
    return {**community, **spike}


# ─────────────────────────────────────────────────────────────
# Rule loader
# ─────────────────────────────────────────────────────────────
async def load_rules_from_db(db: AsyncSession) -> List[Rule]:
    rows = (
        await db.execute(
            text(
                """
                SELECT id, name, metric_path, op, threshold, severity,
                       cooldown_sec, description, is_active
                FROM alert_rules
                WHERE is_active = TRUE
                ORDER BY id
                """
            )
        )
    ).all()
    return [
        Rule(
            id=r.id,
            name=r.name,
            metric_path=r.metric_path,
            op=r.op,
            threshold=float(r.threshold),
            severity=r.severity,
            cooldown_sec=int(r.cooldown_sec),
            description=r.description,
            is_active=bool(r.is_active),
        )
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────
# Endpoints — REST
# ─────────────────────────────────────────────────────────────
@router.get("/rules", response_model=List[RuleOut])
async def list_rules(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            text(
                """
                SELECT id, name, metric_path, op, threshold, severity,
                       cooldown_sec, description, is_active, created_at
                FROM alert_rules
                ORDER BY id
                """
            )
        )
    ).all()
    return [
        RuleOut(
            id=r.id,
            name=r.name,
            metric_path=r.metric_path,
            op=r.op,
            threshold=float(r.threshold),
            severity=r.severity,
            cooldown_sec=int(r.cooldown_sec),
            description=r.description,
            is_active=bool(r.is_active),
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.post("/rules", response_model=RuleOut)
async def create_rule(rule: RuleIn, db: AsyncSession = Depends(get_db)):
    # 이름 중복 시 409
    dup = (
        await db.execute(
            text("SELECT id FROM alert_rules WHERE name = :n"), {"n": rule.name}
        )
    ).first()
    if dup:
        raise HTTPException(status_code=409, detail=f"rule name '{rule.name}' 이미 존재")

    res = await db.execute(
        text(
            """
            INSERT INTO alert_rules
                (name, metric_path, op, threshold, severity, cooldown_sec, description, is_active)
            VALUES
                (:n, :m, :o, :t, :s, :c, :d, :a)
            RETURNING id, created_at
            """
        ),
        {
            "n": rule.name, "m": rule.metric_path, "o": rule.op, "t": rule.threshold,
            "s": rule.severity, "c": rule.cooldown_sec, "d": rule.description,
            "a": rule.is_active,
        },
    )
    row = res.first()
    return RuleOut(
        id=row.id,
        created_at=row.created_at.isoformat() if row.created_at else None,
        **rule.model_dump(),
    )


@router.patch("/rules/{rule_id}", response_model=RuleOut)
async def patch_rule(
    rule_id: int, patch: RulePatch, db: AsyncSession = Depends(get_db)
):
    """룰 부분 업데이트 — is_active 토글, threshold/cooldown/severity/description 수정.

    name/metric_path/op 은 의도적으로 제외. 더 큰 변경은 새 룰 생성으로.
    """
    updates: Dict[str, Any] = {
        k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None
    }
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates.keys())
    params = {**updates, "i": rule_id}
    res = await db.execute(
        text(
            f"""
            UPDATE alert_rules SET {set_clause}
            WHERE id = :i
            RETURNING id, name, metric_path, op, threshold, severity,
                      cooldown_sec, description, is_active, created_at
            """
        ),
        params,
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=404, detail="rule not found")
    return RuleOut(
        id=row.id,
        name=row.name,
        metric_path=row.metric_path,
        op=row.op,
        threshold=float(row.threshold),
        severity=row.severity,
        cooldown_sec=int(row.cooldown_sec),
        description=row.description,
        is_active=bool(row.is_active),
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        text("DELETE FROM alert_rules WHERE id = :i"), {"i": rule_id}
    )
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="rule not found")
    return None


@router.get("/presets", response_model=List[PresetOut])
async def list_presets():
    """알림 룰 프리셋 5종 — DB 비의존 정적 카탈로그.

    UI 의 PresetPicker 가 카드 5개 렌더 시 호출. apply 호출 시 같은 keys 사용.
    """
    return [PresetOut(**p) for p in DEFAULT_PRESETS]


@router.post("/presets/apply", response_model=PresetApplyOut)
async def apply_presets(body: PresetApplyIn, db: AsyncSession = Depends(get_db)):
    """선택한 프리셋들을 alert_rules 로 일괄 INSERT.

    - 알 수 없는 key → skipped 에 "{key}:unknown" 으로 기록 (실패하지 않음)
    - 동일 이름 룰이 이미 존재 → skipped 에 "{key}:exists"
    - 정상 생성 → created_rules 에 RuleOut 반환

    트랜잭션: get_db 가 1 호출당 1 세션. INSERT 별 commit 은 세션 종료 시 자동.
    """
    requested = list(body.keys)
    skipped: List[str] = []
    created_rules: List[RuleOut] = []

    # 이미 존재하는 룰 이름 미리 조회 (1쿼리)
    target_presets = [PRESETS_BY_KEY[k] for k in requested if k in PRESETS_BY_KEY]
    for k in requested:
        if k not in PRESETS_BY_KEY:
            skipped.append(f"{k}:unknown")

    if target_presets:
        names = [p["name"] for p in target_presets]
        existing = {
            r.name
            for r in (
                await db.execute(
                    text("SELECT name FROM alert_rules WHERE name = ANY(:ns)"),
                    {"ns": names},
                )
            ).all()
        }
    else:
        existing = set()

    for p in target_presets:
        if p["name"] in existing:
            skipped.append(f"{p['key']}:exists")
            continue
        row = (
            await db.execute(
                text(
                    """
                    INSERT INTO alert_rules
                        (name, metric_path, op, threshold, severity,
                         cooldown_sec, description, is_active)
                    VALUES
                        (:n, :m, :o, :t, :s, :c, :d, TRUE)
                    RETURNING id, created_at
                    """
                ),
                {
                    "n": p["name"], "m": p["metric_path"], "o": p["op"],
                    "t": p["threshold"], "s": p["severity"],
                    "c": p["cooldown_sec"], "d": p.get("description"),
                },
            )
        ).first()
        created_rules.append(
            RuleOut(
                id=row.id,
                name=p["name"],
                metric_path=p["metric_path"],
                op=p["op"],
                threshold=float(p["threshold"]),
                severity=p["severity"],
                cooldown_sec=int(p["cooldown_sec"]),
                description=p.get("description"),
                is_active=True,
                created_at=row.created_at.isoformat() if row.created_at else None,
            )
        )

    return PresetApplyOut(
        requested=len(requested),
        created=len(created_rules),
        skipped=skipped,
        created_rules=created_rules,
    )


@router.get("/channels", response_model=ChannelStatus)
async def channels_status():
    """채널 상태 — Slack enabled/dry-run/last_dispatch_at + WebSocket 활성 연결 수.

    DB 비의존. settings 와 manager 인메모리 상태만 본다.
    last_dispatch_at: SlackChannel.send() 가 1회라도 호출되면 ISO8601 로 채워짐.
    """
    slack = DEFAULT_CHANNELS.get("slack")
    slack_enabled = bool(getattr(slack, "webhook_url", "") if slack else False)
    return ChannelStatus(
        slack={
            "enabled": slack_enabled,
            "dry_run": not slack_enabled,
            "channel": getattr(slack, "channel", "") if slack else "",
            "last_dispatch_at": getattr(slack, "last_dispatch_at", None) if slack else None,
        },
        websocket={
            "connections": len(manager.active),
        },
    )


@router.post("/channels/slack/test")
async def channels_slack_test(request: Request):
    """Slack 채널 1회 테스트 송신 — localhost only.

    실 webhook 이 설정돼 있으면 block kit 메시지 1건 POST,
    아니면 dry-run 로그만. /alerts/channels 의 last_dispatch_at 이 갱신된다.

    보안: 127.0.0.1 / ::1 / localhost 외부 호출은 403.
    """
    client_host = (request.client.host if request.client else "") or ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="localhost only")

    slack = DEFAULT_CHANNELS.get("slack")
    if slack is None:
        raise HTTPException(status_code=500, detail="slack channel not registered")

    sample = {
        "rule": "channels_slack_test",
        "metric": "channels.slack.test",
        "op": ">=",
        "threshold": 1,
        "value": 1,
        "severity": "info",
        "description": "/alerts/channels/slack/test 수동 테스트 송신",
        "fired_at": datetime.now(timezone.utc).isoformat(),
    }
    ok = await slack.send(sample)
    return {
        "ok": bool(ok),
        "enabled": bool(getattr(slack, "webhook_url", "")),
        "dry_run": not bool(getattr(slack, "webhook_url", "")),
        "last_dispatch_at": getattr(slack, "last_dispatch_at", None),
    }


@router.post("/test", response_model=TestResult)
async def fire_test(
    db: AsyncSession = Depends(get_db),
    respect_cooldown: bool = Query(False, description="True 면 cooldown 적용 (운영 호출). 기본 False 는 강제 발화."),
):
    """모든 활성 룰을 라이브 metric 으로 즉시 평가 + dispatch.

    respect_cooldown=False (기본): cooldown 무시, 강제 발화 (UI 의 '지금 평가' 버튼용).
    respect_cooldown=True: DB 의 마지막 fired_at + cooldown_sec 확인 후 skip (Celery beat 운영용).
    """
    rules = await load_rules_from_db(db)
    metrics = await collect_metrics(db)
    eng = RuleEngine(rules)
    fired = eng.evaluate(metrics, ignore_cooldown=True)

    # respect_cooldown=True 일 때 DB 의 마지막 fired_at 기반으로 skip
    if respect_cooldown and fired:
        skipped: List = []
        kept: List = []
        for fe in fired:
            last = await db.execute(
                text(
                    "SELECT EXTRACT(EPOCH FROM (now() - max(fired_at))) AS sec FROM alert_events WHERE rule_id=:rid"
                ),
                {"rid": fe.rule.id},
            )
            row = last.first()
            sec = float(row.sec) if row and row.sec is not None else float("inf")
            if sec < float(fe.rule.cooldown_sec):
                skipped.append(fe.rule.id)
            else:
                kept.append(fe)
        if skipped:
            logger.info("[alerts.test] cooldown skip rules=%s", skipped)
        fired = kept

    events: List[AlertEventOut] = []
    for fe in fired:
        # 1) 채널 dispatch — 결과를 dispatched_channels 라벨로 정확 반영
        # 라벨 컨벤션:
        #   slack            : 실 webhook 송신 성공
        #   slack:dry        : SLACK_WEBHOOK_URL 미설정 (dry-run)
        #   slack:fail       : send() 가 False 반환 (현재는 폴백으로 거의 없음)
        #   websocket        : manager 가 wire 되어 broadcast 시도
        #   websocket:dry    : manager 미주입 (Celery 등)
        dispatched: List[str] = []
        for ch_name, ch in DEFAULT_CHANNELS.items():
            dry = bool(getattr(ch, "dry_run", False))
            try:
                ok = await ch.send(fe.payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[alerts.test] channel=%s send 실패: %s", ch_name, exc)
                dispatched.append(f"{ch_name}:fail")
                continue
            if dry:
                dispatched.append(f"{ch_name}:dry")
            elif ok:
                dispatched.append(ch_name)
            else:
                dispatched.append(f"{ch_name}:fail")

        # 2) DB INSERT — dispatched_channels 실제 결과 반영
        row = (
            await db.execute(
                text(
                    """
                    INSERT INTO alert_events
                        (rule_id, severity, value, threshold, payload, dispatched_channels)
                    VALUES
                        (:rid, :sev, :v, :th, CAST(:pl AS JSONB), :ch)
                    RETURNING id, fired_at
                    """
                ),
                {
                    "rid": fe.rule.id,
                    "sev": fe.rule.severity,
                    "v": fe.value,
                    "th": fe.rule.threshold,
                    "pl": json.dumps(fe.payload, ensure_ascii=False, default=str),
                    "ch": dispatched,
                },
            )
        ).first()
        events.append(
            AlertEventOut(
                id=row.id,
                rule_id=fe.rule.id,
                rule_name=fe.rule.name,
                fired_at=row.fired_at.isoformat() if row.fired_at else "",
                severity=fe.rule.severity,
                value=fe.value,
                threshold=fe.rule.threshold,
                payload=fe.payload,
                dispatched_channels=dispatched,
            )
        )

    return TestResult(
        evaluated=len(rules),
        fired=len(fired),
        metrics=metrics,
        events=events,
    )


@router.get("/recent", response_model=List[AlertEventOut])
async def recent_events(
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT e.id, e.rule_id, r.name AS rule_name, e.fired_at,
                       e.severity, e.value, e.threshold, e.payload,
                       e.dispatched_channels
                FROM alert_events e
                LEFT JOIN alert_rules r ON r.id = e.rule_id
                ORDER BY e.fired_at DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        )
    ).all()
    return [
        AlertEventOut(
            id=r.id,
            rule_id=r.rule_id,
            rule_name=r.rule_name or "?",
            fired_at=r.fired_at.isoformat() if r.fired_at else "",
            severity=r.severity,
            value=float(r.value),
            threshold=float(r.threshold),
            payload=r.payload or {},
            dispatched_channels=list(r.dispatched_channels or []),
        )
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────
# WebSocket
# ─────────────────────────────────────────────────────────────
@router.websocket("/ws")
async def alerts_ws(ws: WebSocket):
    """실시간 알림 스트림.

    서버 → 클라이언트: {"type":"alert", "data": {...}}  |  {"type":"ping"}
    클라이언트 → 서버: text 무엇이든 OK (받지 않음, 연결 유지용)

    ping_interval: settings.WS_PING_INTERVAL_SEC (기본 30s)
    """
    await manager.connect(ws)
    ping_interval = int(getattr(settings, "WS_PING_INTERVAL_SEC", 30))
    try:
        await ws.send_text(json.dumps({"type": "hello", "data": {"ping_interval": ping_interval}}))
        while True:
            try:
                # 클라이언트 메시지 또는 ping 타임아웃 둘 중 먼저 발생한 것.
                await asyncio.wait_for(ws.receive_text(), timeout=ping_interval)
            except asyncio.TimeoutError:
                await ws.send_text(
                    json.dumps({"type": "ping", "data": {"ts": datetime.now(timezone.utc).isoformat()}})
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alerts.ws] 예외 종료: %s", exc)
    finally:
        await manager.disconnect(ws)


__all__ = ["router", "manager", "collect_metrics", "load_rules_from_db"]
