# dogdrip 댓글의 작성자·발행일이 실제 HTML 구조에서 읽히는지 검증한다.
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from platforms.dogdrip import DogdripCrawler

# 2026-09-15 실측 구조. 사이트가 `.comment-bar-author` → `.comment-bar` 로 바뀐 것을
# 못 따라가 **모든 댓글**이 작성자 '익명' + 발행일 NULL 로 저장되고 있었다
# (10,431행 중 9,211행 = 88%). 그러면 그 댓글은 시계열 분석에서 통째로 빠진다.
NEW_COMMENT = """
<div class="ed comment-item clearfix">
  <div class="ed flex flex-middle margin-bottom-xxsmall comment-bar">
    <div class="ed inline-flex flex-middle margin-right-small">
      <h6 class="ed text-normal">
        <a class="ed link-reset member_40804645" href="#popup_menu_area">
          <img alt="[레벨:10]" class="xe_point_level_icon" src="x.gif"/>유프라테스</a>
      </h6>
      <span class="ed text-muted text-xxsmall">3 일 전</span>
    </div>
  </div>
  <div class="ed comment-content">
    <div class="rhymix_content xe_content">갤럭시 발열이 생각보다 심하네요</div>
  </div>
</div>
"""

# 옛 구조 — 폴백이 살아있는지
OLD_COMMENT = """
<div class="ed comment-item clearfix">
  <div class="comment-bar-author">
    <a class="link-reset">옛유저</a>
    <span class="text-muted">5 일 전</span>
  </div>
  <div class="xe_content">옛 구조 댓글입니다</div>
</div>
"""


def _comments(html):
    c = DogdripCrawler()
    from base.crawler import RawVOC
    post = RawVOC(external_id="p1", content="본문",
                  source_url="https://www.dogdrip.net/dogdrip/1")
    return c._parse_comments(html, post) if hasattr(c, "_parse_comments") else None


def test_new_structure_author_and_date():
    from bs4 import BeautifulSoup
    c = DogdripCrawler()
    it = BeautifulSoup(NEW_COMMENT, "html.parser").select_one(".comment-item")
    author_el = (it.select_one(".comment-bar a.link-reset")
                 or it.select_one(".comment-bar-author a.link-reset"))
    assert author_el is not None, "새 구조에서 작성자를 못 찾는다"
    for img in author_el.select("img"):
        img.decompose()
    assert author_el.get_text(strip=True) == "유프라테스"

    date_el = (it.select_one(".comment-bar .text-muted")
               or it.select_one(".comment-bar-author .text-muted"))
    assert date_el is not None, "새 구조에서 날짜를 못 찾는다"
    assert c._parse_relative_date(date_el.get_text(strip=True)) is not None


def test_old_structure_still_works():
    """폴백을 남겨 구조가 되돌아가도 견딘다."""
    from bs4 import BeautifulSoup
    it = BeautifulSoup(OLD_COMMENT, "html.parser").select_one(".comment-item")
    author_el = (it.select_one(".comment-bar a.link-reset")
                 or it.select_one(".comment-bar-author a.link-reset"))
    assert author_el is not None and author_el.get_text(strip=True) == "옛유저"


def test_selector_uses_fallback_chain():
    """소스가 두 셀렉터를 모두 보는지 — 하나만 보면 다음 변경에 또 전멸한다."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "platforms" / "dogdrip.py").read_text()
    assert '.comment-bar a.link-reset' in src
    assert '.comment-bar-author a.link-reset' in src
    assert '.comment-bar .text-muted' in src


# ── 상대시간 형식 ──────────────────────────────────────────────────────
def test_relative_formats():
    c = DogdripCrawler()
    for txt in ("방금 전", "5 분 전", "3 시간 전", "1 일 전",
                "2 주 전", "3 달 전", "4 개월 전", "2 년 전"):
        assert c._parse_relative_date(txt) is not None, f"{txt!r} 를 못 읽는다"


def test_absolute_formats():
    c = DogdripCrawler()
    d4 = c._parse_relative_date("2024.03.15")
    assert d4 is not None and d4.year == 2024 and d4.month == 3
    d2 = c._parse_relative_date("24.03.15")
    assert d2 is not None and d2.year == 2024, "'YY.MM.DD' 를 못 읽는다"
    hm = c._parse_relative_date("14:32")
    assert hm is not None, "'HH:MM'(오늘) 을 못 읽는다"


def test_four_digit_year_wins_over_two():
    """'2024.03.15' 가 2자리 규칙에 먼저 걸리면 20년으로 읽힌다."""
    c = DogdripCrawler()
    assert c._parse_relative_date("2024.03.15").year == 2024


def test_garbage_returns_none():
    c = DogdripCrawler()
    for txt in ("", "익명", "추천", "122", "ㅋㅋㅋ"):
        assert c._parse_relative_date(txt) is None, f"{txt!r} 를 날짜로 읽었다"
