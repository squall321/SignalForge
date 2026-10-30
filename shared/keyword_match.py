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
# 한글이 든 키워드는 원문만 본다. 번역문은 영어라 한글 키워드가 번역문에만 있는 글이 0건이었다('폴드8' ·
# '갤럭시 Z 폴드8' · '힌지' · '아이폰 17 프로' · '배터리', dev 2026-10-30). 번역문까지 보면 행마다 번역문 TOAST 를
# 풀어야 해 차가운 캐시에서 31초가 걸렸다(원문만이면 1.4초, SG-19).
_SUBSTR_ORIG_CLAUSE = "(v.content_original ILIKE :kw_like ESCAPE '\\')"
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


def _like_pattern(term: str) -> str:
    esc = (term or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def keyword_clause(keyword: str, match: str = "auto"):
    """(SQL 조건, params, 적용된 모드).

    조건은 `v` 별칭의 voc_records 를 가정한다 — 양쪽 호출부가 그렇게 쓴다.
    """
    mode = resolve_mode(keyword, match)
    if mode == "substring":
        clause = _SUBSTR_ORIG_CLAUSE if _HANGUL_RE.search(keyword or "") else _SUBSTR_CLAUSE
        return clause, {"kw_like": _like_pattern(keyword)}, "substring"
    if mode == "phrase":
        return _PHRASE_CLAUSE, {"keyword": keyword}, "phrase"
    return _FTS_CLAUSE, {"keyword": keyword}, "fts"


# ── 동의어 묶음 (시맨틱 검색의 본문 경로) ─────────────────────────────────
# 한 검색어씩 keyword_clause 를 OR 로 이으면 느리다. 실측(2026-10-17, 활성 99만 행) —
#   한국어 5개 부분일치 × 두 컬럼              7.5초
#   같은 5개, language_detected='ko' 원문만    0.40초 (누락 12/1,838 = 0.7% — 언어 오탐 행)
#   영어 4개를 tsquery 하나로 OR(||)          1.3초 (GIN 인덱스)
# 그래서 경로별로 한 번에 묶는다.
#   · 한글이 든 말 → 한국어 원문 부분일치. 번역문은 영어라 볼 필요가 없다.
#   · ASCII        → tsquery 하나로 합친 FTS (한 토큰 plainto, 여러 토큰 phraseto —
#                    keyword_clause 와 같은 판정).
#   · 그 밖의 비ASCII(일본어·러시아어·악센트 등)는 쓰지 않는다. 그 언어 글은 영어
#     번역문이 있어 FTS 쪽에서 잡힌다.
_HANGUL_RE = re.compile(r"[가-힣]")
_FTS_DOC = "to_tsvector('english', COALESCE(v.content_translated, ''))"

# PostgreSQL 'english' 설정의 불용어(snowball). 구절에서 이 말들은 **사라진다** —
# phraseto_tsquery('too hot') = 'hot', ('heats up') = 'heat'. 그러면 정밀한 구절이 흔한
# 낱말 하나가 되어 'hot deal'·'heat sink' 까지 끌고 온다. 그래서 불용어를 빼고 내용어가
# 하나만 남는 구절은 동의어로 쓰지 않는다(실측: 발열 동의어 묶음 1,239건·0.36초 —
# 이 구절들이 섞이면 시간 초과).
_EN_STOP = set(
    "a about above after again against all am an and any are as at be because been before being "
    "below between both but by can did do does doing don down during each few for from further had "
    "has have having he her here hers herself him himself his how i if in into is it its itself just "
    "me more most my myself no nor not now of off on once only or other our ours ourselves out over "
    "own s same she should so some such t than that the their theirs them themselves then there these "
    "they this those through to too under until up very was we were what when where which while who "
    "whom why will with you your yours yourself yourselves".split())


def fts_safe(term: str) -> bool:
    """FTS 동의어로 써도 뜻이 유지되는가 — 불용어가 빠진 뒤 내용어가 남아야 한다."""
    toks = re.findall(r"[a-z0-9]+", (term or "").lower())
    content = [t for t in toks if t not in _EN_STOP]
    if not content:
        return False
    return len(toks) < 2 or len(content) >= 2


def expansion_clauses(terms, prefix: str = "x"):
    """동의어 목록 → {'fts': (sql, params) | None, 'ko': (sql, params) | None}.

    두 조건은 따로 쓰라고 나눠 준다. 하나의 OR 로 묶으면 부분일치 쪽 때문에 FTS 도
    인덱스를 못 타고 행마다 to_tsvector 를 계산한다.
    """
    seen, fts_parts, fts_params, ko_pats = set(), [], {}, []
    for term in terms or ():
        t = " ".join((term or "").split())
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        if _HANGUL_RE.search(t):
            ko_pats.append(_like_pattern(t))
        elif not _NON_ASCII_RE.search(t) and fts_safe(t):
            name = f"{prefix}_f{len(fts_parts)}"
            fn = "phraseto_tsquery" if is_multi_token(t) else "plainto_tsquery"
            fts_parts.append(f"{fn}('english', :{name})")
            fts_params[name] = t
    out = {"fts": None, "ko": None}
    if fts_parts:
        out["fts"] = (f"{_FTS_DOC} @@ ({' || '.join(fts_parts)})", fts_params)
    if ko_pats:
        out["ko"] = (f"(v.language_detected = 'ko' AND v.content_original ILIKE ANY(:{prefix}_ko))",
                     {f"{prefix}_ko": ko_pats})
    return out


def fts_groups_clause(groups, prefix: str = "g"):
    """[[동의어…], [동의어…]] → 그룹끼리는 AND, 그룹 안은 OR 인 FTS 조건 하나. 없으면 None.

    제품 태그가 없는 글을 '삼성 + 태블릿' 범위에 넣을 때 쓴다. 부분일치를 섞으면 인덱스를
    못 타므로 ASCII 말만 쓴다 — 한국어 글도 영어 번역문이 있어 여기서 잡힌다.
    """
    gparts, params = [], {}
    for gi, terms in enumerate(groups or ()):
        parts = []
        for term in terms or ():
            t = " ".join((term or "").split())
            if not t or _NON_ASCII_RE.search(t) or not fts_safe(t):
                continue
            name = f"{prefix}{gi}_{len(parts)}"
            fn = "phraseto_tsquery" if is_multi_token(t) else "plainto_tsquery"
            parts.append(f"{fn}('english', :{name})")
            params[name] = t
        if parts:
            gparts.append("(" + " || ".join(parts) + ")")
    if not gparts:
        return None
    return f"{_FTS_DOC} @@ ({' && '.join(gparts)})", params


def like_any_clause(terms, name: str):
    """낱말 목록 중 하나라도 원문·번역문에 있는가 — 이미 좁혀진 후보에 거는 조건.

    후보 행마다 to_tsvector 를 계산하면 긴 기사에서 행당 수 ms 가 든다(실측: 후보 3,600건에
    10초대). ILIKE 는 그보다 수십 배 싸다. 인덱스가 필요한 전체 검색에는 쓰지 않는다.
    """
    pats = [_like_pattern(" ".join(t.split())) for t in terms or () if t and t.strip()]
    if not pats:
        return "FALSE", {}
    return (f"(v.content_original ILIKE ANY(:{name}) OR v.content_translated ILIKE ANY(:{name}))",
            {name: pats})


def hangul_like_patterns(terms):
    """한글이 든 말 → ILIKE 패턴(중복 없이). 시맨틱 검색의 한국어 본문 경로를 한 번에 훑을 때 쓴다."""
    out = []
    for term in terms or ():
        t = " ".join((term or "").split())
        if t and _HANGUL_RE.search(t):
            pat = _like_pattern(t)
            if pat not in out:
                out.append(pat)
    return out


def all_tokens_clause(tokens, prefix: str = "a"):
    """낱말이 **모두** 나오는 글 — 개념을 못 찾은 질의의 어휘 후보 경로. 없으면 None.

    '카카오톡 알림이 늦게 와요' 같은 질의는 온톨로지 개념이 없다. 예전엔 전체 문구 부분일치라
    거의 안 잡혔고(4건), 벡터만 쓰면 '늦게' 만 닮은 글이 왔다. 낱말을 다 담은 글은 정밀하다.
    한국어 낱말은 한국어 원문 부분일치, ASCII 는 tsquery 하나로 && — expansion_clauses 와 같은 경로.
    """
    ko, fts, params = [], [], {}
    for i, tok in enumerate(t for t in tokens or () if t and t.strip()):
        t = " ".join(tok.split())
        if _HANGUL_RE.search(t):
            name = f"{prefix}_k{i}"
            ko.append(f"v.content_original ILIKE :{name}")
            params[name] = _like_pattern(t)
        elif not _NON_ASCII_RE.search(t) and fts_safe(t):
            name = f"{prefix}_f{i}"
            fts.append(f"plainto_tsquery('english', :{name})")
            params[name] = t
    parts = []
    if ko:
        parts.append("(v.language_detected = 'ko' AND " + " AND ".join(ko) + ")")
    if fts:
        parts.append(f"{_FTS_DOC} @@ ({' && '.join(fts)})")
    if not parts:
        return None
    return " AND ".join(parts), params


def token_like_clause(token: str, name: str):
    """한 낱말이 원문·번역문 어디든 있는가 — 순위 가산용(필터가 아니다). 한글 낱말은 원문만(번역문은 영어라
    늘 거짓이고 후보마다 번역문을 풀기만 한다, SG-19)."""
    if _HANGUL_RE.search(token or ""):
        return f"(v.content_original ILIKE :{name})", {name: _like_pattern(token)}
    return (f"(v.content_original ILIKE :{name} OR v.content_translated ILIKE :{name})",
            {name: _like_pattern(token)})
