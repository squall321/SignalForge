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

## Phase 4/5 설계 결정 (2026-09-06)

### D10. 원안(매체 복제 증폭 제거)을 실측으로 기각
"기사 1건이 50개 매체에 복제"가 전제였으나 교차플랫폼 완전중복은 125건(0.038%)이고
최대 3개 매체까지만 퍼진다. safety 결함이 매체 복제로 부풀려졌다는 가설도 기각
(중복 붕괴 1.94%, 교차플랫폼 0건). 매체 다운웨이팅은 Spearman 0.935 로 무의미.
→ **플랫폼 집중도(HHI)** 로 재정의. 이쪽은 n≥20 조합의 46%를 10계단 이상 이동시킨다.

### D11. RuleEngine 을 경유하지 않는다
alert_rules/RuleEngine 은 metric_path 로 **전역 스칼라 1개**를 고정 임계값과 비교하는
구조라 (제품×부품×증상) 엔티티 차원을 표현할 수 없다. 게다가 평가 루프
evaluate_alert_rules 는 beat 미등록 + psql 의존 + 포트/SSO 불일치의 3중 고장 상태다.
살아 있는 유일한 패턴인 collection_health 의 **직접 INSERT + payload 단위 cooldown** 을
복제하고, alert_rules 에는 대표 룰 1행만 두어 severity·threshold·cooldown 을 운영자가
UI 로 조정할 수 있게 했다.

### D12. 절대건수가 아니라 점유율(share)
폴드7 의 주차별 표본이 24~57건인데 폴드8 은 수천건이다. 과거 세대일수록 수집 깊이가
얕아 세대간 절대건수 비교는 무효다. share = 결함건수 / 같은 창 그 제품 전체 VOC.

### D13. 창 28일은 타협이 아니라 실측 결과
7일 창에서 min_count=10 을 넘는 (제품×부품×증상) 조합이 9개뿐이었다(788 중). 14일도 28개.
28일에서 72개가 되어야 통계가 성립한다. 근본 원인은 **결함행의 31%만 제품 링크를 가진 것**
(11,133/35,700)이라, 링크 커버리지가 올라가면 창을 줄일 수 있다.

### 조사 중 발견한 기반 고장 (모두 컨테이너화 2026-07-07 부작용)
- MV 7종 refresh 가 psql subprocess 인데 sif 에 psql 이 없어 2개월 동결(활성 VOC 의 65%가
  어느 MV 에도 없었다). → _refresh_mv 로 외부 프로세스 의존 제거.
- crawler/alerts/rules.py(sentiment_drop·site_dead·issue_spike)는 reports/alerts.log 경로가
  깨져 침묵 중. **이미 작성된 제품 부정률 급등 SQL 이라 재활용 후보**(미착수).
- 번역기가 Google 오류 페이지를 번역 결과로 저장 3,918행.

### 미해결
- :8013 MCP 는 apptainer 인스턴스가 아니라 22일 된 호스트 프로세스(PID 36706)가 서빙한다.
  up.sh 의 sf-mcp 헬스체크가 포트만 보므로 인스턴스가 없어도 "정상(skip)" 로 판정된다.
  신규 MCP 도구 반영에는 사용자의 start_mcp.sh 재기동이 필요하다.
- backend REST / 프론트 표출 미착수.

## 적대적 검증 결과 (2026-09-06) — 초판 탐지기의 결함 27건 확정

5개 렌즈로 반박을 시도하고 발견마다 독립 회의론자가 재검증했다(39건 중 27건 확정).
**초판이 발화한 8건 중 다수가 오탐이었다.** 확정된 핵심 원인과 조치.

### D14. voc_defects 에 제품 차원이 없다는 사실을 과소평가했다
결함은 (voc_id, component, symptom) 키라 문서 단위다. 이걸 voc_product_links 로 role
무관 조인하면 **문서에 언급된 모든 제품**으로 팬아웃된다. 실측으로 결함 보유 VOC 의
29%가 2개 이상 제품에 링크(최대 8개)돼 있었고, 인도 Galaxy S26 폭발 기사가 말미
"S25+ 사례" 한 줄 때문에 GS25P fire 근거 14건 중 6건이 됐다. → role='primary' 로만 집계.

### D15. 1/HHI 독립성 가드는 신디케이션에 대해 정확히 반대로 작동한다
뉴스 1건을 매체가 많이 받아쓸수록 플랫폼 분포가 넓어져 가드를 더 잘 통과한다.
'칭다오 S25+ 발화' 1건이 10개 매체·5개 언어로 복제돼 eff_platforms=8.16 을 만들었고
safety 라 critical 로 승격됐다. 실제 서로 다른 사고는 3~4건이었다.
→ platforms.kind(0034) 신설, **독립 제보(community/marketplace/official) 소스 수**로 교체.
   플랫폼 다양성이 아니라 '매체가 아닌 곳이 몇 군데인가'를 봐야 한다.

### D16. 소표본 baseline + 순진한 Poisson z 는 유의성을 만들어낸다
z=(cnt-expected)/sqrt(expected) 는 baseline 을 오차 없는 상수로 가정한다. 실제
baseline 이 1건/58문서, 2건/41문서였고 Fisher 정확검정 p=0.18/0.22 로 무의미했는데
z=7.57/5.26 을 보고했다. ±1건이 발화 여부를 뒤집었다.
→ MIN_BASELINE_TOTAL=200 하한 + **두 비율 검정**(pooled SE). 작은 baseline 은 SE 가
   커져 자동으로 눌린다. baseline 0 은 1/n floor 대신 rule of three(3/n).

### D17. cooldown 은 beat 주기보다 길어야 한다
cooldown 21600 == beat 21600 이라 밀리초 지터가 발화/스킵을 정하고 최대 억제율이 50%였다
(동일 설계인 collection_health 실측: 연속 발화쌍의 56.6%가 바로 다음 tick). → 86400.

### D18. 렉시콘 오탐 4종 (결함 레코드의 24.5%)
`hang` 선행 경계 누락으로 'c-hang-ing' 매칭, drain 의 'battery life' 리터럴(리뷰 기사
대부분에 등장하는 중립 표현), 부정 처리 부재('scratch-resistant'·'Not a scratch'·
'less lag'), 'dead simple' 의 non_functional 승격. → 경계 수정 + _is_negated 신설.
재추출로 43,291→32,667.

### D19. MV 폴백 판별이 catch-all 이었다 (내가 도입한 위험)
`"concurrently" in str(exc)` 는 SQLAlchemy 가 예외에 `[SQL: ...CONCURRENTLY...]` 를
붙이므로 **모든 statement 오류**를 통과시킨다. 일시적 오류 한 번에 ACCESS EXCLUSIVE 를
잡는 blocking REFRESH 로 떨어지고 status=ok 로 보고됐다.
→ sqlstate 55000 + 'cannot refresh materialized view' 로만 판별.
   AUTOCOMMIT 주석도 정정했다(트랜잭션 제약이 아니라 **커밋**이 이유다).

### D20. 감시 계층이 죽은 것이 refresh 가 죽은 것보다 중대했다
collect_mv_stats 도 psql 의존이라 즉사 → MV 동결 2개월을 아무도 몰랐다. 같은 방식으로 복구.

### 남은 한계 (해결 안 됨)
- 렉시콘은 **실제 고장과 '구매 전 우려'를 구분하지 못한다**("Hinge Concerns (Possible
  New Owner)" 도 결함으로 계상). 의도/양상 분류가 필요하다.
- primary 역할이 관련성이 아니라 PRODUCT_PATTERNS 목록 순서로 정해진다(구체성·최신순).
  본문 주제와 무관하게 첫 매칭이 primary 가 되는 구조적 한계.
- alert_events 보존/정리 정책 없음(collection_health 가 하루 ~700행 생성).
- 다중비교 보정 없음(수십 조합 동시 검정).
