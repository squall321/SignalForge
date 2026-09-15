# quasarzone 이 table→div 레이아웃으로 바뀌어도 목록 파싱이 유지되는지 검증한다.
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from platforms.quasarzone import QuasarzoneCrawler

# 2026-09 실제 페이지 구조를 줄인 것. 링크는 a.subject-link 로 그대로 보이는데
# 옛 셀렉터(table tbody tr)에는 공지·광고 행만 잡혀 14일간 0건이었다.
NEW_LAYOUT = """
<div class="v2-list">
  <div class="v2-list-row v2-list-row--text">
    <div class="v2-list-row__body">
      <div class="tit-with-badge">
        <p class="tit"><a class="subject-link" href="/bbs/qf_mobile/views/91075?page=1">
          Galaxy S26 발열 심한가요?</a></p>
        <span class="ctn-count qc-count-comment">7</span>
      </div>
    </div>
    <div class="v2-list-row__meta-group">
      <span class="user-nick-wrap v2-nick" data-nick="tester1">tester1</span>
      <span class="v2-list-row__hit">조회 99</span>
      <span class="v2-list-row__time">09.14</span>
    </div>
  </div>
  <div class="v2-list-row v2-list-row--text">
    <div class="v2-list-row__body">
      <div class="tit-with-badge">
        <p class="tit"><a class="subject-link" href="/bbs/qf_mobile/views/91073?page=1">
          폴드8 힌지 유격</a></p>
      </div>
    </div>
    <div class="v2-list-row__meta-group">
      <span class="user-nick-wrap v2-nick" data-nick="tester2">tester2</span>
      <span class="v2-list-row__time">12:30</span>
    </div>
  </div>
</div>
<table><tbody>
  <tr><td>공지 행 — subject-link 없음</td></tr>
</tbody></table>
"""

# 옛 레이아웃 — 폴백이 살아있는지 확인용
OLD_LAYOUT = """
<div class="list-board-wrap"><table><tbody>
  <tr>
    <td><a class="subject-link" href="/bbs/qf_mobile/views/900?page=1">옛 구조 글</a></td>
    <td><span class="user-nick-wrap" data-nick="olduser">olduser</span></td>
    <td><span class="date">09-13</span></td>
    <td><span class="ctn-count">3</span></td>
  </tr>
</tbody></table></div>
"""


def test_new_div_layout_is_parsed():
    out = QuasarzoneCrawler()._parse_list(NEW_LAYOUT, "qf_mobile")
    assert len(out) == 2, f"새 레이아웃에서 {len(out)}건만 파싱됐다"
    first = out[0]
    assert "Galaxy S26" in first.content
    assert first.source_url.endswith("/bbs/qf_mobile/views/91075?page=1")
    assert first.author_name == "tester1"
    assert first.comments_count == 7
    assert first.published_at is not None, "'09.14' 형식 날짜를 못 읽는다"


def test_old_table_layout_still_works():
    """폴백 제거로 옛 구조를 깨뜨리지 않았는지."""
    out = QuasarzoneCrawler()._parse_list(OLD_LAYOUT, "qf_mobile")
    assert len(out) == 1
    assert out[0].author_name == "olduser"
    assert out[0].published_at is not None


def test_dot_and_dash_dates_both_parse():
    c = QuasarzoneCrawler()
    dot = c._parse_quasar_date("09.14")
    dash = c._parse_quasar_date("09-14")
    assert dot is not None and dash is not None
    assert dot == dash, "'09.14' 와 '09-14' 가 다른 시각으로 읽힌다"


def test_rows_without_link_are_skipped():
    out = QuasarzoneCrawler()._parse_list(NEW_LAYOUT, "qf_mobile")
    assert all(v.source_url for v in out)
    # 다른 게시판 코드로 부르면 href 조건에서 전부 걸러져야 한다
    assert QuasarzoneCrawler()._parse_list(NEW_LAYOUT, "qn_mobile") == []
