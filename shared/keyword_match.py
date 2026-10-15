# VOC 본문 키워드 검색 조건을 한 곳에서 결정한다 — MCP·REST 가 갈라지지 않게.
"""키워드 매칭 전략 (정본).

이 판단이 query.py 와 stats_sql.py 에 **복제**돼 있었고 주석으로 "같은 판단을
쓴다"고만 적혀 있었다. 전례가 있다 — REST 와 MCP 가 한국어 검색에서 갈라졌다.
그래서 조건 생성은 여기 하나만 둔다.

세 경로로 갈린다.

1. 비ASCII 키워드 → **부분일치**
   FTS 인덱스는 to_tsvector('english', content_translated) 하나뿐이라 한국어가
   사실상 안 먹는다. 실측(2026-09-12) '발열' FTS 15건 vs 원문 실제 1,346건 =
   재현율 1.1%. 한국어 행 96.1%가 영어로 번역돼 content_translated 에 '발열'이
   없고, 검색이 content_original 을 안 봤다. 'simple' 색인도 교착어 조사
   결합형('발열이')을 못 잡아 49.8%에 그친다.

2. ASCII **여러 토큰** → **구절 검색** (phraseto_tsquery)
   plainto_tsquery 는 `'s25' & 'ultra'` 로 바꿔 글 **전체에서 따로** 찾는다.
   그래서 "S25 Ultra" 검색이 아이폰 16 글·픽셀 10 Pro 글·독일어 지원중단
   기사·케이스 브랜드 "Ultra Hybrid" 글을 반환했다 — 실측(2026-10-15) 상위
   10건 중 5건이 "S25 Ultra" 를 포함하지 않았다. 정밀도 50%.
   'Galaxy Watch 8' 은 더 심하다 — 'watch' 가 흔한 동사라 plainto 5,984건 vs
   구절 2,727건으로 **2.2배 과다**였다.

   phraseto_tsquery 는 `'s25' <-> 'ultra'` 로 인접을 요구한다. 실측 —
     키워드           plainto  구절   부분일치  구절정밀  구절재현
     S25 Ultra         11,541  9,680   9,886    99.8%    97.8%
     Galaxy S25         9,442  6,927   7,241    99.8%    95.5%
     Galaxy Watch 8     5,984  2,727   2,754    99.8%    98.8%
     battery drain      5,955  3,513   3,584    98.8%    96.9%
   정밀도가 99%대로 오르면서 재현율은 95~99%를 지키고, FTS 인덱스를 그대로 쓴다.

3. ASCII **한 토큰** → FTS (plainto)
   토큰이 하나면 plainto 와 phraseto 가 같은 쿼리를 만든다. 어간 처리로
   overheating↔overheat 가 매칭되는 이점이 있어 그대로 둔다.

명시 모드로 덮어쓸 수 있다 — match='substring' | 'phrase' | 'fts'.
'fts'(느슨한 AND)는 "두 단어가 어디든 같이 나오는 글"을 일부러 찾을 때만 쓴다.
"""
import re

_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")

# ILIKE 특수문자를 이스케이프해 '100%' 같은 키워드가 와일드카드로 해석되지 않게 한다
_SUBSTR_CLAUSE = (
    "(v.content_original ILIKE :kw_like ESCAPE '\\' "
    " OR v.content_translated ILIKE :kw_like ESCAPE '\\')"
)
_FTS_CLAUSE = (
    "to_tsvector('english', COALESCE(v.content_translated, '')) "
    "@@ plainto_tsquery('english', :keyword)"
)
_PHRASE_CLAUSE = (
    "to_tsvector('english', COALESCE(v.content_translated, '')) "
    "@@ phraseto_tsquery('english', :keyword)"
)

VALID_MODES = ("auto", "substring", "phrase", "fts")


def is_multi_token(keyword: str) -> bool:
    """공백으로 갈린 토큰이 둘 이상인가 — 구절 검색 대상 판정."""
    return len((keyword or "").split()) > 1


def resolve_mode(keyword: str, match: str = "auto") -> str:
    """실제로 쓸 매칭 모드."""
    mode = (match or "auto").lower()
    if mode not in VALID_MODES:
        mode = "auto"
    if mode != "auto":
        return mode
    if _NON_ASCII_RE.search(keyword or ""):
        return "substring"
    return "phrase" if is_multi_token(keyword) else "fts"


def keyword_clause(keyword: str, match: str = "auto"):
    """(SQL 조건, params, 적용된 모드).

    조건은 `v` 별칭의 voc_records 를 가정한다 — 양쪽 호출부가 그렇게 쓴다.
    """
    mode = resolve_mode(keyword, match)
    if mode == "substring":
        esc = (keyword or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return _SUBSTR_CLAUSE, {"kw_like": f"%{esc}%"}, "substring"
    if mode == "phrase":
        return _PHRASE_CLAUSE, {"keyword": keyword}, "phrase"
    return _FTS_CLAUSE, {"keyword": keyword}, "fts"
