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

## 2단계 — 사실 (2026-11-28)

- 조사 넷(읽기 전용 서브에이전트)이 도구 37개의 인자 뜻 · 닫힌 어휘 · 0/None 의 뜻 · 반환 칸 · 형제 도구를 모았다. 고친 것은
  이 세션이 했다.
- 기간 0 의 뜻이 도구마다 다르다 — 결함 · 분해 · 차트 · 말뭉치는 '전 기간', get_top_issues · get_country_breakdown ·
  get_voc_summary · analyze_sentiment_trend · keyword_stats · period_compare 는 '빈 결과'. 뜻을 바꾸지 않고 앞쪽은 ge=0,
  뒤쪽은 ge=1 로 막았다.
- `category` 는 두 뜻이다 — query_voc · compare_products 는 VOC 주제(voc_records.categories), 통계 · 결함 도구는 기기 종류
  (products.category). 공통 필터 설명표는 기기 종류라 두 도구는 Args 에 주제 설명을 따로 둔다(빠뜨리면 조용히 기기 종류
  설명이 붙는다). 데이터의 주제는 열셋 + model_mention · others · non_target.
- FastMCP 1.x 는 반환 모델로 결과를 검증해 structuredContent 를 짓는다 — 칸 타입이 실제와 어긋나면 호출이 실패한다
  (str 칸은 date 객체를 받지 않는다, int 칸은 소수 Decimal 을 받지 않는다). 글 결과는 그대로 JSON 글로 나간다.
- `list` 반환 도구는 행마다 글 덩어리 하나로 나가 0건이면 내용이 아예 없었다 — 객체로 바꾸며 함께 풀렸다.
- 경보 표(alert_events)에 모든 새 일이 쌓인다 — payload.type 다섯(defect_anomaly · collection_health · operations_monitor ·
  ops_status_violation · backup_fail)과 룰 엔진 규칙(type 없음). payload 는 평균 172자 · 최대 3KB 라 detail 로 그대로 싣는다.
- query_voc '발열' 이 처음 한 번 55초 — 차가운 캐시의 순차 스캔(dev shared_buffers 128MB)이었고 다시 재면 1.5초, 정렬에 붙인
  id 동률 깨기와는 무관(두 정렬 모두 1.6초).

## 2단계 — 결정

- **결과 모델은 tools/outputs.py 한 곳** — 추가 칸 허용 · 모든 칸 기본값(빠진 칸은 null). 타입은 확실한 것만(날짜 객체는
  date · datetime, 이미 글자로 바꾼 날짜는 str, 꼴이 도구마다 다른 차트 raw 는 Any). 큰 글 다섯은 게이트웨이 M2 까지 달지 않는다.
- **목록 도구 7개는 객체로** — {total, truncated, results}. 집계는 SQL 창 함수(count(*) OVER ())로 센 전체 수, 행 도구
  (query_voc · get_engagement_leaders)는 한 건 더 읽어 잘렸는지만 알고 total 은 다 실었을 때만 준다(잘린 쪽은 null — 전체
  건수를 세면 질의가 두 배가 된다). 포털 · 게이트웨이에는 이 꼴을 읽는 코드가 없다(이름 참조뿐 — 확인함).
  ⚠ 그 밖에 읽는 곳이 둘 있었다(포털 71 이 알려 옴) — HWAXRisk(브리프 E10 VOC 근거 — get_top_issues)와
  HWAXAgentServer(심의 환기 — get_top_issues · query_voc). 옛 배열 꼴만 읽어 근거가 소리 없이 비었고, 포털이 둘 다 results
  를 받게 고쳤다(에이전트 서버 1038241). 결과 꼴을 바꿀 때는 이 둘까지 도구 이름으로 grep 한다. 같이 찾은 것 — HWAXRisk 는
  get_top_issues 에 없는 인자 window_days 를 넘겨 SDK 1.x 가 조용히 버리고 기본 30일로 돈다(포털에 알림).
- **query_voc 쪽 넘김** — cursor 는 앞 쪽의 next_cursor(오프셋, 6자리까지). 같은 발행일의 순서가 쪽마다 흔들리지 않게 id 로
  동률을 깬다.
- **경계를 넘는 인자는 거절** — 예전에는 함수 안에서 min(limit, 100) 으로 조용히 잘랐다. 계약 C4 대로 스키마가 막고 상한을
  설명에 적는다(도구 오류로 돌아간다). 모르는 축 · 구간도 예전처럼 기본값으로 바꾸지 않고 거절된다.
- **바뀐 것 목록(R1)은 alert_events 하나로** — 새 VOC 행은 너무 잦고(하루 수만) 결함 표는 created_at 을 다시 쓰지 않아 커서로
  못 읽는다. 커서는 id(덤프 서버는 fired_at 순서와 다르다), since 없이 부르면 가장 최근 limit 건, 숨김 규칙
  (ALERT_HIDDEN_RULES)은 REST 처럼 뺀다. 이름 — defect.spiked · collection.degraded · operations.alerted ·
  ops_status.violated · backup.failed · alert.fired.
- **입구(R3)는 corpus_overview** — 결과에 guide(업무 다섯의 입구 · 차례 · 규칙 넷), 서버 instructions 가 처음 부를 도구로 짚는다.
  같은 글을 workflows.md(R2)에 둔다.
- **조사에서 나온 결함은 설명을 참으로 만드는 만큼 고쳤다** — 설명이 「글 수」 · 「최근 글부터」 · 「그 주제만」 이라 말하려면
  셈 · 순서가 그래야 한다. compare_products 의 category 는 '그 주제가 붙은 글의 모든 주제' 에서 '그 주제만' 으로 바꿨다(설명의
  '범위 한정' 을 그렇게 읽었다). 설명 밖의 것(lat.md 의 낡은 MCP 문서 · 위기 사례 목록 · 공통 필터를 다 받지 않는
  defect_profile)은 두었다.
- **effective_platforms 설명** — '2 미만이면 신뢰도 낮음' 만 두면 '크면 믿을 만하다' 로 읽힌다. 탐지기는 기사 퍼나르기가 1/HHI
  를 키운다는 걸 보고 독립 출처 수로 바꿨다 — 설명과 note 에 그 한계를 적었다.
