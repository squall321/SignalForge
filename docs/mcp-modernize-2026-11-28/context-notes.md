# MCP 현대화 — 사실 · 결정 (2026-11-28)

## 사실

- SDK — 이미지 · 호스트 모두 mcp 1.27(1.x, requirements.lock 이 `mcp<2`), pydantic 2.13. `FastMCP(..., stateless_http=True)` 를 받는다.
- 도구 37개 모두 DB 조회뿐이다 — mcp-server/tools 에 INSERT · UPDATE · DELETE · commit 이 없다. 세션에 기대는 상태도 없다.
- 인자 156개 가운데 docstring 「Args:」 에 설명이 있던 것이 93개(포털이 센 수), 없는 63개는 결함 · 통계 도구 아홉의 공통 필터 ·
  기간 · 상한이다(Args 에 '나머지: 공통 필터' 로 줄여 썼다). 자유형 object 인자는 없다(C5 해당 없음).
- 등록 길목 — `_MaskedFastMCP.tool` 이 모든 도구를 개인정보 가림(`masked`)으로 감싼다. FastMCP 는 인자 스키마를 서명
  (`inspect.signature` — `__signature__` 를 먼저 본다)에서 짓는다.

## 결정

- **길목 한 곳에서** — 도구 37개의 docstring 을 고치지 않고 `_split_args` 가 Args 절을 떼어 `Annotated[타입, Field(description=…)]`
  를 붙인 서명을 감싼 함수의 `__signature__` 로 준다. 도구 설명에는 Args 절을 뺀 글을 넘긴다(설명 평균 219자).
- **공통 필터 설명표 `_COMMON_ARGS`** — 결함 · 통계 도구가 같은 뜻으로 받는 필터(product_code · component · symptom · severity ·
  modality · brand · category · country · platform · sentiment)는 정의(tools/defects._FILTER_SPECS · shared/stats_sql._SLICE_SPECS)
  를 보고 한 곳에 적었다. 도구마다 뜻이 다른 기간 · 상한 · interval · min_docs · case_code 는 그 도구 docstring 의 Args 에 적었다.
  값 목록은 실제에서 — 부품 16(온톨로지), 양상 6(nlp/modality.py), 기기 종류 10(카탈로그).
- **'나머지: 공통 필터' 같은 줄** — 같은 들여쓰기에서 인자 꼴(영문 이름)이 아니면 앞 인자에 이어 붙이지 않는다(처음 판은
  period_days 설명에 붙였다).
- **표지** — 기본 `ToolAnnotations(readOnlyHint=True, openWorldHint=False)`. 로컬 LLM · 임베딩을 부르는 검색도 바깥 세계가 아니다.
  쓰는 도구가 생기면 그 도구의 `@mcp.tool(annotations=…)` 에 따로 단다. 시험은 tools/list JSON 의 열쇠 꼴을 본다(camelCase).
- **이번 판의 범위** — 포털이 짚은 셋(P1 · C2 · C6). C1 · C3 · C4 · C7 · C8 · P2 · P3 · R1 ~ R3 은 상태만 답한다.
