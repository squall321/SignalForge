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

## Phase 2 설계 결정

### D5. 렉시콘 + 근접 페어링 (LLM 아님)
40만행을 일관되게 처리해야 하고 비용/재현성이 중요하다. 부품·증상을 각각 매칭한 뒤
**증상마다 가장 가까운 부품**과 짝짓는다. 단순 교차곱은 노이즈가 폭발한다.

### D6. 부착(attachment) > 최근접
"gap in the hinge and a green line on the screen" 에서 최근접만 쓰면 green_line 이
더 가까운 hinge 에 붙는다. 영어는 증상 뒤 전치사로 부품이 붙으므로(_ATTACH_WINDOW=40)
**증상 직후 부품을 우선**한다. 단 문장 경계를 넘으면 안 된다
("hinge collected dust. Also the camera..." → dust 가 camera 에 붙던 오류).

### D7. 단어경계는 선택이 아니라 필수
경계 없이 두면 'os' 가 cost/most/position/closed 안에서, 'heat' 가 wheat,
'frame' 이 timeframe, 'dust' 가 industry 안에서 매칭된다. 실측으로 software 가
crease/dust 를 3,400여건씩 잘못 흡수했고, 경계 적용 후 43,451 건으로 허위 19,425 건이
제거됐다. 새 패턴 추가 시 반드시 경계를 확인할 것.

## Phase 3 설계 결정

### D8. 세대 매핑은 코드 규칙에서 유도
products.code 가 PREFIX+숫자+SUFFIX 규칙이라 숫자를 1 내려 카탈로그에 실재하면 연결한다
(GZF8→GZF7, GS26U→GS25U, AP16P→AP15P). 175/389 종이 연결됐고 나머지는 최초 세대이거나
단발 모델이라 정상이다. 예외가 생기면 predecessor_code 를 직접 수정하면 된다.

### D9. 뷰는 product_id 가 아니라 링크 기반
v_voc_lifecycle 을 voc_product_links 위에 세워 비교글 언급까지 포함시켰다(Phase 1 활용).
role 컬럼이 있으므로 primary 만 보고 싶으면 필터하면 된다.

### 한계 — 과거 세대 표본
폴드7 의 주차별 표본이 24~57건으로 매우 작다. 과거 시점 수집 깊이가 얕기 때문이며,
**주차별 비교는 신뢰도가 낮고 누적(0~8주) 비교를 봐야 한다**. 이 한계는 과거 backfill
깊이가 개선돼야 해소된다.

### 첫 성과
"폴드8 부정 +195% 급등" 은 상당 부분 **출시 효과**였다. 세대 정규화 시 폴드8 12.9% vs
폴드7 13.3%(-0.3pt)로 사실상 동일. 반면 **GS26 은 GS25 대비 +2.6pt 실제 악화**로,
정규화가 없었으면 폴드8 에 가려 놓쳤을 신호다.
