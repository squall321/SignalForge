"""한국 사이트 페이지네이션 깊이 확장 — 1회 백카탈로그 수집 (트랙 B).

대상 사이트: clien / dcinside / ppomppu / fmkorea / dogdrip

기존 historical_kr_backfill.py 는 clien/dcinside/ppomppu 만 다룬다.
이 스크립트는 5개 한국 사이트 모두를 묶어서 LIST_PAGES 를 12 → 50 (기본) 으로 확장하고
실행 전후 voc 카운트 + 옛 글(>90d) 비율을 audit JSONL 로 기록한다.

원리:
  - 각 사이트 LIST_PAGES 가 module-level 상수라 import 전에 env 를 set 한다.
  - main() 진입 시점에 BACKFILL_PAGES 환경변수 → 사이트별
    {CLIEN,DCINSIDE,PPOMPPU,FMKOREA,DOGDRIP}_BACKFILL_PAGES 일괄 set 후 lazy import.
  - 동일 external_id 는 ON CONFLICT DO NOTHING (BaseCrawler.save) → 멱등.
  - DRY_RUN=1 이면 crawler 실행 없이 baseline 카운트만 audit 에 남긴다.

환경변수:
  DATABASE_URL                  (필수, DRY_RUN=1 이어도 카운트 측정에 사용)
  BACKFILL_PAGES                기본 50 (사이트별 LIST_PAGES 일괄)
  BACKFILL_MAX_POSTS            기본 600 (사이트별 MAX_POSTS 일괄)
  BACKFILL_SITES                쉼표 구분 — 지정 시 그 사이트만
                                (예: BACKFILL_SITES=fmkorea,dogdrip)
  DRY_RUN                       '1'/'true' 면 카운트만 측정 (기본 0)
  AUDIT_PATH                    audit JSONL 경로 (기본 ./audit_korean_deep.jsonl)
  ROUND                         audit 라벨 (기본 'R28-harvest')

실행:
  cd crawler
  DATABASE_URL=postgresql://signalforge:signalforge_pass@127.0.0.1:5434/signalforge \\
    ../.venv/bin/python scripts/korean_pagination_deep.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from typing import Dict, List, Tuple


# 사이트별 LIST_PAGES 가 module 상수라 import 전에 env 설정해야 한다.
BACKFILL_PAGES = int(os.getenv("BACKFILL_PAGES", "50"))
BACKFILL_MAX_POSTS = int(os.getenv("BACKFILL_MAX_POSTS", "600"))
for _site in ("CLIEN", "DCINSIDE", "PPOMPPU", "FMKOREA", "DOGDRIP"):
    os.environ.setdefault(f"{_site}_BACKFILL_PAGES", str(BACKFILL_PAGES))
    os.environ.setdefault(f"{_site}_MAX_POSTS", str(BACKFILL_MAX_POSTS))


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.audit import audit_round  # noqa: E402  (Harvest3 트랙 P2: start/end 자동 보장)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("korean_deep")


def _load_crawlers():
    """import 시 LIST_PAGES 가 고정되므로 env 설정 이후에만 호출."""
    from platforms.clien import ClienCrawler
    from platforms.dcinside import DCInsideCrawler
    from platforms.ppomppu import PpomppuCrawler
    from platforms.fmkorea import FMKoreaCrawler
    from platforms.dogdrip import DogdripCrawler

    return [
        ("clien",    ClienCrawler),
        ("dcinside", DCInsideCrawler),
        ("ppomppu",  PpomppuCrawler),
        ("fmkorea",  FMKoreaCrawler),
        ("dogdrip",  DogdripCrawler),
    ]


def _selected_sites(all_sites):
    raw = os.getenv("BACKFILL_SITES", "").strip()
    if not raw:
        return all_sites
    keep = {s.strip().lower() for s in raw.split(",") if s.strip()}
    return [(n, c) for n, c in all_sites if n in keep]


def _psql_scalar(sql: str) -> str:
    try:
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5434", "-U", "signalforge",
             "-d", "signalforge", "-tA", "-c", sql],
            env={**os.environ, "PGPASSWORD": "signalforge_pass"},
            capture_output=True, text=True, timeout=20,
        )
        return out.stdout.strip() or "?"
    except Exception as e:
        return f"err({e})"


def _site_counts(code: str) -> Dict[str, str]:
    total = _psql_scalar(
        f"SELECT count(*) FROM voc_records v "
        f"JOIN platforms p ON v.platform_id=p.id WHERE p.code='{code}';"
    )
    old = _psql_scalar(
        f"SELECT count(*) FROM voc_records v "
        f"JOIN platforms p ON v.platform_id=p.id "
        f"WHERE p.code='{code}' AND v.published_at < NOW() - INTERVAL '90 days';"
    )
    return {"total": total, "old_90d": old}


def _audit_path() -> str:
    """audit JSONL 경로 — env > 기본 ./audit_korean_deep.jsonl."""
    return os.getenv("AUDIT_PATH", "./audit_korean_deep.jsonl")


def _audit_round_label() -> str:
    """라운드 라벨 — env > 기본 R28-harvest."""
    return os.getenv("ROUND", "R28-harvest")


def _is_truthy(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


async def _run_one(name: str, cls) -> Tuple[int, float, str]:
    ts = time.time()
    try:
        r = await cls().run()
        saved = int(r.get("items_collected", 0) or 0)
        return saved, time.time() - ts, ""
    except Exception as e:
        return -1, time.time() - ts, str(e)


async def _main():
    if not os.getenv("DATABASE_URL"):
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    all_sites = _load_crawlers()
    sites = _selected_sites(all_sites)
    dry = _is_truthy("DRY_RUN")
    site_codes = [n for n, _ in sites]

    log.info(
        "=== KR 깊이 백카탈로그 시작: pages=%d max_posts=%d sites=%s dry=%s ===",
        BACKFILL_PAGES, BACKFILL_MAX_POSTS, site_codes, dry,
    )

    before = {n: _site_counts(n) for n, _ in sites}
    for n, c in before.items():
        log.info("  [before] %s: total=%s old(>90d)=%s", n, c["total"], c["old_90d"])

    # Harvest3+ 트랙 P2: audit_round 컨텍스트가 start/end 를 자동 보장.
    # 본문에서 raise 가 일어나도 end (status=fail) 이 try/finally 로 기록되어
    # verify D "harvest2 audit end 부재" 결함이 영구 해결된다.
    with audit_round(
        _audit_round_label(),
        track=os.getenv("AUDIT_TRACK") or None,
        script="korean_pagination_deep",
        path=_audit_path(),
        extra={
            "pages": BACKFILL_PAGES,
            "max_posts": BACKFILL_MAX_POSTS,
            "sites": site_codes,
            "dry_run": dry,
            "before": before,
        },
    ) as audit:
        if dry:
            log.info("DRY_RUN=1 — crawler 실행 생략, baseline 만 audit 에 기록")
            audit.update(dry_run=True, before=before)
            return

        t0 = time.time()
        results: Dict[str, dict] = {}
        for name, cls in sites:
            log.info("--- %s 시작 (pages=%d) ---", name, BACKFILL_PAGES)
            saved, elapsed, err = await _run_one(name, cls)
            results[name] = {"saved": saved, "elapsed_s": round(elapsed, 1), "error": err}
            if err:
                log.warning("  %s 실패: %s (%ds)", name, err, int(elapsed))
            else:
                log.info("  %s 완료: 신규 %d건 (%ds)", name, saved, int(elapsed))

        after = {n: _site_counts(n) for n, _ in sites}
        total_elapsed = int(time.time() - t0)
        log.info("=== KR 깊이 백카탈로그 종료 (%ds) ===", total_elapsed)

        deltas: Dict[str, dict] = {}
        for n in site_codes:
            b, a = before[n], after[n]
            try:
                d_total = int(a["total"]) - int(b["total"])
                d_old = int(a["old_90d"]) - int(b["old_90d"])
                ratio = round(d_old / d_total * 100, 1) if d_total > 0 else 0.0
            except ValueError:
                d_total = d_old = -1
                ratio = -1.0
            deltas[n] = {
                "total_before": b["total"], "total_after": a["total"], "d_total": d_total,
                "old_before": b["old_90d"], "old_after": a["old_90d"], "d_old_90d": d_old,
                "old_ratio_pct": ratio,
                "saved_by_crawler": results.get(n, {}).get("saved", -1),
            }
            log.info(
                "  [delta] %s: total %s→%s (+%d) | old(>90d) %s→%s (+%d, %.1f%%)",
                n, b["total"], a["total"], d_total,
                b["old_90d"], a["old_90d"], d_old, ratio,
            )

        audit.update(
            elapsed_s=total_elapsed,
            results=results,
            after=after,
            deltas=deltas,
        )


def main():  # entry-point wrapper (테스트에서 mock 하기 쉽게 분리)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
