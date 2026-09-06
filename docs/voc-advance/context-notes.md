# VOC 고도화 컨텍스트 노트

작업 중 내린 결정과 그 이유. 다음 세션이 재유도 없이 이어받기 위한 기록.

## 2026-09-05 착수 배경

직전 세션에서 태깅 버그 2건을 고치며 드러난 구조적 한계가 이 작업의 출발점이다.
- 최신 5종(워치9·A57·A37·A27·F25) product_match 패턴 누락 → 태깅 0 (커밋 322a80f 로 복구)
- GW1 오태깅 잔재 82건 (alembic 0028 로 제거)
- **남은 근본 한계** — VOC 1행 = 제품 1개. 이건 패턴 수정으로 못 고친다.

## Phase 1 설계 결정

### D1. 기존 `voc_records.product_id` 를 건드리지 않는다 (비파괴)
백엔드 20개 파일과 MV 7종(category_daily·country_daily·galaxy_master_timeline·
kg_edges_daily·kpi_overview·mv_voc_daily·platform_health)이 product_id 에 의존한다.
컬럼을 다대다로 옮기면 이 전부를 동시에 고쳐야 하고 회귀 위험이 매우 크다.
→ **product_id 는 primary 제품으로 유지**, 다대다는 별도 테이블로 추가.
   기존 쿼리·대시보드는 그대로 동작하고, 새 분석만 링크 테이블을 쓴다.

### D2. primary 링크도 junction 에 함께 넣는다
`voc_product_links` 만 보면 그 글의 제품 관계가 완결되도록 role='primary' 행도 포함.
product_id 와 중복 저장이지만, 조인 한 번으로 끝나 쿼리가 단순해진다.
정합성은 백필/저장 시 primary = infer_product_code 결과로 맞춘다.

### D3. span 겹침 억제가 필수
단순히 "모든 패턴 매칭"을 하면 오염된다. `_E = (?![0-9a-zA-Z])` 는 뒤 문자가 공백이면
통과하므로 **"Galaxy S26 Ultra" 가 GS26U 와 GS26 을 동시에** 매칭한다.
→ 매칭 위치(span)를 기록하고, PRODUCT_PATTERNS 우선순위 순으로 훑으며
   **이미 채택된 span 과 겹치면 버린다**. 결과적으로 가장 구체적인 모델만 남는다.
   "S26 Ultra vs Fold8" 처럼 span 이 안 겹치면 둘 다 살아남는다(의도한 동작).

### D4. 역할(role) 판정 규칙
- `primary`  — 우선순위 최상위 매칭(= 기존 infer_product_code 결과와 동일)
- `compared` — 비교 마커(vs, 대비, 비교, versus 등)가 본문에 있을 때의 non-primary
- `mentioned`— 그 외 non-primary
비교 마커는 문서 단위로 판정한다(문장 단위 파싱은 과잉 — 정확도 대비 복잡도가 큼).
