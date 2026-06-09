"""
사이트 로테이션 수집 러너.

working 크롤러를 순환 실행하며 여러 사이클 누적 수집한다.
댓글 external_id가 안정적이라(=멱등) 재실행분은 신규만 DB에 적재된다.

환경변수:
  ROTATE_CYCLES     사이클 수 (기본 4)
  ROTATE_SITE_GAP   사이트 간 대기 초 (기본 20)
  ROTATE_CYCLE_GAP  사이클 간 대기 초 (기본 300)
실행: DATABASE_URL=... ../.venv/bin/python rotate_collect.py
"""
import asyncio
import logging
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from platforms.clien import ClienCrawler
from platforms.ppomppu import PpomppuCrawler
from platforms.dcinside import DCInsideCrawler
from platforms.xda import XDACrawler
from platforms.nineto5google import NineTo5GoogleCrawler
from platforms.mobile_review import MobileReviewCrawler
from platforms.arageek import ArageekCrawler
from platforms.tinhte import TinhteCrawler
from platforms.mybroadband import MyBroadbandCrawler
from platforms.sammobile import SamMobileCrawler
from platforms.techcabal import TechCabalCrawler
from platforms.sanook import SanookCrawler
from platforms.mobil_se import MobilSeCrawler
from platforms.techinafrica import TechInAfricaCrawler
from platforms.kompas import KompasCrawler
from platforms.sammyfans import SammyFansCrawler
from platforms.gsmchoice import GSMchoiceCrawler

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
log = logging.getLogger("rotate")
log.setLevel(logging.INFO)

CRAWLERS = [
    ("clien",         ClienCrawler),
    ("ppomppu",       PpomppuCrawler),
    ("dcinside",      DCInsideCrawler),
    ("xda",           XDACrawler),
    ("9to5google",    NineTo5GoogleCrawler),
    ("mobile_review", MobileReviewCrawler),
    ("arageek",       ArageekCrawler),
    ("tinhte",        TinhteCrawler),
    ("mybroadband",   MyBroadbandCrawler),
    ("sammobile",     SamMobileCrawler),
    ("techcabal",     TechCabalCrawler),
    ("sanook",        SanookCrawler),
    ("mobil_se",      MobilSeCrawler),
    ("techinafrica",  TechInAfricaCrawler),
    ("kompas",        KompasCrawler),
    ("sammyfans",     SammyFansCrawler),
    ("gsmchoice",     GSMchoiceCrawler),
]

CYCLES    = int(os.getenv("ROTATE_CYCLES", "4"))
SITE_GAP  = int(os.getenv("ROTATE_SITE_GAP", "20"))
CYCLE_GAP = int(os.getenv("ROTATE_CYCLE_GAP", "300"))


def db_total() -> str:
    """현재 voc_records 총 행수 (psql)"""
    try:
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5434", "-U", "signalforge",
             "-d", "signalforge", "-tA", "-c", "SELECT count(*) FROM voc_records;"],
            env={**os.environ, "PGPASSWORD": "signalforge_pass"},
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout.strip() or "?"
    except Exception as e:
        return f"err({e})"


async def main():
    t0 = time.time()
    log.info(f"=== 로테이션 시작: {CYCLES}사이클, 시작 DB={db_total()}건 ===")
    for cyc in range(1, CYCLES + 1):
        for name, cls in CRAWLERS:
            try:
                r = await cls().run()
                log.info(f"[c{cyc}] {name}: {r.get('items_collected', 0)}건 신규 (DB={db_total()})")
            except Exception as e:
                log.warning(f"[c{cyc}] {name} 실패: {e}")
            await asyncio.sleep(SITE_GAP)
        log.info(f"=== 사이클 {cyc}/{CYCLES} 완료 | DB={db_total()}건 | 경과 {time.time()-t0:.0f}s ===")
        if cyc < CYCLES:
            await asyncio.sleep(CYCLE_GAP)
    log.info(f"=== 로테이션 종료: 최종 DB={db_total()}건, 총 {time.time()-t0:.0f}s ===")


if __name__ == "__main__":
    asyncio.run(main())
