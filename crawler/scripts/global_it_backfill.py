"""H1 — 글로벌 IT 사이트 1회 강제 backfill.

Track H1 of data_grow round (2026-06-09).

대상: 100% MX 매칭 사이트 (hackernews/sammobile/notebookcheck/engadget/xataka
/sammyfans/gigazine/anandtech/xda).

전략
  - 사이트별 LIST_PAGES / MAX_POSTS / TAG_PAGES 등 module 상수를 import 시점 후
    runtime 에 monkey-patch (env-overridable 한 사이트는 env, 그 외는 직접 setattr).
  - asyncio.run(cls().run()) 으로 in-process 1회 실행. celery worker 무영향.
  - ON CONFLICT DO NOTHING 으로 멱등. dedup 자동.

환경변수
  DATABASE_URL                 (필수)
  H1_SITES                     쉼표 구분 — 지정 시 그 사이트만
                               (예: H1_SITES=hackernews,sammobile)
  DRY_RUN                      '1' 면 실행하지 않고 patch 결과만 출력
  H1_FACTOR                    상수 곱 (기본 2.0). pages/posts 일괄 ×
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

H1_FACTOR = float(os.getenv("H1_FACTOR", "2.0"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

# hackernews / sammobile 등 LIST_PAGES env 기반 사이트는 import 전에 env set.
# 그 외는 import 후 setattr 로 직접 patch.
# (HN 은 module level constant 들이 env 미지원이라 setattr 로만 처리)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("h1_backfill")


# 사이트 spec: (site_code, module_path, class_name, patches)
# patches: dict {attr_name: new_value or callable(old)->new}
def _scale(old):
    try:
        return int(old * H1_FACTOR)
    except Exception:
        return old


SITES = [
    # hackernews: MAX_STORIES 600 → 1200, MAX_COMMENTS 1500 → 3000.
    # WINDOW 90d 그대로 (이미 폭넓음).
    (
        "hackernews", "platforms.hackernews", "HackerNewsCrawler",
        {"MAX_STORIES": _scale, "MAX_COMMENTS": _scale},
    ),
    # sammobile: LIST_PAGES 12 → 24, MAX_POSTS 같이 ×2.
    # 모듈 안에 MAX_POSTS 없음 (모든 글 저장) → LIST_PAGES 만.
    ("sammobile", "platforms.sammobile", "SamMobileCrawler",
     {"LIST_PAGES": _scale}),
    # sammyfans: LIST_PAGES 12 → 24.
    ("sammyfans", "platforms.sammyfans", "SammyFansCrawler",
     {"LIST_PAGES": _scale}),
    # notebookcheck: Google News RSS, MAX_POSTS 150 → 300.
    ("notebookcheck", "platforms.notebookcheck", "NotebookCheckCrawler",
     {"MAX_POSTS": _scale}),
    # engadget: 본문 URL fan-out, MAX_POSTS 150 → 300.
    ("engadget", "platforms.engadget", "EngadgetCrawler",
     {"MAX_POSTS": _scale}),
    # xataka: RSS + WP REST, MAX_POSTS 150 → 300.
    ("xataka", "platforms.xataka", "XatakaCrawler",
     {"MAX_POSTS": _scale}),
    # gigazine: archive 일자 LIST_PAGES 12 → 24 (2주 → 4주 archive).
    ("gigazine", "platforms.gigazine", "GigazineCrawler",
     {"LIST_PAGES": _scale}),
    # anandtech: TAG_PAGES 4 → 8, MAX_POSTS 150 → 300.
    ("anandtech", "platforms.anandtech", "AnandTechCrawler",
     {"TAG_PAGES": _scale, "MAX_POSTS": _scale}),
    # xda: tag pages 고정. 그냥 1회 재실행 (dedup 됨).
    ("xda", "platforms.xda", "XDACrawler", {}),
]


def _select_sites():
    raw = os.getenv("H1_SITES", "").strip()
    if not raw:
        return SITES
    keep = {s.strip().lower() for s in raw.split(",") if s.strip()}
    return [s for s in SITES if s[0] in keep]


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


def _site_counts(code: str) -> dict:
    total = _psql_scalar(
        f"SELECT count(*) FROM voc_records v "
        f"JOIN platforms p ON v.platform_id=p.id WHERE p.code='{code}';"
    )
    h24 = _psql_scalar(
        f"SELECT count(*) FROM voc_records v "
        f"JOIN platforms p ON v.platform_id=p.id "
        f"WHERE p.code='{code}' AND v.collected_at > NOW() - INTERVAL '24 hours';"
    )
    return {"total": total, "h24": h24}


def _free_mb() -> int:
    try:
        out = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5)
        for line in out.stdout.splitlines():
            if line.startswith("Swap:"):
                parts = line.split()
                return int(parts[2])  # used
    except Exception:
        pass
    return -1


def _patch_module(mod, patches: dict) -> dict:
    """모듈 상수 monkey-patch. 결과 dict 반환 (old → new)."""
    delta = {}
    for attr, new in patches.items():
        old = getattr(mod, attr, None)
        if old is None:
            delta[attr] = ("MISSING", "skip")
            continue
        try:
            new_val = new(old) if callable(new) else new
        except Exception as e:
            delta[attr] = (old, f"err({e})")
            continue
        setattr(mod, attr, new_val)
        delta[attr] = (old, new_val)
    return delta


async def _run_one(code: str, module_path: str, class_name: str, patches: dict):
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    p_delta = _patch_module(mod, patches)
    log.info("  [%s] patches=%s", code, p_delta)
    if DRY_RUN:
        return {"site": code, "saved": -1, "patches": p_delta, "dry_run": True}

    t0 = time.time()
    try:
        result = await cls(platform_code=code).run()
        saved = result.get("items_collected", 0)
        log.info("  [%s] %d건 (%ds)", code, saved, int(time.time() - t0))
        return {"site": code, "saved": saved, "patches": p_delta, "elapsed_s": int(time.time() - t0)}
    except Exception as e:
        log.warning("  [%s] 실패: %s", code, e)
        return {"site": code, "saved": -1, "error": str(e), "patches": p_delta}


async def main():
    if not os.getenv("DATABASE_URL"):
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    sites = _select_sites()
    log.info("=== H1 글로벌 IT backfill 시작: factor=%.1f sites=%s ===",
             H1_FACTOR, [s[0] for s in sites])

    swap_before = _free_mb()
    before = {s[0]: _site_counts(s[0]) for s in sites}
    for code, c in before.items():
        log.info("  [before] %s: total=%s h24=%s", code, c["total"], c["h24"])

    t0 = time.time()
    results = []
    for code, mod_path, cls_name, patches in sites:
        r = await _run_one(code, mod_path, cls_name, patches)
        results.append(r)

    swap_after = _free_mb()
    after = {s[0]: _site_counts(s[0]) for s in sites}
    log.info("=== H1 종료 (%ds, swap_used %dMB→%dMB) ===",
             int(time.time() - t0), swap_before, swap_after)
    for code in [s[0] for s in sites]:
        b, a = before[code], after[code]
        log.info("  [delta] %s: total %s→%s  h24 %s→%s",
                 code, b["total"], a["total"], b["h24"], a["h24"])

    # audit JSONL — round=data_grow track=H1
    audit_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "audit", "data_grow.jsonl",
    )
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "round": "data_grow",
        "track": "H1",
        "factor": H1_FACTOR,
        "dry_run": DRY_RUN,
        "swap_used_mb_before": swap_before,
        "swap_used_mb_after": swap_after,
        "results": results,
        "before": before,
        "after": after,
    }
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    log.info("audit → %s", audit_path)


if __name__ == "__main__":
    asyncio.run(main())
