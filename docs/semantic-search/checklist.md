# 온톨로지 기반 시맨틱 검색 (2026-10-17)

## 계획

**목표.** 검색어를 그대로 글자 비교하지 않고 **뜻(개념)으로** 바꿔 찾는다. 같은 뜻이면
표기·언어가 달라도 같은 결과가 나와야 한다 —
'S25 울트라 발열' = 'Galaxy S25 Ultra overheating' = 's25u 발열 문제'.

**방법.** 이미 정밀도를 다듬어 둔 사전들을 하나의 개념 그래프(온톨로지)로 묶는다.

| 개념 | 출처 | 말뭉치 쪽 주석 |
|---|---|---|
| 제품·세대(시리즈)·브랜드·기기종류 | product_match(라이브+레거시), products 카탈로그 | voc_product_links |
| 부품 16 · 증상 32 (+심각도·함의 부품) | nlp/defect_extract | voc_defects |
| 주제 13 | nlp/categorizer | voc_records.categories |
| 시장(국가·언어) · 채널(플랫폼) | 국가 사전, platforms | country_code·language_detected·platform_id |

질의 → 개념 인식(겹침 해소) → 확장(제품→세대, 증상→함의 부품·주제, 다국어 동의어) →
말뭉치의 개념 주석(정확) + 동의어 본문 일치(재현) 로 후보 → 관련도 점수 순 정렬.
개념이 하나도 없으면 기존 키워드 검색 그대로.

**단계.**
- 1단계(이번): 온톨로지 + 질의 해석 + 시맨틱 검색, search_voc 기본 경로로 연결.
  새 인프라 없음 — MCP 컨테이너에 /crawler 바인드 한 줄만 추가(사전을 복제하지 않기 위해).
  S25 세대 = S25·S25+·S25 Ultra·S25 FE (S25 Edge 는 카탈로그에서 S25 로 흡수돼 있다).
- 2단계(결정 필요): 임베딩 벡터 검색 — 사전에 없는 바꿔 말하기('폰이 손난로 같다')까지.
  GPU(RTX 5070 Ti 16GB)는 있으나 pgvector·임베딩 모델이 없어 인프라 결정이 필요하다.

## 체크리스트

- [x] 실측·자원 확인 — pgvector 없음, GPU 있음, 사전·주석 테이블 구조 확인
- [x] shared/keyword_match — 동의어 묶음(expansion_clauses·fts_groups_clause·like_any_clause), 불용어 구절 차단
- [x] shared/ontology.py — 개념 레지스트리·관계·라벨·질의 해석(interpret)·질의 약칭(노트9·워치4)
- [x] mcp-server/tools/semantic.py — 해석 → 범위(적응형 3모드) → 후보·점수 → 결과·근거, 시간 초과 시 주석만
- [x] MCP 도구 — search_voc 기본 시맨틱, semantic_search(해석+결과), get_ontology (35 → 37)
- [x] up.sh — sf-mcp 에 /crawler 바인드, 재기동 (바인드 없으면 키워드 검색으로 강등)
- [x] 테스트 — test_ontology 32 · test_semantic_search 11 · keyword 동의어 6, mcp 127 passed
- [x] 문서 — context-notes, mcp README, cae00 적용 항목
- [x] 커밋 · push · 메모리 (9fad381)
