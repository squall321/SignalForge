# VOC 고도화 체크리스트

3단계 순차 진행. 각 단계는 **검증 통과 후** 다음으로 넘어간다.

## Phase 1 — 다대다 제품 태깅 (voc_product_links)

근거: Fold8 언급 글이 S26U로 1,280건·S26으로 402건 태깅되어 비교글 신호가 소실.
iPhone 언급 30,927건 중 태깅 2,896건으로 경쟁사 분석 불가.

- [x] 기존 `product_id` 사용처 파악 (백엔드 20파일·MV 7종 → 비파괴 설계 확정)
- [x] `infer_all_product_codes()` — 전체 매칭 + **span 겹침 억제**
      ("Galaxy S26 Ultra"가 GS26U·GS26 둘 다 잡히는 문제 해결)
- [x] 역할 판정 — primary / compared(비교 마커 존재) / mentioned
- [x] 단위 테스트 (겹침·비교·다중제품·회귀)
- [x] alembic 0029 — `voc_product_links` 테이블 + 인덱스 + FK
- [x] 백필 스크립트 (멱등·배치·진행로그)
- [x] 검증 — Fold8/iPhone 링크 증가, primary 는 기존 product_id 와 일치

## Phase 2 — 결함 구조화 추출 (voc_defects)

근거: 현재 최선이 "hinge 248 / dust 100" 단어 카운트. 부품·증상·조건·심각도가 없음.

- [x] 결함 택소노미 정의 (component / symptom / severity)
- [x] 렉시콘 기반 추출기 — 전 코퍼스 결정적 처리
- [x] 단위 테스트
- [x] alembic 0030 — `voc_defects` 테이블 + 인덱스
- [x] 백필 + 검증 (Fold8 힌지/이물 구조화 레코드 확인)

## Phase 3 — 라이프사이클 정규화

근거: 신제품은 출시 직후 원래 급등. 폴드8 6주차 vs 폴드7 6주차로 비교해야 진짜 신호.
released_at 은 0027 로 전 제품 채움 완료 → 지금 가능.

- [x] 세대 매핑 (제품 → 이전 세대) 정의
- [x] lifecycle_week 산출 + 세대간 동일 주차 비교 뷰/함수
- [x] 검증 — 폴드8 vs 폴드7 동일 주차 부정률 비교 산출
