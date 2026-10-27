# cae00 수정 요청 SG-01~18 처리 (2026-10-27)

요청 — HWAX Portal 운영(cae00)이 `a216d82` 기준으로 낸 SG-01~SG-18. 사용자 결정 SG-18 = (가) 기본으로 가림.

## 체크리스트

- [x] SG-01 이미지 — mcp·crawler requirements 에 numpy(+mcp<2 · httpx), def 에 PYTHONNOUSERSITE, lock 으로 판 고정, 세 이미지 재빌드 · 검증(46c43fa)
- [x] SG-01 이미지 교체 · 재기동(워치독 락, 'semantic import ok') · Drive 업로드(sif-20261027-232447Z · latest, sha256 일치)
- [x] SG-01 문구·스모크 — `_SEM_NOTE` 예외 종류별, 기동 로그 'semantic import ok', server.py 실제 import 시험(89529b0), up.sh 기동 스모크(5c66b45)
- [x] SG-02·15·17 — merge-from-drive 가 id 를 code 로 번역, 카탈로그 code upsert, 링크·결함 병합, 옛 정수 복사 행 제자리로, 표본 대조 가드, 뷰 REFRESH, 겹침 잠금, 합성 DB 시험 11/11(396af7c)
- [x] SG-02 실규모 병합 시험(dev 덤프 123만 행 → 카탈로그를 돌린 사본) — 첫 27분 · 두 번째 6분 · 표본 5,000/5,000
- [x] SG-02 도구 — 태깅 0 · 키워드 >0 이면 no_tagged_records + 키워드 안내(89a0583)
- [x] SG-03·09·12 — PLM 거울: task_code·별칭 → by_code, key → by_name, model_code 우선, renamed_from(91e3e84)
- [x] SG-04 — 카탈로그 시드(dev 카탈로그 897·101, 234d550), resolve_product_keyword 별칭(제품 매처) 폴백(89a0583), 도구 설명 예시 ⊆ 카탈로그 시험
- [x] SG-05 — 결함 도구 pipeline_status · 결함률 null(89a0583)
- [x] SG-06 — up.sh Redis 없음 ⚠ 크게(b6d8c7b), 뷰 미적재 → '집계 준비 중'(89a0583), 병합이 뷰를 채운다
- [x] SG-07 — 키워드 auto 0건 → 낱말·FTS 폴백, search_voc 는 늘 객체(89529b0)
- [x] SG-08 — site_health 전 기간 최신값 · stale 12h 공유 · 워커 생존(cee09d0)
- [x] SG-10 — 이슈 순위에서 메타 태그(comparison·model_mention) 분리(fe2ecca) — 분류기 규칙은 방침 결정 몫으로 남김
- [x] SG-11·18 — MCP 출력 가림(작성자·계정·IMEI·일련번호·메일·전화, b5e2fa6)
- [x] SG-13 — scripts/plm-sync.sh + 안내서 §2(4ac0161)
- [x] SG-14 — PG_FRESH 와 '새 pgdata' 분리, T10(8f15aed)
- [x] SG-15·16·17 답 · cae00 적용 문서(DEPLOY_CAE00_2026-10-27.md) · 전체 시험 · push · 메모리
