# cae00 요청 SFO-1 ~ 5 · HP-71 (2026-11-25)

## 계획

운영 박스(cae00)가 11-24 에 모은 요청 가운데 SignalForge 몫. 포털(hwaxportal-71)이 PLAN §3-4 로 보냈다. 차례 — SFO-1 + SFO-2 →
SFO-3 → SFO-5 → SFO-4(운영 쪽 답 R25-Q4 뒤) → HP-71. 5차 라운드 동결 중이라 가지(`sfo-1125`, worktree `SignalForge-sfo`)에만
커밋하고 push 는 동결 해제 알림 뒤. 시험 입력은 요청서의 자리표시(과제 A · 과제코드 C1 · 프로젝트 P1 · 모델코드 X1 · 마케팅명 M1)와
합성 거울(PlmIndex.from_rows)로 — 실제 이름은 적지 않는다.

1. **SFO-1** — 프로젝트 key · 옛 key · 이름 · 별칭을 알아듣고 프로젝트 → 과제 → 개발모델 → 모델코드 → 제품코드로 푼다. 알아들었지만
   제품이 없으면 넓히지 않고 0건 + reason. resolve_product_keyword 도 PLM 으로 푼다(core_key · matched_by).
2. **SFO-2** — 과제가 제품에 이어지지 않아 낱말 검색으로 물러날 때 그 줄을 한 번만, `strict` 인자(넓히지 않음).
3. **SFO-3** — resolve · search_voc 응답에 core_key{type_slug,key} · matched_by · via.
4. **SFO-5** — search_voc 에 core_type · core_key 인자(SFO-1 재사용).
5. **SFO-4** — 받는 타입 · PlmIndex 에 plm_model_code(코어 칸 이름은 운영 쪽 답 뒤).
6. **HP-71** — `.env.example` 에 코어 주소(포털 경로 꼴) 주석 · rootdesign 두 번인 까닭 · NO_PROXY 자리표시.

## 체크리스트

- [x] SFO-1 · SFO-2 — PlmIndex 프로젝트 역색인 · 과제 옛 key 로 모델 잇기 · `_detect_plm` 프로젝트 · 줄 한 번 · 못 풀면 0건 + reason ·
      strict · resolve 의 PLM 풀기 · 키워드 물러남이 PLM 줄을 버리지 않게 · 시험
  - [x] `PlmIndex` — `project_tasks` · `by_project`(key · 옛 key · 이름) · `project_multi_names` · `project_models()` · 모델 → 옛 과제 key
  - [x] `ontology` — `_detect_plm_projects`(제품 감지 뒤) · `plm_matches` · `plm_unresolved` · `plm_projects` · `plm_route()` ·
        `_detect_plm` 줄 · 묶음 코어 객체마다 한 번 · 이름이 과제 여럿이면 과제마다 · 주의에 프로젝트 key
  - [x] `semantic` — `_scope_stop`(0건 + reason) · `_fallback_note`(한 줄) · `strict` · 키워드 물러남이 해석 줄을 지킨다 · 응답에 푼 길
  - [x] `server` — search_voc · semantic_search 에 `strict`, 응답에 reason · core_key · matched_by · via · core_matches ·
        core_unresolved, 설명 고침 · 시맨틱 꺼짐 대체 함수도 strict 를 받는다
  - [x] `query` — resolve 의 PLM 풀기(resolved_via 'plm' · resolved_codes · 푼 길 · reason)
  - [x] 시험 — crawler `test_plm_mirror`(13) · MCP `test_ontology` 새 6 · `test_plm_route` 새 10 · MCP 전체 · crawler 전체(2,040 통과,
        실패 넷은 무관 — context-notes)
- [ ] SFO-3
- [ ] SFO-5
- [ ] SFO-4(R25-Q4 답 뒤)
- [ ] HP-71
- [ ] 시험 전체(crawler · MCP · backend) · 가지 커밋 · 동결 풀리면 main 으로 · 포털에 착지 알림 · cae00 문서
