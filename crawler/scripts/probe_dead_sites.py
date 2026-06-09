"""
Track A — 죽은 사이트 복구 진단.

각 코드별로 collector 를 인스턴스화하여 run() 를 1회 호출,
DB INSERT 발생 여부 / HTTP error / fetch_count 를 캡처.

사용:
    DATABASE_URL=... python crawler/scripts/probe_dead_sites.py [code1 code2 ...]

출력:
    JSON 한 줄 per site: {code, status, items_collected, error, duration_s}
"""
import asyncio
import importlib
import json
import os
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_CRAWLER = os.path.dirname(_HERE)
sys.path.insert(0, _CRAWLER)

# tasks.py 의 _CRAWLER_SPECS 와 동일한 매핑 재사용
from tasks import _CRAWLER_SPECS  # noqa: E402


DEAD_CODES_DEFAULT = [
    "ausdroid", "gizmodo_au", "arageek", "mobile_review", "samsung_community",
    "stackexchange", "mybroadband", "sammobile", "techcabal", "techinafrica",
    "ithome", "gigazine", "mysmartprice", "inside_handy", "gsmchoice",
    "sammyfans", "hipertextual", "kompas", "dpreview", "mobil_se",
    "reddit_rss",
]


async def probe_one(code: str) -> dict:
    spec = _CRAWLER_SPECS.get(code)
    if spec is None:
        return {"code": code, "status": "no_spec", "items_collected": 0, "error": "not in _CRAWLER_SPECS"}

    mod, cls = spec
    try:
        m = importlib.import_module(mod)
        Crawler = getattr(m, cls)
    except Exception as exc:
        return {"code": code, "status": "import_error", "items_collected": 0,
                "error": f"{type(exc).__name__}: {exc}"}

    t0 = time.time()
    try:
        c = Crawler(platform_code=code)
        result = await c.run()
        dt = round(time.time() - t0, 2)
        return {
            "code": code,
            "status": "ok",
            "items_collected": int(result.get("items_collected", 0) or 0),
            "items_inserted": int(result.get("items_inserted", result.get("items_collected", 0)) or 0),
            "error": None,
            "duration_s": dt,
            "raw": {k: v for k, v in (result or {}).items() if isinstance(v, (int, float, str, bool))},
        }
    except Exception as exc:
        dt = round(time.time() - t0, 2)
        tb_short = traceback.format_exc().splitlines()
        last = tb_short[-1] if tb_short else ""
        return {
            "code": code,
            "status": "run_error",
            "items_collected": 0,
            "error": f"{type(exc).__name__}: {exc} | {last}",
            "duration_s": dt,
        }


async def main():
    codes = sys.argv[1:] or DEAD_CODES_DEFAULT
    for c in codes:
        r = await probe_one(c)
        print(json.dumps(r, ensure_ascii=False))
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
