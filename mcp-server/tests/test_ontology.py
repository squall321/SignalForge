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


def test_noise_cancelling_is_not_a_noise_defect():
    """'노이즈캔슬링'(ANC)은 기능이다 — 잡음 결함으로 보면 ANC 불만 질의가 잡음 글로 간다."""
    assert "noise" not in O.interpret("버즈3 프로 노이즈캔슬링 불만이에요").symptoms
    assert "noise" not in O.interpret("노이즈 캔슬링이 약해요").symptoms
    assert "noise" in O.interpret("버즈3 노이즈가 심해요").symptoms
    assert "noise" in O.interpret("통화할 때 지지직 잡음").symptoms


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


# ── 모델코드 · PLM 과제 (2026-10-23) ─────────────────────────────────────
from base import plm_mirror as _plm  # noqa: E402


@pytest.fixture
def plm():
    data = {
        "plm_project": [{"key": "P-1", "label": "프로젝트"}],
        "plm_task": [
            {"key": "TK-001", "label": "알파", "properties": {"project": "P-1"}, "previous_keys": ["OLD-1"]},
            {"key": "TK-002", "label": "베타_12", "properties": {"project": "P-1"}},
            {"key": "TK-003", "label": "감마 프로", "properties": {"project": "P-1"}},
            {"key": "TK-004", "label": "델타", "properties": {"project": "P-1"}}],
        "plm_model": [
            {"key": "SM-S938B_KOR_SKT", "properties": {"task": "TK-001", "base_code": "SM-S938B"}},
            {"key": "SM-S938N_KOR_KTF", "properties": {"task": "TK-001", "base_code": "SM-S938N"}},
            {"key": "SM-S931B_EUR_12", "properties": {"task": "TK-002", "base_code": "SM-S931B"}},
            {"key": "SM-S936B_EUR_12", "properties": {"task": "TK-002", "base_code": "SM-S936B"}},
            {"key": "SM-F976B_NA", "properties": {"task": "TK-003", "base_code": "SM-F976B"}},
            {"key": "SM-X100A_KOR_SKT", "properties": {"task": "TK-004", "base_code": "SM-X100A"}}],
    }
    return _plm.PlmIndex.from_rows(data)


def test_model_code_query_equals_name_query():
    """'SM-S938B 발열' = 'S25 울트라 발열' = '삼성 S25 울트라 발열' — 같은 제품·남는 말 없음(같은 결과)."""
    want = (["GS25U"], [])
    for q in ("SM-S938B 발열", "S25 울트라 발열", "삼성 S25 울트라 발열", "Samsung Galaxy S25 Ultra overheating"):
        it = O.interpret(q)
        assert (it.products, it.residual) == want, (q, it.products, it.residual)


def test_unreleased_model_code_scopes_the_generation_as_one_group():
    it = O.interpret("SM-S958U vs SM-S948B 배터리")
    assert it.products == ["GS26U"] and [c for _, c in it.product_sets] == [("GS27", "GS27P", "GS27U")]
    assert it.compare_all, "묶음은 하나로 센다 — 두 쪽 비교"


def test_unknown_model_code_stays_as_a_word():
    it = O.interpret("SM-S906B 화면")             # S22+ — 카탈로그 공백이라 사전에 없다
    assert not it.products and it.residual == ["sm-s906b"] and any("사전에 없어" in n for n in it.notes)
    # S25 Edge(S937)는 예전 이 시험의 예였는데 카탈로그 0047 로 들어와 이제 그 기기로 풀린다(2026-11-07)
    assert O.interpret("SM-S937B 화면").products == ["GS25E"]


def test_task_code_and_name_resolve_to_its_product(plm):
    for q in ("TK-001 발열", "알파 발열", "알파의 발열", "OLD-1 발열", "SM-S938B_KOR_SKT 발열"):
        it = O.interpret(q, plm=plm)
        assert (it.products, it.residual) == (["GS25U"], []), (q, it.products, it.residual, it.notes)
        assert any("TK-001(알파)" in n and "GS25U" in n for n in it.notes), it.notes


def test_task_with_several_products_is_one_group_and_names_drop_revision_suffix(plm):
    it = O.interpret("베타 배터리", plm=plm)              # 과제명 '베타_12' 의 개발명
    assert not it.products and [c for _, c in it.product_sets] == [("GS25", "GS25P")], it.product_sets


def test_spaced_task_name(plm):
    it = O.interpret("감마 프로 힌지", plm=plm)
    assert it.products == ["GZF8U"] and it.residual == [], (it.products, it.residual)


def test_task_without_known_model_code_falls_back_to_words(plm):
    it = O.interpret("델타 발열", plm=plm)
    assert not it.products and "델타" in it.residual
    assert any("제품에 이어지지 않는다" in n and "X100" in n for n in it.notes), it.notes


def test_no_plm_means_names_are_plain_words():
    it = O.interpret("알파 발열")
    assert not it.products and it.residual == ["알파"]


def test_rootdesign_task_code_and_model_code_resolve():
    """rootdesign 꼴 — key=과제명, 과제코드는 task_code, 개발모델은 model_code(SG-03·09)."""
    data = {"plm_project": [], "plm_model": [{"key": "M-1", "properties": {"task": "감마_C2", "model_code": "SM-S938N"}}],
            "plm_task": [{"key": "감마_C2", "label": "감마_C2", "properties": {"task_code": "TK-77"}}]}
    idx = _plm.PlmIndex.from_rows(data)
    for q in ("TK-77 발열", "감마 발열", "감마_C2 발열"):
        it = O.interpret(q, plm=idx)
        assert it.products == ["GS25U"] and it.residual == [], (q, it.products, it.residual, it.notes)
        assert any("TK-77(감마_C2)" in n for n in it.notes), it.notes


# ── PLM 프로젝트 · 푼 길 (SFO-1 · 2 · 3, 2026-11-25) ─────────────────────
@pytest.fixture
def plm_projects():
    """자리표시 — 프로젝트 P1(옛 key P0 · 이름 '피원 프로젝트' · 마케팅명 별칭) → 과제 A(과제코드 C1) · 과제 B,
    프로젝트 P9 → 사전에 없는 모델코드뿐, 프로젝트 P5 는 이름이 제품명과 같고 과제가 없다."""
    data = {
        "plm_project": [
            {"key": "PRJ-P1", "label": "피원 프로젝트", "previous_keys": ["PRJ-P0"],
             "properties": {"aliases": ["Galaxy S25 Ultra"]}},
            {"key": "PRJ-P9", "label": "피나인", "properties": {}},
            {"key": "PRJ-P5", "label": "Galaxy Z Fold8", "properties": {}}],
        "plm_task": [
            {"key": "과제A", "label": "과제A", "properties": {"task_code": "TK-C1", "project": "PRJ-P1"}},
            {"key": "과제B", "label": "과제B", "properties": {"task_code": "TK-C2", "project": "PRJ-P0"}},
            {"key": "과제Z", "label": "과제Z", "properties": {"task_code": "TK-C9", "project": "PRJ-P9"}}],
        "plm_model": [
            {"key": "M-A", "properties": {"task": "과제A", "model_code": "SM-S938B"}},
            {"key": "M-B", "properties": {"task": "과제B", "model_code": "SM-F976B"}},
            {"key": "M-Z", "properties": {"task": "과제Z", "model_code": "SM-X100A"}}],
    }
    return _plm.PlmIndex.from_rows(data)


def test_project_key_old_key_and_name_resolve_through_tasks_to_products(plm_projects):
    """프로젝트 → 과제 → 개발모델 → 모델코드 → 제품(SFO-1). 예전에는 프로젝트를 몰라 남는 말이 되고 증상만으로 전체를 찾았다."""
    for q, how in (("PRJ-P1 발열", "key"), ("prj-p0 발열", "old_key"), ("피원 프로젝트 발열", "label")):
        it = O.interpret(q, plm=plm_projects)
        assert not it.products and [c for _, c in it.product_sets] == [("GS25U", "GZF8U")], (q, it.product_sets, it.notes)
        assert it.residual == [] and it.symptoms == ["overheat"] and it.plm_projects == ["PRJ-P1"], (q, it.residual)
        assert [(m["core_key"], m["matched_by"], m["matched_on"]) for m in it.plm_matches] == [
            ({"type_slug": "plm_project", "key": "PRJ-P1"}, "project", how)], it.plm_matches
        assert it.plm_matches[0]["via"] == "project → task → model → model_code" and not it.plm_unresolved
        assert sum("프로젝트 PRJ-P1(피원 프로젝트) → 과제 2개" in n for n in it.notes) == 1, it.notes
        route = O.core_route(it)
        assert route["core_key"] == {"type_slug": "plm_project", "key": "PRJ-P1"} and route["matched_by"] == "project"


def test_project_without_linked_products_is_unresolved_not_a_word(plm_projects):
    """알아들었지만 제품에 못 이은 프로젝트 — 남는 말로 두지 않고(낱말로 찾으면 엉뚱한 글이 섞인다) 까닭을 남긴다."""
    it = O.interpret("PRJ-P9 발열", plm=plm_projects)
    assert not it.products and not it.product_sets and it.residual == [] and it.plm_projects == ["PRJ-P9"]
    assert [(u["core_key"], u["matched_by"], u["reason"]) for u in it.plm_unresolved] == [
        ({"type_slug": "plm_project", "key": "PRJ-P9"}, "project", "in_core_no_local_data")]
    assert "X100" in it.plm_unresolved[0]["detail"] and sum("프로젝트 PRJ-P9(피나인)" in n for n in it.notes) == 1
    route = O.core_route(it)
    assert route["core_key"]["key"] == "PRJ-P9" and route["via"] is None and route["core_unresolved"]


def test_project_name_or_alias_does_not_take_a_product_query(plm_projects):
    """제품 감지가 먼저다 — 프로젝트 이름 · 마케팅명 별칭이 제품 이름과 같아도 제품 질의는 그 제품이다."""
    for q, code in (("Galaxy S25 Ultra 발열", "GS25U"), ("Galaxy Z Fold8 힌지", "GZF8")):
        it = O.interpret(q, plm=plm_projects)
        assert it.products == [code] and not it.plm_projects and not it.plm_unresolved, (q, it.products, it.plm_projects)


def test_same_task_by_code_and_name_writes_one_line(plm_projects):
    """과제코드와 과제명을 함께 써도 줄 · 푼 길은 하나(SFO-2 — 11-24 실측에서 같은 줄이 두 번)."""
    it = O.interpret("TK-C1 과제A 발열", plm=plm_projects)
    assert it.products == ["GS25U"] and it.residual == [], (it.products, it.residual)
    assert sum("→ GS25U" in n for n in it.notes) == 1, it.notes
    assert [(m["core_key"]["key"], m["matched_by"]) for m in it.plm_matches] == [("과제A", "task_code")]
    it = O.interpret("TK-C9 과제Z 발열", plm=plm_projects)              # 제품에 못 잇는 과제 — 줄 하나, 낱말로는 남긴다
    assert not it.products and sum("제품에 이어지지 않는다" in n for n in it.notes) == 1, it.notes
    assert [(u["core_key"], u["matched_by"]) for u in it.plm_unresolved] == [
        ({"type_slug": "plm_task", "key": "과제Z"}, "task_code")]
    assert "과제z" in it.residual, it.residual


def test_matched_by_names_the_field_that_matched(plm_projects):
    want = {"과제A 발열": ("plm_task", "과제A", "task_name"), "SM-S938B 발열": ("plm_model", "M-A", "model_code"),
            "M-A 발열": ("plm_model", "M-A", "dev_model")}
    for q, (typ, key, how) in want.items():
        m = O.interpret(q, plm=plm_projects).plm_matches
        assert [(x["core_key"]["type_slug"], x["core_key"]["key"], x["matched_by"]) for x in m] == [(typ, key, how)], (q, m)


def test_project_named_with_another_projects_product_is_flagged(plm_projects):
    """질의가 프로젝트 P9 를 key 로 짚었는데 해석한 제품은 P1 것 — 주의(SG-04)가 프로젝트 key 도 본다."""
    it = O.interpret("PRJ-P9 Galaxy S25 Ultra 발열", plm=plm_projects)
    assert it.products == ["GS25U"] and it.plm_caution and "피나인" in it.plm_caution["foreign_projects"], it.plm_caution


def test_dictionary_resolutions_carry_matched_by_and_core_key(plm_projects):
    """사전으로 푼 것도 푼 길에 남긴다(SFO-3) — 마케팅명(별칭 사전) · 모델코드(모델코드 사전). 그 자리가 코어 프로젝트의
    마케팅명 별칭이면 core_key 가 그 프로젝트, 코어가 모르는 표기 · 거울 없음이면 null."""
    r = O.core_route(O.interpret("Galaxy S25 Ultra 발열", plm=plm_projects))
    assert (r["core_key"], r["matched_by"], r["via"]) == (
        {"type_slug": "plm_project", "key": "PRJ-P1"}, "marketing_name", "alias"), r
    assert r["resolutions"][0]["products"] == ["GS25U"] and "span" not in r["resolutions"][0]
    r = O.core_route(O.interpret("SM-S931U 화면", plm=plm_projects))          # 거울에 없는 모델코드 — 자체 사전으로
    assert (r["core_key"], r["matched_by"], r["via"], r["resolutions"][0]["products"]) == (
        None, "model_code", "model_code", ["GS25"]), r
    assert O.core_route(O.interpret("S25 울트라 발열", plm=plm_projects))["core_key"] is None, "코어 별칭이 아닌 표기"
    r = O.core_route(O.interpret("Galaxy S25 Ultra 발열"))
    assert r["core_key"] is None and r["matched_by"] == "marketing_name", "거울이 없으면 core_key 는 null"
    assert O.core_route(O.interpret("발열")) == {}, "범위로 푼 말이 없으면 칸도 없다"


def test_plm_resolutions_come_first_and_alias_on_two_projects_is_not_decided(plm_projects):
    r = O.core_route(O.interpret("Galaxy Z Fold8 vs TK-C1 발열", plm=plm_projects))
    assert [x["matched_by"] for x in r["resolutions"]] == ["task_code", "marketing_name"] and r["matched_by"] == "task_code"
    data = {"plm_project": [{"key": "PRJ-X", "label": "엑스", "properties": {"aliases": ["Galaxy S25 Ultra"]}},
                            {"key": "PRJ-Y", "label": "와이", "properties": {"aliases": ["Galaxy S25 Ultra"]}}],
            "plm_task": [{"key": "과제X", "label": "과제X", "properties": {"project": "PRJ-X"}}],   # 과제 0 인 거울은 '없음'
            "plm_model": []}
    m = O.core_route(O.interpret("Galaxy S25 Ultra 발열", plm=_plm.PlmIndex.from_rows(data)))["resolutions"][0]
    assert m["core_key"] is None and m["core_candidates"] == [
        {"type_slug": "plm_project", "key": "PRJ-X"}, {"type_slug": "plm_project", "key": "PRJ-Y"}], m
