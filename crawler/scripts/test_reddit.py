"""Reddit OAuth 키 입력 후 1회 호출 검증 — 운영자가 즉시 동작 확인용으로 사용.

이 스크립트는 `.env` 의 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET 로 실제 Reddit
OAuth 호출 + listing fetch 를 1 회 수행해 수집 건수를 출력한다.
pytest 단위 테스트 (`crawler/tests/test_reddit_oauth.py`) 와 달리 mock 을 쓰지 않는다.

운영 시나리오:
  1) 사용자가 docs/dashboard/REDDIT_OAUTH_GUIDE.md 의 1~3 단계 완료
     (Developer Platform 폼 통과 + .env 등록).
  2) scripts/activate-channels.sh 가 platforms.is_active 를 켠 후 이 스크립트를 호출.
  3) 또는 수동으로:  cd crawler && python -m scripts.test_reddit

키 미설정 환경에서는 안전하게 0 을 반환하고 종료한다 (exit code 0).
키 설정 후 호출 실패 (네트워크 / 인증 / Developer Platform 미승인) 시에만 exit 2.

실행 예:
  cd /home/koopark/claude/SignalForge/crawler
  python -m scripts.test_reddit
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# .env 자동 로드 (있을 경우) — 운영자가 명시적 export 하지 않아도 동작.
try:
    from dotenv import load_dotenv

    root_env = Path(__file__).resolve().parents[2] / ".env"
    if root_env.exists():
        load_dotenv(root_env, override=False)
except Exception:
    pass

from platforms.reddit import RedditCrawler, _has_reddit_keys  # noqa: E402


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


async def _run() -> int:
    has_keys = _has_reddit_keys()
    print(f"keys_present={has_keys}")

    if not has_keys:
        print(
            "[guide] .env 의 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET 가 비어 있습니다.\n"
            "       docs/dashboard/REDDIT_OAUTH_GUIDE.md 의 1~3 단계 (Developer Platform 폼 포함) 를 따른 후 다시 실행하세요."
        )
        return 0

    try:
        crawler = RedditCrawler()
        results = await crawler.crawl()
    except Exception as e:  # noqa: BLE001
        print(f"[error] Reddit 호출 실패: {type(e).__name__}: {e}")
        return 2

    print(f"collected={len(results)}")
    for i, voc in enumerate(results[:3], start=1):
        snippet = (voc.content or "").replace("\n", " ")[:120]
        print(f"  sample {i}: author={voc.author_name!r} score={voc.likes_count} text={snippet!r}")
    return 0


def main() -> int:
    _setup_logging()
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
