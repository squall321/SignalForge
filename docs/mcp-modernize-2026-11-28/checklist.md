# MCP 현대화 — stateless · 공통 계약 (2026-11-28)

## 계획

포털 hwaxportal-71 이 사용자 지시로 모든 MCP 를 stateless 와 공통 계약(포털 `docs/mcp-modernize/contract.md`)에 맞추고 있다.
SignalForge 몫은 signalforge-7f 에 갔지만 7f 가 보안 결정 여섯의 답을 기다리며 멈춰, 71 이 c6 에 맡을 수 있으면 하라고 했다
(7f 에는 결정이 이미 적용됐고 c6 이 맡는다고 알렸다 — 7f 의 가지 · worktree 는 없었다).

1. **P1 stateless(1단계)** — `_MaskedFastMCP(..., stateless_http=True)`. SDK 1.27.1 이 받는다. 도구는 DB 조회뿐이라 세션 상태가 없다.
2. **C2 인자 설명** — 등록 길목(`_MaskedFastMCP.tool`)에서 docstring 「Args:」 절을 떼어 인자마다 `Annotated[타입, Field(description=…)]`
   로 서명에 붙인다(도구 37 · 인자 93). 도구 설명에는 그 절을 뺀 글을 넘긴다.
3. **C6 표지** — 37개 모두 읽기 전용(INSERT · UPDATE · DELETE · commit 없음 — 확인함) → 기본 `ToolAnnotations(readOnlyHint=True,
   openWorldHint=False)`(camelCase). tools/list JSON 의 열쇠 꼴을 단언하는 시험.
4. 나머지 계약 항목(C1 · C3 · C4 · C5 · C7 · C8 · C9 · P2 · P3 · R1 ~ R3)은 상태만 답한다 — 이번 판은 포털이 짚은 셋.

## 체크리스트

- [x] 1 — stateless_http=True
- [x] 2 — Args → Field(description) 헬퍼 · 설명에서 Args 절 빼기 · 공통 필터 설명표 · 아홉 도구 Args 보탬(인자 156/156)
- [x] 3 — 기본 표지(readOnlyHint · openWorldHint) — 37/37
- [x] 시험 — tools/list JSON(camelCase 열쇠 · 인자 설명 100% · 설명에 'Args:' 없음 · stateless 설정) · Args 파서
- [x] MCP 시험(계약 · 서버 기동 · 도구 설명) · dev sf-mcp 다시 띄우기 · 살아 있는 서버 확인 — 세션 헤더 0, tools/list 37 ·
      readOnlyHint 37(camelCase) · 인자 설명 156/156, 세션 없이 도구 호출 정상
- [x] 커밋 8ca86ed · main d274827 · push · 포털에 항목별 한 줄과 판

## 2단계 — 남은 계약 항목 (2026-11-28, 포털 71 이 전한 사용자 지시 「남기지 말고 최대한 많이」)

1단계(P1 · C2 · C6) 뒤 「할 차례」 로 답한 C1 · C3 · C4 · C7 · C8 · R1 ~ R3 를 이어 한다. SDK 2.x(P2)는 따로.
도구 37개의 인자 뜻 · 닫힌 어휘 · 0/None 의 뜻 · 반환 칸 · 형제 도구는 읽기 전용 조사 넷으로 모으고, 고치는 것은 이 세션이 한다.

- [x] C3 — 닫힌 어휘를 Literal(enum)로 — 축(by) · 구간 · 정렬 · 경로 · 심각도 · 양상 · 감성 · 기기 종류 · VOC 주제 · 관계 종류 ·
      미태깅 사유 · 코어 타입 · 위기 사례 · 이벤트 이름. 부품 16 · 증상 32 · 주제는 온톨로지에서 짓는다. 플랫폼 · 국가 · 브랜드는
      설명에 찾는 도구(voc_breakdown by=…)
- [x] C4 — limit · top_n · 기간 · 주차 · 개월 · min_docs · 제품 목록에 스키마 경계(ge · le · min_length · max_length), 상한을 설명에도.
      함수 안 조용한 자르기(min(limit, 100))는 뺐다 — 넘으면 검증 오류
- [x] C8 — 목록 도구 7개는 {total, truncated, results}(꼴이 바뀜 — 포털에 알림), query_voc 는 next_cursor · cursor(쪽 넘김).
      결함 · 통계 · 검색 도구에 total · truncated(voc_breakdown · period_compare 는 groups)
- [x] C7 — 결과 모델 32개(tools/outputs.py — 추가 칸 허용 · 모든 칸 선택). 큰 글 다섯(query_voc · search_voc · semantic_search ·
      get_ontology · defect_evidence)과 열쇠가 동적인 compare_products 는 달지 않음
- [x] C1 — 38개 설명을 「무엇을 · 언제 · 형제 도구 · 돌려주는 꼴 · 한도」 꼴로(1,500자 안, search_voc 2,919 → 1,086자). 날짜 · 결정
      번호 · 실측 일화는 뺐다
- [x] R1 — list_changes(since · limit · events) — alert_events 를 id 커서로, 이벤트 이름 signalforge.<대상>.<일> 여섯
- [x] R2 · R3 — workflows.md 다섯 업무 · 입구는 corpus_overview(결과에 guide, 서버 instructions 가 짚는다)
- [x] 조사에서 나온 결함 고침 — compare_products(평균 없는 주제에서 죽음 · 주제를 주면 그 주제만) · 결함 1인칭 · 세대 비교 ·
      출시 후 경과일을 글 단위로 · defect_evidence 최근 글부터 · defect_anomalies 가 새 탐지기 칸(indep_sources) · get_ontology
      'topic:battery' · Decimal 이 글자로 나가던 수(::float) · get_platform_breakdown 에 platform_code
- [x] 시험 — 계약(설명 꼴 · enum · 경계 · outputSchema · 잘림 · 커서 · 입구) · 결함 글 단위 · compare_products null · 온톨로지 앞머리 ·
      dev DB 에서 도구 38개를 실제로 불러 결과 모델 검증(46번 호출 실패 0, 잘못된 인자 넷 거절)
- [ ] MCP 시험 전부 · 커밋 · main · dev sf-mcp 다시 띄우기 · 살아 있는 서버 확인 · push · 포털에 항목별 답
