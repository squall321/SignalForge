"""
크롤링 품질 모니터링 (Track G)
================================
사이트 / NLP 파이프라인 건강도를 점검하고 Markdown 리포트를 생성한다.

지표:
1. 사이트별 last_collected_at, 24h rows, 7d 일평균
2. status: active / idle / dead  (24h>0 → active, 7d>0 → idle, else dead)
3. 번역 실패율: content_translated IS NULL OR == content_original (외국어인데 동일)
4. 제품 태깅률: product_id IS NOT NULL 비율
5. 카테고리 빈 비율: categories IS NULL OR cardinality(categories)=0
6. Celery beat 스케줄에 정의되어 있으나 7일간 1건도 안 들어온 사이트 (= dispatch 의심)

출력:  reports/health_YYYY-MM-DD.md (UTC 날짜)

알림:
- dead 사이트 또는 번역실패율 > 50% 또는 태깅률 < 30% → ALERT_WEBHOOK_URL 로 POST.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# --- 경로 / 환경 ---------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]     # /home/koopark/claude/SignalForge
REPORTS_DIR = ROOT_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# DB 접속 — .env 또는 기본값. asyncpg 는 postgres:// 또는 postgresql:// 둘 다 인식.
DB_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://signalforge:signalforge_pass@127.0.0.1:5434/signalforge",
)
# SQLAlchemy 비동기 prefix 가 들어왔으면 asyncpg 가 받아들이는 형식으로 정리
if DB_DSN.startswith("postgresql+asyncpg://"):
    DB_DSN = "postgresql://" + DB_DSN.split("://", 1)[1]

# Celery beat schedule 로드용
_CRAWLER_DIR = ROOT_DIR / "crawler"


# --- 결과 자료구조 -------------------------------------------------------
@dataclass
class PlatformHealth:
    code: str
    name: str
    is_active: bool
    last_collected_at: datetime | None
    rows_24h: int
    rows_7d: int
    avg_per_day_7d: float
    status: str                # active / idle / dead
    in_beat_schedule: bool
    note: str = ""


@dataclass
class HealthReport:
    generated_at: datetime
    total_rows: int
    rows_24h_total: int
    platforms: list[PlatformHealth] = field(default_factory=list)
    translation_total: int = 0
    translation_failed: int = 0
    translation_fail_rate: float = 0.0
    product_total: int = 0
    product_tagged: int = 0
    product_tag_rate: float = 0.0
    category_total: int = 0
    category_empty: int = 0
    category_empty_rate: float = 0.0
    critical_alerts: list[str] = field(default_factory=list)

    @property
    def dead_platforms(self) -> list[PlatformHealth]:
        return [p for p in self.platforms if p.status == "dead" and p.in_beat_schedule]

    @property
    def idle_platforms(self) -> list[PlatformHealth]:
        return [p for p in self.platforms if p.status == "idle"]


# --- Beat 스케줄 로드 (정의된 플랫폼 set) --------------------------------
def load_beat_platforms() -> set[str]:
    """celery_app.beat_schedule 에서 platform code 추출.

    스케줄 args = (platform_code, ...)  → set 반환.
    import 실패 시 빈 set (= 모든 플랫폼이 in_beat=False 로 표시).
    """
    if str(_CRAWLER_DIR) not in sys.path:
        sys.path.insert(0, str(_CRAWLER_DIR))
    try:
        from celery_app import app  # type: ignore
        codes: set[str] = set()
        for _, spec in (app.conf.beat_schedule or {}).items():
            args = spec.get("args") or ()
            if args and isinstance(args[0], str):
                codes.add(args[0])
        return codes
    except Exception as e:
        logger.warning(f"beat_schedule 로드 실패: {e}")
        return set()


# --- DB 쿼리 (asyncpg, 동기 래퍼) ----------------------------------------
async def _collect_platform_stats_async(now: datetime) -> list[PlatformHealth]:
    sql = """
    SELECT
        p.code,
        p.name,
        p.is_active,
        MAX(v.collected_at)                              AS last_collected,
        COUNT(*) FILTER (WHERE v.collected_at >= $1)     AS rows_24h,
        COUNT(*) FILTER (WHERE v.collected_at >= $2)     AS rows_7d
    FROM platforms p
    LEFT JOIN voc_records v ON v.platform_id = p.id
    GROUP BY p.id, p.code, p.name, p.is_active
    ORDER BY p.code
    """
    t_24h = now - timedelta(hours=24)
    t_7d = now - timedelta(days=7)
    beat_codes = load_beat_platforms()
    results: list[PlatformHealth] = []
    conn = await asyncpg.connect(DB_DSN)
    try:
        rows = await conn.fetch(sql, t_24h, t_7d)
    finally:
        await conn.close()
    for row in rows:
        rows_24h = int(row["rows_24h"] or 0)
        rows_7d = int(row["rows_7d"] or 0)
        if rows_24h > 0:
            status = "active"
        elif rows_7d > 0:
            status = "idle"
        else:
            status = "dead"
        results.append(PlatformHealth(
            code=row["code"],
            name=row["name"],
            is_active=bool(row["is_active"]),
            last_collected_at=row["last_collected"],
            rows_24h=rows_24h,
            rows_7d=rows_7d,
            avg_per_day_7d=round(rows_7d / 7.0, 2),
            status=status,
            in_beat_schedule=row["code"] in beat_codes,
        ))
    return results


async def _collect_nlp_quality_async(now: datetime) -> dict[str, Any]:
    t_7d = now - timedelta(days=7)
    sql_total = """
    SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (
            WHERE content_translated IS NULL
               OR (language_detected IS NOT NULL
                   AND language_detected NOT IN ('en','')
                   AND content_translated = content_original)
        ) AS translation_failed,
        COUNT(*) FILTER (WHERE product_id IS NOT NULL) AS product_tagged,
        COUNT(*) FILTER (WHERE categories IS NULL OR cardinality(categories) = 0) AS category_empty
    FROM voc_records
    WHERE collected_at >= $1
    """
    conn = await asyncpg.connect(DB_DSN)
    try:
        r = await conn.fetchrow(sql_total, t_7d)
    finally:
        await conn.close()
    total = int(r["total"] or 0)
    fail = int(r["translation_failed"] or 0)
    tagged = int(r["product_tagged"] or 0)
    cat_empty = int(r["category_empty"] or 0)
    return {
        "total": total,
        "translation_failed": fail,
        "translation_fail_rate": (fail / total) if total else 0.0,
        "product_tagged": tagged,
        "product_tag_rate": (tagged / total) if total else 0.0,
        "category_empty": cat_empty,
        "category_empty_rate": (cat_empty / total) if total else 0.0,
    }


async def _collect_totals_async() -> tuple[int, int]:
    conn = await asyncpg.connect(DB_DSN)
    try:
        total = int(await conn.fetchval("SELECT COUNT(*) FROM voc_records"))
        last_24h = int(await conn.fetchval(
            "SELECT COUNT(*) FROM voc_records WHERE collected_at >= now() - interval '24 hours'"
        ))
    finally:
        await conn.close()
    return total, last_24h


def _run_async(coro):
    """동기 컨텍스트에서 안전하게 coroutine 실행 (Celery worker 안에서도 OK)."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        # 이미 실행 중인 루프가 있으면 새 스레드에서 별도 루프로 실행
        import threading
        result: list = []
        err: list = []
        def runner():
            try:
                result.append(asyncio.run(coro))
            except Exception as e:
                err.append(e)
        t = threading.Thread(target=runner)
        t.start()
        t.join()
        if err:
            raise err[0]
        return result[0]
    return asyncio.run(coro)


def collect_platform_stats(now: datetime) -> list[PlatformHealth]:
    """플랫폼별 last/24h/7d 집계 (동기 wrapper)."""
    return _run_async(_collect_platform_stats_async(now))


def collect_nlp_quality(now: datetime) -> dict[str, Any]:
    """최근 7일 NLP 품질 지표 (동기 wrapper)."""
    return _run_async(_collect_nlp_quality_async(now))


def collect_totals() -> tuple[int, int]:
    """전체 voc rows + 최근 24h rows (동기 wrapper)."""
    return _run_async(_collect_totals_async())


# --- 리포트 빌드 ---------------------------------------------------------
def build_report() -> HealthReport:
    now = datetime.now(timezone.utc)
    total, last_24h = collect_totals()
    platforms = collect_platform_stats(now)
    nlp = collect_nlp_quality(now)

    rep = HealthReport(
        generated_at=now,
        total_rows=total,
        rows_24h_total=last_24h,
        platforms=platforms,
        translation_total=nlp["total"],
        translation_failed=nlp["translation_failed"],
        translation_fail_rate=nlp["translation_fail_rate"],
        product_total=nlp["total"],
        product_tagged=nlp["product_tagged"],
        product_tag_rate=nlp["product_tag_rate"],
        category_total=nlp["total"],
        category_empty=nlp["category_empty"],
        category_empty_rate=nlp["category_empty_rate"],
    )

    # 임계치 (env 로 조정 가능)
    DEAD_ALERT_MIN = int(os.getenv("HEALTH_DEAD_ALERT_MIN", "1"))
    TRANS_FAIL_LIMIT = float(os.getenv("HEALTH_TRANS_FAIL_LIMIT", "0.5"))
    TAG_RATE_FLOOR = float(os.getenv("HEALTH_TAG_RATE_FLOOR", "0.3"))

    dead = rep.dead_platforms
    if len(dead) >= DEAD_ALERT_MIN:
        codes = ", ".join(p.code for p in dead)
        rep.critical_alerts.append(
            f"[DEAD] Beat 스케줄에 정의되었으나 7일간 0건 수집된 사이트 {len(dead)}개: {codes}"
        )
    if rep.translation_total > 0 and rep.translation_fail_rate > TRANS_FAIL_LIMIT:
        rep.critical_alerts.append(
            f"[TRANSLATION] 7일 번역 실패율 {rep.translation_fail_rate:.1%} "
            f"(>{TRANS_FAIL_LIMIT:.0%})"
        )
    if rep.product_total > 0 and rep.product_tag_rate < TAG_RATE_FLOOR:
        rep.critical_alerts.append(
            f"[TAGGING] 7일 제품 태깅률 {rep.product_tag_rate:.1%} "
            f"(<{TAG_RATE_FLOOR:.0%})"
        )
    return rep


# --- Markdown 렌더링 -----------------------------------------------------
def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_markdown(rep: HealthReport) -> str:
    lines: list[str] = []
    lines.append(f"# SignalForge 품질 모니터링 리포트")
    lines.append("")
    lines.append(f"- 생성: {rep.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"- voc_records 총행수: **{rep.total_rows:,}**")
    lines.append(f"- 최근 24h 신규: **{rep.rows_24h_total:,}**")
    lines.append(f"- 등록 플랫폼: {len(rep.platforms)}")
    lines.append("")

    # 1) Critical
    lines.append("## 1. Critical Alerts")
    if rep.critical_alerts:
        for a in rep.critical_alerts:
            lines.append(f"- {a}")
    else:
        lines.append("- (없음)")
    lines.append("")

    # 2) 플랫폼 요약
    actives = [p for p in rep.platforms if p.status == "active"]
    idles = [p for p in rep.platforms if p.status == "idle"]
    deads = [p for p in rep.platforms if p.status == "dead"]
    lines.append("## 2. 플랫폼 Status 요약")
    lines.append("")
    lines.append(f"- active: **{len(actives)}**   idle: **{len(idles)}**   dead: **{len(deads)}**")
    lines.append("")

    # 3) 사이트별 표
    lines.append("## 3. 사이트별 수집 현황")
    lines.append("")
    lines.append("| code | status | beat | 24h | 7d | 7d/일 | last_collected |")
    lines.append("|------|--------|------|-----|----|-------|----------------|")
    # 정렬: status (dead first) → rows_24h desc → code
    status_order = {"dead": 0, "idle": 1, "active": 2}
    for p in sorted(rep.platforms, key=lambda x: (status_order[x.status], -x.rows_24h, x.code)):
        beat_mark = "Y" if p.in_beat_schedule else "-"
        status_tag = {"dead": "DEAD", "idle": "idle", "active": "ok"}[p.status]
        lines.append(
            f"| {p.code} | {status_tag} | {beat_mark} | "
            f"{p.rows_24h} | {p.rows_7d} | {p.avg_per_day_7d} | "
            f"{_fmt_dt(p.last_collected_at)} |"
        )
    lines.append("")

    # 4) NLP 품질
    lines.append("## 4. NLP 품질 (최근 7일)")
    lines.append("")
    lines.append(f"- 표본: {rep.translation_total:,} 건")
    if rep.translation_total > 0:
        lines.append(
            f"- 번역 실패: {rep.translation_failed:,} "
            f"({rep.translation_fail_rate:.1%}) — translated NULL 또는 외국어인데 원문과 동일"
        )
        lines.append(
            f"- 제품 태깅: {rep.product_tagged:,} "
            f"({rep.product_tag_rate:.1%})"
        )
        lines.append(
            f"- 카테고리 빈 비율: {rep.category_empty:,} "
            f"({rep.category_empty_rate:.1%})"
        )
    else:
        lines.append("- (7일간 표본 없음)")
    lines.append("")

    # 5) Beat schedule vs 실제 dispatch
    in_beat_dead = [p for p in rep.platforms if p.in_beat_schedule and p.status == "dead"]
    lines.append("## 5. Beat schedule vs 실제 dispatch")
    lines.append("")
    if not in_beat_dead:
        lines.append("- 모든 beat-scheduled 사이트가 최근 7일내 1건 이상 수집됨")
    else:
        lines.append("Beat 에는 있으나 7일간 데이터 0건:")
        for p in in_beat_dead:
            lines.append(
                f"- `{p.code}` (last={_fmt_dt(p.last_collected_at)})"
            )
    lines.append("")

    return "\n".join(lines)


# --- 파일 출력 / 알림 ---------------------------------------------------
def write_report(rep: HealthReport, when: datetime | None = None) -> Path:
    when = when or rep.generated_at
    fname = f"health_{when.astimezone(timezone.utc):%Y-%m-%d}.md"
    out = REPORTS_DIR / fname
    out.write_text(render_markdown(rep), encoding="utf-8")
    return out


def send_alert(title: str, body: str) -> bool:
    """ALERT_WEBHOOK_URL 로 critical 알림 POST.

    Slack-compatible 'text' payload — Slack/Discord/Webhook.site 등에서 동작.
    설정되지 않았으면 stdout 으로 출력하고 True 반환.
    """
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    payload = {"text": f"*{title}*\n```{body}```"}
    if not url:
        logger.info("[ALERT/stdout] %s\n%s", title, body)
        return True
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        logger.warning(f"send_alert 실패: {e}")
        return False


def run_health_check() -> dict[str, Any]:
    """Health check 1회 실행 — 리포트 작성 + 알림 발송."""
    rep = build_report()
    out_path = write_report(rep)
    alerted = False
    if rep.critical_alerts:
        title = f"SignalForge Health Alert ({rep.generated_at:%Y-%m-%d %H:%MZ})"
        body = "\n".join(rep.critical_alerts)
        alerted = send_alert(title, body)
    return {
        "status": "ok",
        "report": str(out_path),
        "total_rows": rep.total_rows,
        "rows_24h": rep.rows_24h_total,
        "platforms_total": len(rep.platforms),
        "platforms_dead": sum(1 for p in rep.platforms if p.status == "dead"),
        "platforms_idle": sum(1 for p in rep.platforms if p.status == "idle"),
        "platforms_active": sum(1 for p in rep.platforms if p.status == "active"),
        "translation_fail_rate": rep.translation_fail_rate,
        "product_tag_rate": rep.product_tag_rate,
        "critical_alerts": rep.critical_alerts,
        "alert_sent": alerted,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run_health_check()
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
