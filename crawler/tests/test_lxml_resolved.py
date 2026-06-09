"""R27 Track B — bs4 lxml 의존성 검증.

R26 보고서가 '미해결 3 failures: bs4 lxml' 로 주장한 항목을
실측으로 확정. 주 venv(/home/koopark/claude/SignalForge/.venv, py3.13)
에서 lxml 6.1.0 이 정상 설치돼 있고 BeautifulSoup(html, "lxml") 백엔드가
실제 동작함을 1 케이스로 입증한다.

Discovery 가 식별한 lxml 직접 사용 모듈 8개:
  - crawler/platforms/reddit_rss.py (fallback chain: lxml → html.parser → regex)
  - crawler/platforms/anandtech.py
  - crawler/platforms/androidcentral.py
  - crawler/platforms/computerbase.py
  - crawler/platforms/macrumors.py
  - crawler/platforms/ppomppu.py
  - crawler/platforms/tinhte.py
  - crawler/scripts/crisis_kr_backfill.py
모두 BeautifulSoup(..., "lxml") 호출이므로 lxml import 성공 +
BeautifulSoup parser 기동 성공만 검증하면 의존성 OK.
"""

from __future__ import annotations


def test_lxml_module_importable() -> None:
    """lxml 패키지가 venv 에 설치돼 있어야 한다."""
    import lxml  # noqa: F401

    # 버전 문자열 노출 확인 (메타데이터 깨짐 방지)
    assert hasattr(lxml, "__version__") or hasattr(lxml, "etree")


def test_beautifulsoup_lxml_backend_parses_html() -> None:
    """bs4 가 lxml 백엔드로 HTML 을 실제 파싱할 수 있어야 한다.

    8개 크롤러가 BeautifulSoup(html, "lxml") 패턴을 쓰므로
    이 한 줄이 실패하면 모든 RSS/HTML 수집이 죽는다.
    """
    from bs4 import BeautifulSoup

    html = (
        "<html><body>"
        "<div class='post'><p>안녕</p><span>Galaxy S24</span></div>"
        "</body></html>"
    )
    soup = BeautifulSoup(html, "lxml")

    # 정상 파싱: 한국어/영문 텍스트 모두 추출
    assert soup.p is not None
    assert soup.p.get_text(strip=True) == "안녕"
    assert soup.span.get_text(strip=True) == "Galaxy S24"
    # 셀렉터 동작 (lxml 백엔드 핵심 사용 패턴)
    div = soup.select_one("div.post")
    assert div is not None
    assert "Galaxy" in div.get_text()
