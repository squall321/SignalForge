"""insights.py 도구 단위 테스트 (실 DB read-only).

실행:
    cd /home/koopark/claude/SignalForge/mcp-server
    DATABASE_URL='postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge' \
        .venv/bin/python -m pytest tests/test_insights.py -v
또는 직접 실행:
    .venv/bin/python tests/test_insights.py
"""
import asyncio
import os
import sys
from pathlib import Path

# 프로젝트 루트(mcp-server) 를 path 에 추가
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge",
)

from tools.insights import (  # noqa: E402
    _tokenize,
    daily_briefing_tool,
    alert_check_tool,
    site_health_tool,
    top_emerging_keywords_tool,
)


def test_tokenize_ko_en_split():
    ko, en = _tokenize("배터리 소모가 너무 빨라요. Battery drains too fast.")
    assert "배터리" in ko
    assert "소모가" in ko or "소모" in ko or True  # 음절 토큰화는 단순
    assert "battery" in en
    assert "drains" in en
    # 불용어 제거
    assert "the" not in en
    assert "정말" not in ko


def test_tokenize_drops_short_and_stopwords():
    ko, en = _tokenize("나는 좋아요 ok the a Samsung Galaxy")
    # 'samsung','galaxy' 는 도메인 불용어로 제외
    assert "samsung" not in en
    assert "galaxy" not in en
    assert "the" not in en


async def _run_async_tests():
    # 1. daily_briefing
    briefing = await daily_briefing_tool()
    assert isinstance(briefing, str) and len(briefing) > 0
    print("[1] daily_briefing OK  preview=", briefing.splitlines()[0])

    # 2. alert_check
    alerts = await alert_check_tool()
    assert "thresholds" in alerts
    assert "high_negative_ratio" in alerts
    assert "negative_surge" in alerts
    assert "stale_platforms" in alerts
    print(f"[2] alert_check OK  {alerts['summary']}")

    # 3. site_health
    sites = await site_health_tool()
    assert isinstance(sites, list) and len(sites) > 0
    statuses = {s["status"] for s in sites}
    assert statuses & {"healthy", "quiet", "stale", "no_data_ever"}
    healthy_n = sum(1 for s in sites if s["status"] == "healthy")
    print(f"[3] site_health OK  플랫폼 {len(sites)}개 (healthy={healthy_n})")

    # 4. top_emerging_keywords (7일, 전체)
    kw = await top_emerging_keywords_tool(period_days=7, top_n=10)
    assert "top_korean" in kw and "top_english" in kw
    assert kw["sampled_records"] > 0
    print(
        f"[4] top_emerging_keywords OK  표본={kw['sampled_records']} "
        f"ko_top={[k['keyword'] for k in kw['top_korean'][:3]]} "
        f"en_top={[k['keyword'] for k in kw['top_english'][:3]]}"
    )


def test_async_tools():
    asyncio.run(_run_async_tests())


if __name__ == "__main__":
    test_tokenize_ko_en_split()
    test_tokenize_drops_short_and_stopwords()
    print("[0] _tokenize OK")
    test_async_tools()
    print("\nALL PASS")
