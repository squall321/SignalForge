r"""test_legacy_relink — R9 Track B 검증.

R8 가 이미 옛 폰 (Galaxy Grand/Ace/Core/Mega/On/Pocket/Mini/Star/Win/Y/Trend
등) 의 영문/한국어 매칭을 추가했음. R9 Track B 는 그 위에 다음 정밀도 갭을
보강하는 변경을 검증한다.

R9 Track B 변경:
  1. "6.3in Galaxy Mega" 처럼 디지트가 *앞에* 오는 헤드라인 — bare
     `\bgalaxy\s+mega\b` fallback 으로 GMEGA63 매칭.
  2. "갤럭시 스타터팩" 가 GSTAR 로 잘못 매칭되던 substring 충돌 — substring
     MAP 에서 빼고 negative-lookahead regex 로만 매칭.
  3. "갤럭시 미니멀/트렌디" 등 일반어 충돌도 동일 패턴으로 차단.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.relink_products import match_product_code  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Track B-1: bare "Galaxy Mega" — 디지트가 앞에 오는 헤드라인 케이스.
# ─────────────────────────────────────────────────────────────────────
def test_galaxy_mega_bare_with_size_before():
    """'Samsung unveils 6.3in Galaxy Mega' — 종래 패턴은 mega 뒤 6.3 만 잡았음."""
    assert (
        match_product_code("Samsung unveils 6.3in Galaxy Mega smartphone")
        == "GMEGA63"
    )


def test_galaxy_mega_specific_versions_still_priority():
    """'Galaxy Mega 5.8' 처럼 명시적 디지트가 있으면 그쪽이 우선."""
    assert match_product_code("Galaxy Mega 5.8 cheaper") == "GMEGA58"
    assert match_product_code("Galaxy Mega 6.3 launch") == "GMEGA63"


# ─────────────────────────────────────────────────────────────────────
# Track B-2: '갤럭시 스타터팩' → 더이상 GSTAR 로 잘못 매칭되지 않는다.
# ─────────────────────────────────────────────────────────────────────
def test_galaxy_starter_pack_no_match():
    """'갤럭시 스타터팩' 은 OLD 'Galaxy Star' 와 별개 — 매칭 금지."""
    assert match_product_code("갤럭시 스타터팩 이거 맞아?") is None
    assert match_product_code("갤럭시 스타트업 키트") is None


def test_galaxy_star_real_still_matches():
    """진짜 'Galaxy Star' 는 여전히 매칭."""
    assert match_product_code("갤럭시 스타 출시 (2013)") == "GSTAR"
    assert match_product_code("갤럭시 스타 2 entry") == "GSTAR2"
    assert match_product_code("Galaxy Star 2 cheap") == "GSTAR2"


# ─────────────────────────────────────────────────────────────────────
# Track B-3: '갤럭시 미니멀' / '갤럭시 트렌디' 등 일반어 충돌 차단.
# ─────────────────────────────────────────────────────────────────────
def test_galaxy_minimal_no_match():
    """'갤럭시 미니멀 디자인' 은 'Galaxy Mini' 가 아니다."""
    assert match_product_code("갤럭시 미니멀 디자인 리뷰") is None


def test_galaxy_mini_real_still_matches():
    """진짜 'Galaxy Mini' / 'Mini 2' 는 매칭 유지."""
    assert match_product_code("갤럭시 미니 케이스") == "GMINI"
    assert match_product_code("갤럭시 미니 2 후기") == "GMINI2"
    assert match_product_code("Galaxy S3 mini i8190") == "GS3MINI"


def test_galaxy_trendy_no_match():
    """'갤럭시 트렌디' 는 'Galaxy Trend' 가 아니다."""
    assert match_product_code("갤럭시 트렌디한 색상") is None


def test_galaxy_trend_real_still_matches():
    """진짜 'Galaxy Trend' 매칭 유지."""
    assert match_product_code("Galaxy Trend Lite") == "GTRENDL"
    assert match_product_code("갤럭시 트렌드 라이트") == "GTRENDL"
