# MCP 도구 설명에 태깅률 숫자가 박혀 낡지 않는지, 실측 커버리지가 돌아오는지 고정한다.
"""막는 고장(2026-10-17).

도구 설명(docstring)은 호출한 AI 가 매번 읽는다. get_unmapped_voc 의 설명에 2026-06
수치("전체의 ~82%를 차지하는 미태깅분")가 그대로 남아, 실측 태깅률이 33.3% 가 된 뒤에도
AI 가 검색할 때마다 "제품 태깅이 거의 안 돼 있다"고 사용자에게 옮겼다.

숫자는 설명에 두지 않고 도구가 실측해서 돌려준다(coverage).
"""
import os
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ("/shared", str(ROOT.parent / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(ROOT))

# 한 줄에 태깅 관련어와 비율이 같이 있으면 태깅률 표기로 본다. 순서는 따지지 않는다 —
# 원래 문장은 "미태깅(제품 미매핑) VOC를 점검합니다 — 전체의 ~82%를" 처럼 사이가 길었다.
_TAG_WORD = re.compile(r"미태깅|태깅율|태깅률|untagged|tagged", re.IGNORECASE)
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")


def _rate_lines(src: str):
    return [ln.strip() for ln in src.splitlines()
            if _TAG_WORD.search(ln) and _PERCENT.search(ln)]


def test_tool_docs_carry_no_tagging_rate():
    hits = _rate_lines((ROOT / "server.py").read_text())
    assert not hits, f"도구 설명에 태깅률 숫자가 있다 — 낡으면 AI 가 그대로 옮긴다: {hits}"


def test_detector_catches_the_original_sentence():
    """검출기가 실제 사고 문장을 잡지 못하면 위 테스트는 아무것도 막지 못한다."""
    old = '    """미태깅(제품 미매핑) VOC를 점검합니다 — 전체의 ~82%를 차지하는 미태깅분 진단.'
    assert _rate_lines(old)


_DB = os.getenv("DATABASE_URL", "")


@pytest.mark.skipif(not _DB, reason="DATABASE_URL 없음")
def test_unmapped_tool_reports_live_coverage():
    import asyncio

    from tools.coverage import get_unmapped_voc_tool

    r = asyncio.run(get_unmapped_voc_tool(limit=1))
    cov = r["coverage"]
    assert cov["active_total"] == cov["tagged"] + cov["untagged"]
    # 두 쿼리 사이에 수집이 끼면 몇 건 어긋날 수 있다 — 같은 모집단인지만 본다
    assert abs(cov["untagged"] - r["untagged_total"]) <= 1000, (cov, r["untagged_total"])
    assert 0 < cov["tagged_pct"] < 100
    reasons = {b["reason"] for b in r["breakdown"]}
    assert "(tagged_or_general)" not in reasons, "미태깅 행에 '태깅됨' 라벨을 붙이고 있다"
