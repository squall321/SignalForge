# 바뀐 것 목록(공통 계약 R1) — alert_events 를 id 커서로 읽어 signalforge.<대상>.<일> 이벤트로 낸다
"""게이트웨이 이벤트 허브는 앱에 '지난번 뒤로 바뀐 것' 을 묻고 io.modelcontextprotocol/events 로 다시 낸다.

SignalForge 에서 새로 생기는 일은 모두 alert_events 한 표에 쌓인다 — 결함 급등 탐지기 · 수집 상태 점검 · 운영 감시 ·
운영 상태 위반 · 백업 검증(payload.type 으로 가른다)과 룰 엔진의 지표 규칙(payload 에 type 이 없다).
커서는 id 다. 덤프로 받는 서버는 병합이 원천의 fired_at 을 그대로 두고 새 id 를 붙여, fired_at 순서와 id 순서가 다르다 —
id 로 이어 읽어야 빠짐 · 겹침이 없다.
"""
import json
import os
from typing import Optional

from db import get_db_session
from sqlalchemy import text

EVENT_NAMES = {
    "defect_anomaly": "signalforge.defect.spiked",
    "collection_health": "signalforge.collection.degraded",
    "operations_monitor": "signalforge.operations.alerted",
    "ops_status_violation": "signalforge.ops_status.violated",
    "backup_fail": "signalforge.backup.failed",
}
RULE_EVENT = "signalforge.alert.fired"


def _hidden_rules() -> list:
    """REST /alerts 와 같은 숨김 규칙(ALERT_HIDDEN_RULES, 쉼표 목록) — 이 서버가 보이지 않기로 한 규칙은 이벤트로도 내지 않는다."""
    return [n.strip() for n in os.getenv("ALERT_HIDDEN_RULES", "").split(",") if n.strip()]


def _summary(rule: str, p: dict) -> str:
    for key in ("reason", "description"):
        if isinstance(p.get(key), str) and p[key]:
            return p[key]
    if p.get("label"):
        codes = ", ".join(p.get("codes") or [])
        return f"{p['label']} — {codes}" if codes else p["label"]
    return rule


async def list_changes_tool(since: Optional[int] = None, limit: int = 50,
                            events: Optional[list] = None) -> dict:
    conds = ["r.name <> ALL(CAST(:hidden AS text[]))"]
    params: dict = {"hidden": _hidden_rules()}
    if events:
        parts = []
        types = [t for t, ev in EVENT_NAMES.items() if ev in events]
        if types:
            parts.append("e.payload->>'type' = ANY(CAST(:types AS text[]))")
            params["types"] = types
        if RULE_EVENT in events:
            parts.append("(e.payload->>'type' IS NULL OR e.payload->>'type' <> ALL(CAST(:known AS text[])))")
            params["known"] = list(EVENT_NAMES)
        conds.append("(" + " OR ".join(parts or ["FALSE"]) + ")")
    if since is not None:
        conds.append("e.id > :since")
        params["since"] = since
    where = " AND ".join(conds)
    joins = "FROM alert_events e JOIN alert_rules r ON r.id = e.rule_id"
    # since 가 있으면 그 뒤를 오래된 것부터, 없으면 가장 최근 것을 — 한 건 더 읽어 더 있는지 안다
    order = "e.id ASC" if since is not None else "e.id DESC"
    stmt = text(f"""
        SELECT e.id, r.name AS rule, e.severity, e.fired_at, e.value, e.threshold, e.payload
        {joins} WHERE {where} ORDER BY {order} LIMIT :lim
    """)
    async with get_db_session() as db:
        rows = [dict(r) for r in (await db.execute(stmt, {**params, "lim": limit + 1})).mappings().all()]
        total = int((await db.execute(text(f"SELECT count(*) {joins} WHERE {where}"), params)).scalar() or 0)
    more = len(rows) > limit
    rows = rows[:limit]
    if since is None:
        rows.reverse()                    # 가장 최근 limit 건을 오래된 것부터 — 마지막 id 가 다음 커서다
    out = []
    for r in rows:
        p = r["payload"] or {}
        if isinstance(p, str):
            p = json.loads(p)
        out.append({
            "id": r["id"],
            "event": EVENT_NAMES.get(p.get("type"), RULE_EVENT),
            "rule": r["rule"],
            "severity": r["severity"],
            "at": r["fired_at"],
            "value": r["value"],
            "threshold": r["threshold"],
            "summary": _summary(r["rule"], p),
            "detail": p,
        })
    return {"total": total, "truncated": more,
            "next_cursor": out[-1]["id"] if out else since,
            "events": out}
