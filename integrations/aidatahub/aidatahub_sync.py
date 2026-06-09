#!/usr/bin/env python3
"""SignalForge → AX Hub (Mobile eXperience AI Data Hub) 동기화 어댑터.

사용:
    python aidatahub_sync.py --mode=push-all --config=config.yml
    python aidatahub_sync.py --mode=push-recent --since=2026-05-28T00:00:00Z --config=config.yml
    python aidatahub_sync.py --help

설계:
    - 외부 의존성 최소화: httpx + pyyaml + (옵션) sqlalchemy / asyncpg
    - 데이터 수집 두 가지 경로:
        1) HTTP — SignalForge 의 `GET /api/v1/products/{code}/voc` 반복 호출
        2) DB direct — config 에 db_url 있으면 PG 직접 query (Celery worker 내부)
    - 매핑: AIDATAHUB_CLIENT_SPEC.md §3 룰 그대로
    - 실패 record 는 ./dead_letter_{timestamp}.json 으로 별도 저장

본 어댑터는 SignalForge backend code 안에서 직접 import 해서 사용해도 됨
(``from integrations.aidatahub.aidatahub_sync import push_recent``).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger("aidatahub_sync")

# ===========================================================================
# 매핑 규약 (AIDATAHUB_CLIENT_SPEC.md §3)
# ===========================================================================
SENTIMENT_TO_SEVERITY = {
    "very_negative": "critical",
    "negative":      "major",
    "neutral":       "info",
    "positive":      "info",
}
SEVERITY_QS = {"critical": 100, "major": 75, "minor": 50, "info": 25}

# SignalForge 의 제품 코드 — 본 어댑터가 반복 순회
DEFAULT_PRODUCT_CODES = ["GS", "GZ", "GA", "GW", "GB", "GR"]


# ===========================================================================
# 변환
# ===========================================================================
def voc_to_record(voc: dict[str, Any]) -> dict[str, Any]:
    """SignalForge VOC dict → AX Hub record dict.

    voc 는 SignalForge ``VocRecordRead`` schema (또는 SQL row dict).
    Pydantic 모델 객체면 ``.model_dump()`` 한 결과를 넘겨라.
    """
    content_original = voc.get("content_original") or ""
    content_translated = voc.get("content_translated")
    title = content_original[:80] + ("..." if len(content_original) > 80 else "")
    if not title:
        title = f"[empty] VOC #{voc.get('id')}"

    product = voc.get("product") or {}
    platform = voc.get("platform") or {}
    categories = voc.get("categories") or []
    sentiment_label = voc.get("sentiment_label") or "neutral"

    tags: list[str] = []
    tags.extend(f"voc:{c}" for c in categories if c)
    if voc.get("country_code"):
        tags.append(f"country:{voc['country_code']}")
    if platform.get("code"):
        tags.append(f"channel:{platform['code']}")
    tags.append(f"sentiment:{sentiment_label}")
    if product.get("code"):
        tags.append(product["code"])
    if product.get("series_code"):
        tags.append(product["series_code"])
    # 중복 제거
    seen: set[str] = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))][:30]

    severity = SENTIMENT_TO_SEVERITY.get(sentiment_label, "info")
    qs = SEVERITY_QS.get(severity, 25)

    published_at = voc.get("published_at")
    valid_from = None
    year = datetime.now().year
    if published_at:
        try:
            dt = (
                datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                if isinstance(published_at, str)
                else published_at
            )
            valid_from = dt.date().isoformat()
            year = dt.year
        except Exception:
            pass

    subject_keywords: list[str] = []
    for k in (product.get("code"), product.get("name_en"), product.get("name_ko")):
        if k:
            subject_keywords.append(str(k))

    sentiment_score = voc.get("sentiment_score") or 0.0
    summary = (
        f"{product.get('code', '?')} VOC — {sentiment_label} "
        f"({platform.get('code', '?')}, {voc.get('country_code', '?')}, "
        f"score={sentiment_score:.2f})"
    )

    # content sections
    sections = [
        {
            "section_id": "1",
            "level": 1,
            "title": "본문 (원어)",
            "content_text": content_original,
        }
    ]
    if content_translated:
        sections.append(
            {
                "section_id": "2",
                "level": 1,
                "title": "본문 (한글 번역)",
                "content_text": content_translated,
            }
        )
    meta_text = (
        f"source_url: {voc.get('source_url') or 'n/a'}\n"
        f"engagement: likes={voc.get('likes_count', 0)}, "
        f"comments={voc.get('comments_count', 0)}, "
        f"shares={voc.get('shares_count', 0)}, "
        f"score={voc.get('engagement_score') or 0:.2f}\n"
        f"sentiment_score: {sentiment_score:.3f}\n"
        f"processed_at: {voc.get('processed_at') or 'n/a'}"
    )
    sections.append(
        {
            "section_id": "3",
            "level": 1,
            "title": "메타",
            "content_text": meta_text,
        }
    )

    return {
        "_external_id": str(voc["id"]),
        "data_type": "DOC",
        "team": "MX",
        "group": "VOC",
        "year": year,
        "title": title,
        "summary": summary,
        "doc_type": "voc_report",
        "tags": tags,
        "agents": ["market-voc-analyst"],
        "classification": "internal",
        "language": "ko",
        "author": "signalforge",
        "department": "MX/VOC",
        "valid_from": valid_from,
        "subject_keywords": subject_keywords,
        "quality_score": qs,
        "content": {"sections": sections},
    }


# ===========================================================================
# Data sources
# ===========================================================================
async def fetch_via_http(
    sf_base_url: str,
    sf_api_key: str,
    *,
    product_codes: list[str],
    since: datetime | None,
    page_size: int = 100,
    max_rps: float = 4.0,
    backfill_window_hours: int = 48,
) -> list[dict[str, Any]]:
    """SignalForge HTTP API 로 VOC 수집 (제품 코드 반복 + offset 페이지네이션).

    NOTE: SignalForge 의 ``from`` 쿼리는 ``published_at`` 기준 필터다 (server side).
    하지만 우리는 'NLP 가 새로 완료된 VOC' (= ``processed_at`` 새로움) 를 잡아야
    하므로:

    1. 서버 호출 시 ``from = since - backfill_window_hours`` 로 더 넓게 가져온 뒤
    2. client side 에서 ``processed_at >= since`` 인 것만 다시 필터한다.

    이렇게 하면 publish 는 시간 전이지만 sentiment 분석이 막 끝난 VOC 도
    누락 없이 흡수한다. backfill_window_hours 가 너무 작으면 누락,
    너무 크면 중복 호출이 늘 뿐 (UPSERT 라 데이터 손상은 없음).
    """
    all_recs: list[dict[str, Any]] = []
    interval = 1.0 / max(0.1, max_rps)
    headers = {"X-API-Key": sf_api_key} if sf_api_key else {}

    # server-side from_date: since 보다 backfill_window_hours 만큼 더 거슬러
    server_from: datetime | None = None
    if since is not None:
        server_from = since - timedelta(hours=int(backfill_window_hours))

    async with httpx.AsyncClient(timeout=30.0) as client:
        for code in product_codes:
            offset = 0
            while True:
                params: dict[str, Any] = {"limit": page_size, "offset": offset}
                if server_from is not None:
                    params["from"] = server_from.date().isoformat()
                url = f"{sf_base_url.rstrip('/')}/api/v1/products/{code}/voc"
                try:
                    resp = await client.get(url, params=params, headers=headers)
                except httpx.HTTPError as exc:
                    logger.warning("fetch %s offset=%s failed: %s", code, offset, exc)
                    break
                if resp.status_code == 404:
                    logger.info("product %s not found — skip", code)
                    break
                if resp.status_code != 200:
                    logger.warning(
                        "fetch %s offset=%s status=%s — abort product",
                        code, offset, resp.status_code,
                    )
                    break
                body = resp.json()
                items = body.get("items") or []
                if not items:
                    break
                all_recs.extend(items)
                logger.info(
                    "fetched product=%s offset=%s count=%s total_so_far=%s",
                    code, offset, len(items), len(all_recs),
                )
                if len(items) < page_size:
                    break
                offset += page_size
                await asyncio.sleep(interval)

    # client-side 필터 — processed_at >= since (서버가 published_at 기준이라 우회)
    if since is not None:
        filtered: list[dict[str, Any]] = []
        for v in all_recs:
            pat = v.get("processed_at")
            if not pat:
                continue  # NLP 미완료 — 별도 filter 단계에서도 걸러지지만 일찍 거름
            try:
                pdt = (
                    datetime.fromisoformat(pat.replace("Z", "+00:00"))
                    if isinstance(pat, str) else pat
                )
                if pdt >= since:
                    filtered.append(v)
            except Exception:
                # 파싱 실패면 보수적으로 포함 (UPSERT 라 안전)
                filtered.append(v)
        logger.info(
            "client-side processed_at filter: %s/%s kept (since=%s, server_from=%s)",
            len(filtered), len(all_recs), since, server_from,
        )
        all_recs = filtered

    return all_recs


async def fetch_via_db(db_url: str, *, since: datetime | None) -> list[dict[str, Any]]:
    """SignalForge PG 직접 query — Celery worker 내부에서 호출 시 가장 빠름.

    의존성: sqlalchemy + asyncpg (config 에 db_url 있을 때만 try-import).
    """
    try:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import joinedload, sessionmaker
    except ImportError:
        logger.error("sqlalchemy + asyncpg required for db_url mode")
        return []

    # SignalForge 의 모델 import — SF backend 의 sys.path 가 보장되어야 함
    try:
        from app.models.voc_record import VocRecord  # type: ignore[import-not-found]
    except ImportError:
        logger.error("SignalForge app.models.voc_record not importable")
        return []

    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    stmt = select(VocRecord).options(
        joinedload(getattr(VocRecord, "product", None)),
        joinedload(getattr(VocRecord, "platform", None)),
    )
    if since:
        stmt = stmt.where(VocRecord.processed_at >= since)
    stmt = stmt.where(VocRecord.processed_at.isnot(None))

    results: list[dict[str, Any]] = []
    async with SessionLocal() as session:
        rows = (await session.execute(stmt)).scalars().all()
        for r in rows:
            # SQLAlchemy 객체 → dict 변환 (가장 단순)
            d: dict[str, Any] = {
                c.name: getattr(r, c.name) for c in r.__table__.columns
            }
            prod = getattr(r, "product", None)
            if prod is not None:
                d["product"] = {
                    "code": getattr(prod, "code", None),
                    "series_code": getattr(prod, "series_code", None),
                    "name_en": getattr(prod, "name_en", None),
                    "name_ko": getattr(prod, "name_ko", None),
                }
            plat = getattr(r, "platform", None)
            if plat is not None:
                d["platform"] = {
                    "code": getattr(plat, "code", None),
                    "name": getattr(plat, "name", None),
                    "region": getattr(plat, "region", None),
                }
            results.append(d)
    await engine.dispose()
    return results


# ===========================================================================
# AX Hub Push
# ===========================================================================
async def push_to_aidh(
    records: list[dict[str, Any]],
    *,
    aidh_base_url: str,
    aidh_api_key: str,
    batch_size: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """변환된 record 배열을 AX Hub /api/records/import 로 batch 호출.

    Returns:
        {ok, failed, batches, dead_letter (list[record + error])}
    """
    ok = failed = batches = 0
    dead_letter: list[dict[str, Any]] = []
    headers = {
        "X-API-Key": aidh_api_key,
        "Content-Type": "application/json",
    }
    url = f"{aidh_base_url.rstrip('/')}/api/records/import"
    params = {
        "auto_seq": "true",
        "external_source": "signalforge",
        "dry_run": "true" if dry_run else "false",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(0, len(records), batch_size):
            batch = records[i: i + batch_size]
            payload = {
                "auto_seq": True,
                "external_source": "signalforge",
                "records": batch,
            }
            try:
                resp = await client.post(url, params=params, headers=headers, json=payload)
                resp.raise_for_status()
                body = resp.json()
                batches += 1
                ok += body.get("ok", 0)
                failed += body.get("failed", 0)
                for row in body.get("results", []):
                    if row.get("error"):
                        dead_letter.append(row)
                logger.info(
                    "batch %s: count=%s ok=%s failed=%s",
                    batches, len(batch), body.get("ok", 0), body.get("failed", 0),
                )
            except httpx.HTTPError as exc:
                logger.exception("batch failed: %s", exc)
                failed += len(batch)
                dead_letter.append({"error": f"batch http error: {exc}", "batch_size": len(batch)})
    return {
        "ok": ok,
        "failed": failed,
        "batches": batches,
        "dead_letter": dead_letter,
    }


# ===========================================================================
# CLI
# ===========================================================================
def _load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        logger.error("config not found: %s", p)
        sys.exit(2)
    body = p.read_text(encoding="utf-8")
    # 환경변수 ${VAR} 치환 — 누락된 변수는 즉시 에러 (silent empty 차단)
    import re

    missing: list[str] = []

    def repl(m):
        name = m.group(1)
        val = os.environ.get(name)
        if val is None or val == "":
            missing.append(name)
            return f"<MISSING_{name}>"
        return val

    body = re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", repl, body)
    if missing:
        logger.error(
            "config has unset env vars: %s — set them or remove from config.yml",
            ", ".join(sorted(set(missing))),
        )
        sys.exit(2)
    return yaml.safe_load(body) or {}


async def run(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)

    aidh = cfg.get("aidatahub") or {}
    aidh_url = args.aidh_url or aidh.get("base_url")
    aidh_key = args.aidh_key or aidh.get("api_key") or ""
    if not aidh_url:
        logger.error("aidh base_url missing (config.aidatahub.base_url 또는 --aidh-url)")
        return 2

    sf = cfg.get("signalforge") or {}
    sf_url = sf.get("base_url")
    sf_key = sf.get("api_key") or ""
    sf_db = sf.get("db_url")
    sync_cfg = cfg.get("sync") or {}
    batch = args.batch_size or sync_cfg.get("batch_size") or 100
    product_codes = sync_cfg.get("product_codes") or DEFAULT_PRODUCT_CODES

    since: datetime | None = None
    if args.mode == "push-recent":
        if args.since:
            since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        else:
            since = datetime.now(timezone.utc) - timedelta(minutes=int(args.since_minutes or 35))
    elif args.mode == "push-all":
        since = None

    # 데이터 수집
    if sf_db:
        logger.info("fetching VOC via DB: %s", sf_db.split("@")[-1] if "@" in sf_db else "(local)")
        raw = await fetch_via_db(sf_db, since=since)
    elif sf_url:
        logger.info("fetching VOC via HTTP: %s", sf_url)
        raw = await fetch_via_http(
            sf_url, sf_key,
            product_codes=product_codes,
            since=since,
            page_size=sync_cfg.get("page_size", 100),
            max_rps=sync_cfg.get("max_rps", 4.0),
        )
    else:
        logger.error("Neither signalforge.db_url nor signalforge.base_url set")
        return 2

    logger.info("fetched %s VOC records", len(raw))
    if not raw:
        logger.info("no VOC to sync — exit")
        return 0

    # 필터 + 변환
    records: list[dict[str, Any]] = []
    skipped_pii = skipped_unprocessed = 0
    filt = sync_cfg.get("filter") or {}
    require_processed = filt.get("require_processed_at", True)
    skip_pii_unmasked = filt.get("skip_when_pii_unmasked", True)

    for voc in raw:
        if require_processed and not voc.get("processed_at"):
            skipped_unprocessed += 1
            continue
        if skip_pii_unmasked and voc.get("pii_masked") is False:
            skipped_pii += 1
            continue
        try:
            records.append(voc_to_record(voc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("transform failed voc.id=%s: %s", voc.get("id"), exc)

    logger.info(
        "transform: ok=%s skipped_unprocessed=%s skipped_pii=%s",
        len(records), skipped_unprocessed, skipped_pii,
    )

    # AX Hub push
    summary = await push_to_aidh(
        records,
        aidh_base_url=aidh_url,
        aidh_api_key=aidh_key,
        batch_size=batch,
        dry_run=args.dry_run,
    )

    logger.info(
        "push complete: ok=%s failed=%s batches=%s dead_letter=%s",
        summary["ok"], summary["failed"], summary["batches"], len(summary["dead_letter"]),
    )

    # dead_letter dump — 우선순위: --dead-letter-dir / config.sync.dead_letter_dir
    # / $AIDH_DEAD_LETTER_DIR / fallback /tmp. Celery worker CWD 의존 X.
    if summary["dead_letter"]:
        dl_dir = (
            args.dead_letter_dir
            or sync_cfg.get("dead_letter_dir")
            or os.environ.get("AIDH_DEAD_LETTER_DIR")
            or "/tmp"
        )
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # process pid 추가로 동시 호출 충돌 방지
        dump_path = Path(dl_dir) / f"aidh_sf_dead_letter_{ts}_{os.getpid()}.json"
        try:
            dump_path.parent.mkdir(parents=True, exist_ok=True)
            dump_path.write_text(
                json.dumps(summary["dead_letter"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.warning("dead_letter dumped: %s", dump_path)
        except OSError as exc:
            logger.error("dead_letter dump failed (path=%s): %s", dump_path, exc)

    return 0 if summary["failed"] == 0 else 1


def main() -> None:
    ap = argparse.ArgumentParser(
        description="SignalForge → AX Hub VOC 동기화 어댑터"
    )
    ap.add_argument(
        "--mode",
        choices=["push-all", "push-recent"],
        default="push-recent",
        help="push-all=전체 backfill (since 무시) / push-recent=증분 (since 또는 since-minutes)",
    )
    ap.add_argument("--config", default="config.yml", help="config.yml 경로")
    ap.add_argument("--since", default=None, help="ISO 8601 시각. push-recent 시 사용")
    ap.add_argument(
        "--since-minutes", type=int, default=35,
        help="push-recent 시 since 미지정이면 (now - N분). 기본 35분.",
    )
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="검증만, AX Hub 저장 안 함")
    ap.add_argument(
        "--dead-letter-dir", default=None,
        help="dead_letter dump 디렉토리 (생략 시 config.sync.dead_letter_dir → $AIDH_DEAD_LETTER_DIR → /tmp)",
    )
    ap.add_argument("--aidh-url", default=None, help="AX Hub base URL override")
    ap.add_argument("--aidh-key", default=None, help="AX Hub API key override")
    ap.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
