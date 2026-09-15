# ppomppu 목록의 컬럼 배치가 게시판마다 달라도 날짜·작성자를 맞게 읽는지 검증한다.
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from platforms.ppomppu import PpomppuCrawler

# 2026-09-15 실측 배치.
#   phone  : [id, 제목, 작성자, 날짜, ?, 조회]          → 날짜 cols[3]
#   review : [id, 분류, 제목, , 제목, 작성자, 날짜, ?, 조회] → 날짜 cols[6]
# 고정 인덱스(cols[3])로 읽던 탓에 review 글은 발행일이 통째로 NULL 이었다.
PHONE_ROW = """
<table><tr class="baseList">
  <td>3931739</td>
  <td><a class="baseList-title" href="view.php?id=phone&no=1"><span>갤럭시 S26 발열</span></a>
      <span class="baseList-c">7</span></td>
  <td><div class="list_name"><a>dkdlelx</a></div></td>
  <td>26/08/14</td>
  <td><span class="baseList-rec">3 - 1</span></td>
  <td><span class="baseList-views">2593</span></td>
</tr></table>
"""

REVIEW_ROW = """
<table><tr class="baseList">
  <td>37897</td>
  <td>컴퓨터</td>
  <td><a class="baseList-title" href="view.php?id=review&no=2"><span>갤럭시S9 물에 담궈보기</span></a></td>
  <td></td>
  <td>갤럭시S9 물에 담궈보기</td>
  <td><div class="list_name"><a>해인아범</a></div></td>
  <td>18/03/31</td>
  <td><span class="baseList-rec">0 - 1</span></td>
  <td><span class="baseList-views">1555</span></td>
</tr></table>
"""


def _parse(html, board):
    return PpomppuCrawler()._parse_board_list(html, board)


def test_phone_board_date_and_author():
    out = _parse(PHONE_ROW, "phone")
    assert len(out) == 1
    v = out[0]
    assert v.published_at is not None, "phone 날짜를 못 읽었다"
    assert v.published_at.year == 2026 and v.published_at.month == 8
    assert v.author_name == "dkdlelx"


def test_review_board_date_and_author():
    """review 는 날짜가 cols[6] — 고정 인덱스로는 NULL 이 된다."""
    out = _parse(REVIEW_ROW, "review")
    assert len(out) == 1
    v = out[0]
    assert v.published_at is not None, "review 날짜가 NULL — 역사 수집이 무의미해진다"
    assert v.published_at.year == 2018 and v.published_at.month == 3
    assert v.author_name == "해인아범", f"작성자를 {v.author_name!r} 로 읽었다"


def test_counts_found_by_class_not_position():
    """추천·조회수도 위치가 다르다 — 클래스로 찾아야 한다."""
    v = _parse(REVIEW_ROW, "review")[0]
    assert v.comments_count == 0
    p = _parse(PHONE_ROW, "phone")[0]
    assert p.comments_count == 7


def test_recommend_is_first_number_not_concatenated():
    """'3 - 1' 은 추천 3·반대 1 이다. 숫자만 이어붙이면 31 이 된다."""
    assert PpomppuCrawler._first_int("3 - 1") == 3
    assert PpomppuCrawler._first_int("0 - 1") == 0
    assert PpomppuCrawler._first_int("2593") == 2593
    assert PpomppuCrawler._first_int("") == 0
    assert PpomppuCrawler._first_int("없음") == 0
    assert _parse(PHONE_ROW, "phone")[0].likes_count == 3
    assert _parse(REVIEW_ROW, "review")[0].likes_count == 0


def test_find_date_col_scans_from_the_right():
    """제목에 '18/03' 같은 게 섞여도 오인하면 안 된다."""
    from bs4 import BeautifulSoup
    html = """<table><tr><td>18/03 특가</td><td>작성자</td><td>26/01/02</td></tr></table>"""
    cols = BeautifulSoup(html, "html.parser").select("td")
    assert PpomppuCrawler._find_date_col(cols) == 2


def test_missing_date_does_not_crash():
    no_date = """
    <table><tr class="baseList">
      <td>1</td>
      <td><a class="baseList-title" href="view.php?id=phone&no=9"><span>제목</span></a></td>
      <td>작성자</td>
      <td><span class="baseList-views">10</span></td>
    </tr></table>
    """
    out = _parse(no_date, "phone")
    assert len(out) == 1
    assert out[0].published_at is None
    assert out[0].author_name == "익명"
