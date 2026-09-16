# 자격증명이 없어 수집을 건너뛰는 크롤러가 그 사실을 결과에 남기는지 전수 검사한다.
"""왜 로그 경고로는 부족한가.

자격증명이 없으면 크롤러는 logger.warning 을 찍고 빈 리스트를 돌려준다.
그러면 crawl_jobs 에는 status=done, 0건으로 남는다 — **"새 글이 없는 소스"와
글자 하나 다르지 않다.** Twitter 가 그 상태로 109일, Reddit 이 109일을 갔다.

컨테이너 stderr 를 뒤져야만 보이는 사실은 관측된 게 아니다. report_blocked 로
결과에 남겨야 status=blocked + 사유가 되어 대시보드와 DB 에서 보인다.

이 테스트는 개별 크롤러가 아니라 **규칙**을 지킨다. 새 크롤러가 자격증명
게이트를 추가하면서 보고를 빠뜨리면 여기서 걸린다.
"""
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PLATFORMS = sorted((ROOT / "platforms").glob("*.py"))

# 자격증명·API 키 부재를 뜻하는 표현. 백필 인자(WAYBACK_YEAR 등)는 자격증명이
# 아니라 오케스트레이션 파라미터라 제외한다 — 값이 없을 때 거르는 게 정상이다.
CRED_HINT = re.compile(
    r"(CLIENT_ID|CLIENT_SECRET|API_KEY|USERNAME|PASSWORD|HANDLE|TOKEN|BEARER)")
NOT_A_CREDENTIAL = re.compile(r"(WAYBACK_YEAR|_YEAR\b|PUBLISHED_(BEFORE|AFTER))")


def _gate_blocks(src: str):
    """`if not <자격증명>:` 로 시작해 return [] 로 끝나는 구간들."""
    out = []
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if not re.match(r"\s*if not .*:", line):
            continue
        block = "\n".join(lines[i:i + 10])
        if "return []" not in block:
            continue
        # 게이트 조건이나 그 직후 메시지에 자격증명 단서가 있어야 한다
        if not CRED_HINT.search(block) or NOT_A_CREDENTIAL.search(block):
            continue
        out.append((i + 1, block))
    return out


@pytest.mark.parametrize("path", PLATFORMS, ids=lambda p: p.name)
def test_credential_gate_reports_blocked(path):
    src = path.read_text()
    for lineno, block in _gate_blocks(src):
        assert "report_blocked" in block, (
            f"{path.name}:{lineno} — 자격증명이 없어 건너뛰면서 report_blocked 를 "
            "부르지 않는다. 그러면 status=done, 0건으로 찍혀 '새 글 없음'과 "
            "구별되지 않는다.\n" + block
        )


def test_the_rule_actually_finds_the_known_gates():
    """탐지기가 아무것도 못 찾으면 위 테스트는 항상 통과한다 — 무의미해진다."""
    found = {p.name for p in PLATFORMS if _gate_blocks(p.read_text())}
    for expected in ("reddit.py", "bluesky.py", "twitter.py", "youtube_comments.py"):
        assert expected in found, f"{expected} 의 자격증명 게이트를 못 찾는다 — 탐지기가 고장났다"
