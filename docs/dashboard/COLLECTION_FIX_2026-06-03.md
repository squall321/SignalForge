# 수집 복원 라운드 — 2026-06-03

워크플로우 wyvf0mfl3(진단) + w0vqr3pxx(복원) + 메인 보완.

## 1. 라운드별 결과

| 트랙 | 산출 | 검증 | 상태 |
|---|---|---|---|
| A. HN Algolia 전환 | `crawler/platforms/hackernews.py` 재작성 + tests/test_hackernews_algolia.py | `HackerNewsCrawler.crawl()` → 298건 수집 OK. DB INSERT 0 (Algolia 가 같은 인기 글 반복 — query 다양화 필요) | partial |
| B. Reddit OAuth 슬롯 | .env 슬롯 (REDDIT_CLIENT_ID/SECRET/USER_AGENT) + tests/test_reddit_oauth.py + docs/REDDIT_OAUTH_GUIDE.md | 키 미입력 시 graceful skip, 단위 통과 | pass |
| C. rule 1·2 재산정 | rule 1 (anomaly_z), rule 2 (negative_rate) 둘 다 is_active=false 처리 + rule 35 (platforms_negative_share, threshold 0.15, cooldown 3600) 신설 | 활성 4 → 2 (rule 3 + rule 35) | pass |
| D. categorizer 백필 | `crawler/nlp/categorizer.py` (12 카테고리, 한국어/영어 키워드 사전) + tests/test_categorizer.py | 단위 13/13 통과. 백필 20k 스캔 → 3,755건 분류 (18.8% hit). overall NULL 60.44%, 7d NULL 58.59% (이전 63.66% → -5pp) | partial (백필 후속 + 한국어 인포멀 표현 확대 필요) |
| E. Instiz 필터 + 노이즈 측정 | crawler/platforms/instiz.py 댓글 필터 영구화 (이전 라운드) | 24h 잠금 안내문 105건 (5505 → 105, 98% 감소) | pass |

## 2. 현재 핵심 수치 (직접 측정)

| 항목 | 실측 |
|---|---|
| voc_records 누적 | 122,399 (1라운드 126.6k → -4.2k, Instiz 5505 삭제 영향) |
| 활성 플랫폼 | 62 (1라운드 61 + HN 1 활성) / 비활성 10 |
| 1h 신규 | 80건 |
| 7d categories NULL | **58.59%** (1라운드 65.93% → -7.3pp) |
| overall categories NULL | **60.44%** |
| 24h Instiz 잠금 안내문 | **105건** (1라운드 5,505건 → 5400+ 감소) |
| 24h 알림 발화 | 313 (08시 231 + 02시 76 + 22시 5 + 10시 1 — *과거 누적*, 800 적용 후 1h 발화 0) |
| 활성 룰 | 2 (rule 3 new_term_spike threshold 800 / rule 35 platforms_negative_share threshold 0.15) |
| HN | 308 누적 (활성, Algolia 298건 받았으나 중복) |

## 3. 알림 정상화 검증

- 1h 발화: **0** (이전 6h 304건 폭주 → 임계 20→800 + cooldown 900→3600 효과)
- rule 1·2 (anomaly_z·negative_rate) 비활성 — metric 가 실제 0 만 반환했음
- rule 35 (platforms_negative_share) 신설 — 부정 비중 15% 초과 시 발화

## 4. categorizer 백필 결과

- 백필 스캔 20,000 row, 분류 적용 3,755 (18.8%)
- 한국어 인포멀 짧은 댓글 ("ㅋㅋ", "ㄹㅇ", "삼성전자 좋다") 가 카테고리 키워드 미매칭이 대부분
- 개선 방향: (a) 한국어 어미 변이 사전 확장, (b) Galaxy 모델 정규식 강화, (c) "others" 카테고리 부여 옵션

## 5. HN Algolia 동작 + 한계

- Algolia API GET /search_by_date 정상 200 응답
- 검색어 [samsung, galaxy, S25, Z Fold, Z Flip, Galaxy Watch, Galaxy Buds] 각 30건 → 약 298 RawVOC
- DB INSERT 0 — ON CONFLICT(platform_id, external_id) DO NOTHING (이미 같은 objectID 누적)
- 권고: 검색어 확장 + tags=story|comment 분리 + 시간 윈도우 `numericFilters=created_at_i>...`

## 6. Reddit OAuth 가이드

- .env 슬롯: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
- 키 입력 후 collector 가 OAuth 토큰 자동 발급 (현 미구현 — collector 본체 추가 필요)
- 가이드 문서: docs/dashboard/REDDIT_OAUTH_GUIDE.md

## 7. 인프라 상태 (직접 검증)

- sf_postgres apptainer instance: 다운 → scripts/up.sh 로 복구
- backend uvicorn / celery worker / celery beat / MCP 모두 가동
- Redis 정상 (Soseks314! 인증)
- crawler worker 가 DB 저장 중 (dcinside/clien/dogdrip 활발)
- `.env` 의 `REDDIT_USER_AGENT` 값에 공백/슬래시 있어 set -a; source 실패 → 따옴표 추가로 해결

## 8. 다음 단계 (사용자 결정)

1. **HN 검색어 다양화** + tags 분리로 중복 감소 (collector 패치)
2. **Reddit collector 본체 작성** (OAuth client_credentials 구현)
3. **categorizer 한국어 인포멀 확장** — Galaxy 모델 정규식 + "ㅋㅋ" 같은 짧은 댓글은 "others" 로 분류
4. **rule 35 동작 검증** — 7일 대기 후 발화 추이 확인
5. **DATABASE_URL .env 정규화** — 현재 POSTGRES_* 만 있고 DATABASE_URL 없어 직접 호출 시 명시 필요
