# cae00 요청 SFO-1 ~ 5 · HP-71 — 사실 · 결정 (2026-11-25)

## 사실 (코드 · 합성 거울로 확인)

- 과제 → 프로젝트는 과제 속성 `project`, 개발모델 → 과제는 모델 속성 `task` 다 — 키트의 선(`<타입>__relations`)이 아니다. 선은 PLM
  색인이 쓰지 않는다는 결정(2026-11-04)을 건드리지 않는다.
- `_detect_plm`(shared/ontology.py)은 개발모델 · 과제코드 · 과제명만 알아듣는다. 프로젝트 key · 옛 key · 이름 · 별칭은 못 알아들어
  남은 낱말(residual)이 되고, 증상 개념이 있으면 범위 없이 전체를 찾는다(SFO-1 의 '10,410건').
- `project_names` 는 이름 · 별칭뿐(key · 옛 key 없음)이고 주의(plm_caution)에만 쓴다 — 그것도 다른 길로 제품이 풀렸을 때만.
- 같은 줄 두 번 — 노트를 과제마다가 아니라 매칭마다 쓴다. 제품이 없는 갈래는 구간을 차지하지 않아(`spans.take` 전에 continue)
  같은 과제의 겹친 매칭(과제코드 + 과제명 · 이름의 판 접미 뗀 꼴)이 또 잡힌다. 성공 줄도 같은 꼴로 두 번 나온다.
- 모델의 과제 참조가 과제의 지금 key 와만 맞춰진다 — 과제가 이름을 바꾸거나 합쳐지면 그 모델이 빠진다(프로젝트는 옛 key 로 맞춘다).
- resolve_product_keyword 는 PLM 을 풀기에 쓰지 않는다(주의만) — 과제코드 · 과제명 · 개발모델명도 no_catalog_match.
- semantic_search 의 키워드 물러남(개념 · 벡터 · product_code 없음)이 notes 를 갈아 끼워 PLM 줄이 사라진다.
- dev 에는 진짜 PLM 거울이 없다(토큰 없음) — 시험은 합성 거울로.

## 결정

- **worktree · 가지에서** — main 작업 트리는 dev 서비스가 바인드한다. 5차 동결이라 push 는 해제 뒤.
- **프로젝트는 key · 옛 key · 이름(세 글자 이상)만 알아듣는다, 별칭은 안 쓴다** — 코어의 프로젝트 별칭은 마케팅명이라
  'Galaxy S26' 같은 제품 이름 질의를 PLM 이 가로채 범위가 바뀐다. 별칭은 지금처럼 주의(SG-04 `project_names`)에만.
- **프로젝트 감지는 제품 감지가 다 끝난 뒤, 남은 자리에만** — 이름이 제품 이름과 같아도 제품이 먼저(시험 고정).
- **푼 길은 해석에 남긴다** — `plm_matches`(core_key{type_slug,key} · matched_by · via · products · raw, 프로젝트는
  matched_on key|old_key|label) · `plm_unresolved`(core_key · matched_by · reason · detail) · `plm_projects`. 응답 칸은
  `ontology.core_route(it)` 하나로 짓는다(SFO-3 에서 plm_route · core_matches 를 core_route · resolutions 로 바꿈 — 사전으로
  푼 것도 담아 'core' 만이 아니다). 과제 쪽 core_key 는 과제 자신(ODB 는 프로젝트를 주지만 SignalForge 는 과제가 더 좁다).
- **matched_by** — project · task_code · task_name · model_code(모델코드 · 양산모델코드 칸이 맞음) · dev_model(개발모델
  key · 이름이 맞음). 요청서 목록(task_code · task_name · project · model_code · marketing_name)에 dev_model 을 더했다 —
  ODB 에는 개발모델명 길이 없다. marketing_name 은 SFO-3 에서(카탈로그 · 별칭으로 푼 것).
- **못 풀면 넓히지 않는다(검색)** — `_scope_stop`:
  - 늘 0건 + reason `in_core_no_local_data` — 제품에 못 이은 프로젝트, PLM 으로 푼 제품이 이 서버 카탈로그에 없음. 둘 다
    다른 제품 범위(카탈로그에 있는 제품 코드 · 세대)가 없을 때. 브랜드 · 기기 범위는 '다른 범위' 로 치지 않는다(프로젝트 대신
    삼성 폰 전체가 된다).
  - strict — 위에 더해 못 이은 과제 · 개발모델, 카탈로그에 없는 제품(`product_not_in_catalog`)도 다른 범위가 있어도 0건.
  - 그 밖(strict 아님) — 못 이은 과제 · 개발모델은 예전처럼 남는 말로 낱말 · 뜻 검색에 물러나고, notes 에
    「… → 제품 연결 없음, 낱말 검색으로 바꿨다」 한 줄(여럿이어도 한 줄). 다른 범위가 있으면 「범위는 다른 말로 건 것만 썼다」.
  - 0건 줄은 까닭을 되풀이하지 않는다 — 까닭은 해석 줄('… 는 제품에 이어지지 않는다 — …') · `_resolve` 의 '범위에서 뺐다'
    줄에 이미 있다(같은 정보를 두 줄에 쓰면 SFO-2 의 '같은 줄 두 번' 과 같은 꼴).
- **해석 줄에서 '본문 낱말로 찾는다' 를 뺐다** — 낱말로 찾을지(기본) 0건일지(strict)는 검색이 정하고 적는다.
- **키워드 물러남이 해석 줄을 버리지 않는다** — `it.notes + sc["notes"]` 를 앞에 둔다.
- **resolve 는 카탈로그 이름 → 별칭 사전 → PLM 차례** — 카탈로그 정확 일치나 별칭이 있으면 PLM 을 보지 않는다(제품 질의 그대로).
  PLM 으로 풀면 resolved_via 'plm' · resolved_codes(프로젝트는 제품 여럿) · 푼 길 칸. PLM 이 알아들었는데 못 이으면 status 는
  no_catalog_match 그대로 두고 reason · core_unresolved — status 값을 늘리면 받는 쪽 분기가 깨질 수 있다. PLM 이 알아들은 말이면
  카탈로그 이름 부분일치는 답으로 내지 않는다(후보로만).
- **주의(SG-04)도 프로젝트 key 를 본다** — `plm_caution(…, project_keys)`. 'P9 + P1 의 제품' 이면 다른 프로젝트 주의.

- **SFO-3 — 사전으로 푼 것도 푼 길에** — `name_matches`(matched_by marketing_name · via alias = 태깅의 별칭 사전, matched_by
  model_code · via model_code = 모델코드 사전, span). `core_route` 의 `resolutions` = PLM 으로 푼 것(앞) + 사전으로 푼 것(뒤),
  위 칸(core_key · matched_by · via)은 첫 resolution. via 는 '그 말에서 제품까지의 길'(ODB 는 '코어 key 까지의 길').
- **마케팅명의 core_key** — 질의의 그 자리(span)가 코어 프로젝트의 이름 · 마케팅명 별칭(`project_names`)과 겹치면 그 프로젝트.
  별칭 하나가 프로젝트 여럿이면 core_key 는 null 로 두고 `core_candidates` — 하나로 정하지 않는다(그 제품 VOC 를 가르지 못함은
  주의 SG-04 가 말한다). 코어가 모르는 표기('S25 울트라' — 코어 별칭은 'Galaxy S25 Ultra')는 null 이다. 제품 → 개발모델 →
  프로젝트로 거슬러 짐작하지 않는다 — core_key 는 맞춘 객체이지 짐작이 아니다.
- **자체 모델코드 사전으로 푼 모델코드의 core_key 는 null** — 거울의 개발모델에 그 코드가 있으면 `_detect_plm` 이 먼저 잡아
  plm_model 이 된다. 코어 `plm_model_code` 로 잇는 것은 SFO-4(운영 쪽 칸 이름 R25-Q4 뒤).
- **resolve 의 카탈로그 · 별칭 길도 칸을 싣는다** — `_name_route` 가 해석의 같은 제품 resolution 을 쓰고(core_key), 해석이 못 잡은
  카탈로그 이름이면 matched_by 만(키워드가 제품 코드면 product_code). via 는 resolve 자신의 길(catalog_name · alias).
- **과제 0 인 거울은 '없음'** — PlmIndex 는 `__len__` 이 과제 수라 과제가 없으면 거짓이다. 온톨로지 전체가 `if not plm` 으로
  쓰니 같은 뜻으로 둔다(시험 거울에는 과제를 하나 넣는다).

- **SFO-5 — 코어 key 인자** — `search_voc(core_type, core_key)` → `interpret(…, core=(타입, key))` → `_apply_core_key` 가 질의
  낱말과 따로 그 코어 객체의 제품을 범위로 건다(빈 질의여도). 타입은 plm_project · plm_task · plm_model(plm_model_code 는
  SFO-4 뒤). 옛 key 도 받는다(`PlmIndex.project_key` · `task_key`). matched_by 는 core_key.
- **코어 key 로 건 범위를 못 풀면 늘 0건** — 다른 범위가 질의에 있어도(부른 쪽이 범위를 정했다, 다른 제품으로 바꿔 찾지 않는다).
  reason 을 넷으로 가른다 — unsupported_core_type(이 서버가 못 푸는 타입) · no_core_mirror(거울 없음) · not_in_core_mirror(거울에
  없는 key — 받기가 늦었거나 지워짐) · in_core_no_local_data(있지만 이 서버 제품에 못 이음). 0건 줄의 reason 은 첫 hard 의 것.
- **core_type 과 core_key 는 함께** — 하나만 오면 reason bad_arguments. 코어 key 가 오면 semantic=False 여도 시맨틱으로(키워드
  길은 범위를 못 건다). 시맨틱이 꺼진 서버(온톨로지 import 실패)는 키워드로 넓히지 않고 0건 + reason semantic_unavailable.

## 시험 돌리기 (worktree)

- 크롤러 이미지에는 pytest 가 없다(이미지가 사용자 site 를 끈다 — SG-01 뒤) — 스크래치에 `pip install --target <dir> pytest` 해 둔
  묶음을 `--env PYTHONPATH=<dir>` 로 붙이고 `--pwd <worktree>/crawler` 로 돈다.
- worktree 에는 `crawler/data`(벡터 색인 · PLM 사본)가 없다 — 실DB 시맨틱 시험은 `EMB_DIR=<main>/crawler/data/vectors` 를 줘야
  예전과 같은 길(벡터)로 간다. 안 주면 `test_no_concept_goes_to_vector_or_keyword` 가 키워드로 가 빈손이 된다(코드 탓 아님 — main
  에서는 통과, worktree 에 EMB_DIR 을 주면 통과).
- 크롤러 시험 전체는 reports/ 에 부산물(backfill_audit.jsonl 두 줄 · daily/weekly md)을 남긴다 — 커밋 전에 되돌린다.
- 크롤러 시험 실패 넷은 이 일과 무관 — 셋(gsmarena_forum e2e · reddit_rss crawl · topic_classifier multi_label_korean)은 main
  에서도 실패, test_reports_dir_location 은 worktree 폴더 이름(SignalForge-sfo)을 'SignalForge' 로 박아 둔 시험.
- 실DB 응답 시간 시험(`test_response_time`)은 크롤러 시험 · 수집이 돌 때 차가운 캐시로 한계를 넘을 수 있다(11-25 — '발열' 6.4초 ·
  '태블릿 화면 깜빡임' 11.7초). 바로 다시 돌리면 worktree · main 둘 다 4개 통과(4.2초) — 이 일의 새 SQL 은 없다.
