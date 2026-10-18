# 질의 해석(온톨로지)이 표기·언어에 흔들리지 않고 개념을 바르게 잡는지 고정한다 (DB 불필요).
"""왜 이 테스트인가 (2026-10-17).

검색이 글자 일치라 같은 뜻도 표기마다 결과가 달랐다 —
  'S25 Ultra' 9,680건 · '갤럭시 S25 울트라' 88건,  '발열' 은 'overheating' 을 못 찾는다.
온톨로지는 질의를 개념으로 바꾼다. **같은 뜻이면 같은 개념**이어야 하고, 같은 개념이면
SQL 이 같아 결과가 같다(그건 test_semantic_search 가 실DB 로 본다).
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ("/shared", str(ROOT.parent / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 온톨로지는 crawler 의 정본 사전을 import 한다 — 컨테이너에서 돌릴 때는 /crawler 바인드가 필요하다
O = pytest.importorskip("ontology", reason="crawler 사전을 import 할 수 없음 — /crawler 바인드 필요")
from nlp import categorizer as _cat  # noqa: E402  (ontology 가 경로를 잡는다)
from nlp import defect_extract as _dx  # noqa: E402


def _sig(q):
    """SQL 을 결정하는 해석 성분. role(boost/derived)은 SQL 에서 같은 가산이라 합친다."""
    it = O.interpret(q)
    return (tuple(it.products), it.family, tuple(sorted(it.series)), tuple(sorted(it.symptoms)),
            tuple(sorted(it.components)), tuple(sorted(set(it.boost_components))),
            tuple(sorted(it.topics)), tuple(sorted(set(it.boost_topics))),
            tuple(sorted(it.markets)), tuple(sorted(it.channels)))


# ── 같은 뜻 = 같은 개념 ──────────────────────────────────────────────────
@pytest.mark.parametrize("group", [
    ["S25 울트라 발열", "갤럭시 S25 울트라 발열", "Galaxy S25 Ultra overheating", "s25u 발열",
     "S25 Ultra 과열", "S25울트라 발열이"],
    ["폴드7 힌지 먼지", "Z Fold 7 hinge dust", "갤럭시 Z 폴드7 힌지 먼지"],
    ["S25 시리즈 배터리 광탈", "S25 series battery drain", "S25 라인업 배터리 광탈"],
    ["번인", "burn-in", "burn in"],
])
def test_same_meaning_same_concepts(group):
    sigs = {q: _sig(q) for q in group}
    first = sigs[group[0]]
    diff = {q: s for q, s in sigs.items() if s != first}
    assert not diff, f"같은 뜻인데 해석이 갈렸다 — 기준 {group[0]!r}={first}\n다른 것: {diff}"


# ── 개념 종류별 ─────────────────────────────────────────────────────────
def test_product_and_symptom():
    it = O.interpret("S25 울트라 발열")
    assert it.products == ["GS25U"] and it.symptoms == ["overheat"]
    assert "thermal" in it.boost_components, "증상이 함의하는 부품이 가산에 없다"
    assert "performance" in it.boost_topics


def test_family_marker_expands_generation():
    assert O.interpret("S25 시리즈 발열").family
    assert not O.interpret("S25 발열").family, "세대 표지 없이 세대로 넓히면 안 된다"


@pytest.mark.parametrize("code,fam", [("GS25U", "GS25"), ("GFE25", "GS25"), ("GS25P", "GS25"),
                                      ("GTS10FP", "GTABS10"), ("AP16PM", "AP16"), ("PX9PXL", "PX9"),
                                      ("GW4C", "GW4"), ("GN20U", "GN20"), ("GZF7", "GZF7")])
def test_family_key(code, fam):
    assert O.family_key(code) == fam


def test_series_without_number():
    assert O.interpret("폴드 주름").series == ["zfold"]
    assert O.interpret("플립 힌지").series == ["zflip"]
    assert O.interpret("폴드7 주름").series == [], "번호가 있으면 시리즈가 아니라 모델이다"


def test_korean_short_product_forms():
    assert O.interpret("노트9 배터리").products == ["GN9"]
    assert O.interpret("워치4 클래식 배터리").products == ["GW4C"], "'클래식' 이 '워치4' 에 먹혔다"
    assert O.interpret("노트20 울트라 S펜").products == ["GN20U"]


def test_rival_and_counter_are_not_galaxy():
    assert "GN9" not in O.interpret("레드미 노트9 발열").products, "타사 '레드미 노트9' 를 갤럭시로 봤다"
    assert O.interpret("노트 9개 샀어요").products == [], "'노트 9개'(공책)를 제품으로 봤다"


def test_galaxy_ai_is_a_feature_not_a_brand():
    it = O.interpret("갤럭시 AI 번역")
    assert it.topics == ["ai_features"] and it.brands == []


def test_brand_and_device_scope():
    assert O.interpret("삼성 발열").brands == ["samsung"]
    assert ("watch", "samsung") in O.interpret("갤럭시 워치 배터리").categories
    assert ("tablet", None) in O.interpret("태블릿 화면 깜빡임").categories
    assert O.interpret("폴더블 주름").forms == ["foldable"]


def test_channel_needs_a_place_marker():
    """'유튜브 앱 튕김' 의 유튜브는 앱이다. 장소로 쓸 때만 채널 필터다."""
    assert O.interpret("레딧에서 픽셀 10 발열").channels
    assert O.interpret("유튜브 반응 S25").channels == ["youtube"]
    assert O.interpret("유튜브 앱 튕김").channels == []
    assert O.interpret("Pixel 10 overheating on reddit", channel_lexicon={"reddit": ["reddit_rss"]}).channels


def test_market_is_country_or_language():
    it = O.interpret("한국 S25 불만")
    assert it.markets == ["KR"] and it.languages == ["ko"]
    assert O.interpret("인도네시아 반응").markets == ["ID"], "인도네시아를 인도로 봤다"
    assert "US" in O.interpret("iPhone 16 overheating in US").markets
    assert O.interpret("tell us about S25").markets == [], "소문자 'us' 는 미국이 아니다"


def test_comparison_requires_all_products():
    it = O.interpret("S25 vs 아이폰 16 배터리 비교")
    assert it.products == ["GS25", "AP16"] and it.compare_all


def test_sentiment_recency_year():
    it = O.interpret("최근 S25 발열 불만")
    assert it.sentiment == "negative" and it.days == 90
    assert O.interpret("2024년 폴드6 화면 줄").year == 2024


def test_residual_is_clean():
    assert O.interpret("S25 울트라의 발열이 심해요").residual == [], "조사·감성어가 잔여어로 남았다"
    assert O.interpret("S25 vs 아이폰 16 배터리 비교").residual == [], "'vs' 와 '비교' 중 하나가 남았다"
    assert O.interpret("카카오톡 알림").residual == ["카카오톡", "알림"]


def test_residual_drops_endings_glued_to_concepts():
    """증상에 붙은 어미('버벅'+'여요')·맨 '폰'·빈도 부사는 남는 말이 아니다 — '폰이' 를 남기면
    부분일치로 '아이폰이' 글에 가산이 붙었다(2026-10-18 실측)."""
    assert O.interpret("유튜브 볼 때 폰이 버벅여요").residual == ["유튜브"]
    assert O.interpret("갤럭시가 찜질팩처럼 뜨끈해요").residual == ["찜질팩"]


def test_residual_keeps_noun_of_verb_and_brand_compound():
    """'실패해요' → '실패'(활용형은 본문에 거의 없다). 브랜드 합성어는 통째로 — '페이' 만 남기면
    네이버페이·카카오페이 글까지 가산을 받았다."""
    assert O.interpret("삼성페이 결제가 자꾸 실패해요").residual == ["삼성페이", "결제", "실패"]
    assert O.interpret("갤럭시처럼 발열").residual == [], "조사만 남은 토막은 남는 말이 아니다"


@pytest.mark.parametrize("word,stem", [
    ("알림이", "알림"), ("결제가", "결제"), ("배터리는", "배터리"), ("충전을", "충전"), ("카메라로", "카메라"),
    ("폰으로", "폰"), ("갤럭시와", "갤럭시"), ("업데이트에서", "업데이트"),
    ("삼성페이", "삼성페이"), ("디스플레이", "디스플레이"), ("딜레이", "딜레이"), ("와이파이", "와이파이"),
    ("손난로", "손난로"),   # 받침 있는 음절 뒤 '로' 는 조사가 아니다
])
def test_particle_follows_batchim(word, stem):
    """받침에 따라 갈리는 조사는 앞 음절 받침을 본다 — '페이' 의 '이' 를 떼지 않는다."""
    assert O._strip_particle(word) == stem


def test_no_concept_means_keyword_fallback():
    assert not O.interpret("카카오톡 알림").has_concepts


# ── 정본 사전과의 동기 ──────────────────────────────────────────────────
def test_every_lexicon_symptom_has_a_label():
    """결함 렉시콘에 증상이 추가됐는데 온톨로지가 모르면 질의에서 그 증상을 못 쓴다."""
    assert set(O.SYMPTOM_LABELS) == set(_dx._SYMPTOM_SRC), (
        set(O.SYMPTOM_LABELS) ^ set(_dx._SYMPTOM_SRC))


def test_every_lexicon_component_has_a_label():
    assert set(_dx._COMPONENT_SRC) <= set(O.COMPONENT_LABELS), set(_dx._COMPONENT_SRC) - set(O.COMPONENT_LABELS)


def test_topics_match_categories_annotation():
    """주제 개념 = categories 주석 코드. 하나라도 어긋나면 주제 검색이 빈손이 된다."""
    assert set(O.TOPIC_LABELS) == set(_cat.CATEGORY_KEYWORDS)
    assert set(O.QUERY_TOPIC_SRC) == set(O.TOPIC_LABELS)


def test_relations_point_to_known_concepts():
    for s, c in O.SYMPTOM_COMPONENT.items():
        assert s in O.SYMPTOM_LABELS and c in O.COMPONENT_LABELS, (s, c)
    for s, t in O.SYMPTOM_TOPIC.items():
        assert s in O.SYMPTOM_LABELS and t in O.TOPIC_LABELS, (s, t)
    for c, t in O.COMPONENT_TOPIC.items():
        assert c in O.COMPONENT_LABELS and t in O.TOPIC_LABELS, (c, t)


# ── 본문 동의어 ─────────────────────────────────────────────────────────
def test_literal_branches_from_lexicon():
    assert O.literal_branches(r"발열|뜨거워|열이\s*많이|\btoo hot\b(?!-)|\b(?:really|very)\s+hot\b") == \
        ["발열", "뜨거워", "열이 많이", "too hot"]


def test_symptom_terms_are_multilingual():
    terms = O.symptom_terms("overheat")
    assert "발열" in terms and "overheating" in terms
    assert "hot" not in terms, "맨 'hot' 은 본문 검색어로 쓰면 'hot deal' 까지 끌고 온다"
