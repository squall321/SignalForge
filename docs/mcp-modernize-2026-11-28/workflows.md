# SignalForge MCP 길 안내 — 대표 업무 다섯 (계약 R2 · R3)

게이트웨이 목적 표(`tool_purposes.json`)와 skill 의 씨앗으로 보낸다. 도구 38개는 모두 읽기 전용이라 「확인할 쓰기」 는 없다.
**앱 입구(R3)는 `corpus_overview`** — 처음 부르면 말뭉치 규모 · 기간 · 플랫폼 · 나라 · 언어 · 태깅 · 결함률과 함께 `guide`(아래 표와
같은 업무 다섯과 규칙)를 준다. 서버 instructions 도 처음 부를 도구로 이것을 짚는다. 같은 내용의 정본은 `mcp-server/server.py` 의 `_GUIDE`.

| # | 목적 | 차례 | 입구 도구 | 확인할 쓰기 |
|---|---|---|---|---|
| 1 | 한 제품의 결함 양상과 근거 | `resolve_product_keyword`(제품 코드 확정) → `defect_profile`(부품 × 증상 × 심각도) → `defect_timeline`(언제 시작 · 가속) → `defect_breakdown`(어느 플랫폼 · 나라 · 양상, 1인칭 비율) → `defect_evidence`(1인칭 원문 근거) | `resolve_product_keyword` | 없음 |
| 2 | 증상 · 주제로 VOC 찾기와 규모 | `search_voc`(뜻 검색 — 표기 · 언어가 달라도 같은 결과) → `keyword_stats`(총량 · 추세 · 쏠림) → `voc_breakdown`(축 분해 · 비중) | `search_voc` | 없음 |
| 3 | 급등 · 위기 감시 | `list_changes`(지난번 뒤로 새로 생긴 일 — 커서) → `defect_anomalies`(결함 급등의 자세한 값) → `lifecycle_compare`(직전 세대 같은 주차) → `alert_check`(지금 걸린 감성 · 수집 경보) → `period_compare`(전기 대비 늘고 준 것) | `list_changes` | 없음 |
| 4 | 경쟁 제품 비교 | `resolve_product_keyword`(코드 둘 이상) → `compare_products`(주제별 감성) → `voc_breakdown(by="product", include_mentions=True)`(비교글까지 센 건수 · 부정 비율) → `period_compare(by="brand")` | `resolve_product_keyword` | 없음 |
| 5 | PLM 프로젝트 · 과제 · 개발모델로 VOC 찾기 | `get_ontology`(PLM 거울이 켜졌나 — `plm.active`) → `resolve_product_keyword`(프로젝트 key · 과제코드 → 제품, 푼 길) → `search_voc(core_type=…, core_key=…)` 또는 `strict=True`(못 풀면 0건 + reason) | `get_ontology` | 없음 |

## 규칙 (guide.rules)

- 제품 코드는 짐작하지 않는다 — `resolve_product_keyword` 로 확정해 `product_code` 로 넘긴다(표기마다 낱말 검색 건수가 크게 흔들린다).
- 0건은 'VOC 없음' 이 아니다 — 결과의 `reason` · `notes` · `status` · `pipeline_status` 가 까닭(범위를 못 걸었다 · 태깅이 비었다 ·
  기간에 없다 · 결함 추출이 안 돌았다)을 준다.
- 잘린 목록은 `truncated` 로 안다 — 전체 수를 말할 때는 `total` · `groups` 를 인용한다. `query_voc` 는 `next_cursor` 로 다음 쪽.
- `category` 는 도구마다 뜻이 다르다 — `query_voc` · `compare_products` 는 VOC 주제(battery · camera …), 통계 · 결함 도구는 기기
  종류(phone · watch …).
- 결과에 `ignored_args` 가 있으면 그 도구에 없는 인자를 넘긴 것이다 — SDK 1.x 는 모르는 인자를 버리고 기본값으로 돌므로 인자
  이름을 고쳐 다시 부른다(서버 로그에도 경고 한 줄이 남는다).

## 바뀐 것 목록 (R1)

`list_changes(since, limit, events)` — `alert_events` 를 id 커서로 읽는다. 게이트웨이는 받은 `next_cursor` 를 다음 `since` 로 넘긴다
(since 없이 부르면 가장 최근 limit 건과 그 마지막 id). 이벤트 이름은 `signalforge.<대상>.<일>`.

| 이벤트 | 무엇 | 원천 |
|---|---|---|
| `signalforge.defect.spiked` | 결함 급등(제품 × 부품 × 증상) | 결함 급등 탐지기 |
| `signalforge.collection.degraded` | 사이트 수집 이상(발행일 결측 · 0건 등) | 수집 상태 점검 |
| `signalforge.operations.alerted` | 운영 지표 경보(예: LLM 근거율) | 운영 감시 |
| `signalforge.ops_status.violated` | 운영 상태 위반 | 운영 상태 점검 |
| `signalforge.backup.failed` | 백업 검증 실패 | 백업 검증 |
| `signalforge.alert.fired` | 지표 규칙 경보(룰 엔진) | 경보 규칙 |

- 커서는 늘 id 다 — 덤프로 받는 서버는 병합이 원천의 시각(`at`)을 두고 새 id 를 붙여, 시각 순서와 id 순서가 다르다.
- 이 서버가 화면에서 숨긴 규칙(`ALERT_HIDDEN_RULES`)은 이벤트로도 내지 않는다.
