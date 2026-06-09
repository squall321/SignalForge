# Stage 5B 부활 + 공백 보강 (2026-06-08)

## 1. Discovery 요약
사장 collector 4종 식별 (Stage 5 후속 + 본 라운드): **anandtech** (11KB, AnandTechCrawler, US IT), **bluesky** (8.6KB, BlueskyCrawler, Global SNS), **kaskus** (12KB, KaskusCrawler, Indonesia), 추가 신규 **droidsans** (TH). 4pda RSS 한계 30건 capacity 실증. 진짜 공백 4국 (NL 16/CA 26/TH 29/CN 31) 중 TH 1국 우선 착수.

## 2. R1 4pda 본런
audit_end 1건 (06:39:45.157), DB INSERT id=1334515 (06:39:45.136) — 정확 매칭. voc 5→6 (+1, RSS 1채널 한계 실증). RU 170→171.

## 3. R2 anandtech 부활 (US IT)
DB id=94, region=US, is_active=t. **voc +329** (unique_threads 27, avg posts/thread 12.19) — 보고 0% drift. tasks.py L157 등록, celery_app.py L242-244 schedule 14400s 신규.

## 4. R3 bluesky 부활 (Global SNS)
DB id=84 (기존 row 재활용), tasks.py L153 신규 등록, celery_app.py L44 기존 schedule 재사용. voc +0 (.env BLUESKY_HANDLE/PASSWORD 키 라인 자체 부재 → graceful skip, 정상 동작). **블로커: .env 키 등록 선행 필수**.

## 5. R4 kaskus 부활 (Indonesia)
tasks.py L150, celery_app.py L241 등록 OK. **DB row 부재** — 보고의 "true→false 롤백" 표현 부정확 (실제는 row 자체 미생성). voc +0. ID region 158건 기존 사이트 유지.

## 6. R5 droidsans (TH 공백)
DB id=95 신규, region=TH, is_active=t. **voc +6**, TH 29→35 (정확 매칭). 6 audit 이벤트 (07:00:00~07:00:35). 사이트 1→2.

## 7. 라이브 실측 (2026-06-08 현재)
| 지표 | 값 | 비고 |
|---|---|---|
| voc_records 총합 | **142,822** | 컨텍스트 142,486 → +336 |
| 4pda voc | 6 | +1 (R1) |
| anandtech voc | **329** | 신규 부활 최대 기여 |
| bluesky voc | 0 | 키 미등록 |
| kaskus voc | 0 | DB row 부재 |
| droidsans voc | 6 | TH 공백 1국 해소 |
| 활성 사이트 RU/US/TH/CN/NL/CA | 2/3/2/1/1/1 | TH 1→2 |

## 8. OOM 안전 검증
| 시점 | free MB | available MB | swap used MB |
|---|---|---|---|
| Stage 5B 시작 | ~4,050 | ~65,690 | 7,797 |
| R5 종료 | 4,565 | 66,134 | 7,797 |
| **현재** | **4,581** | **66,155** | **7,797** |

swap 7,797MB **무변동** 전 트랙 유지 — 본 라운드 추가 압박 0. concurrency 4 정책 준수.

## 9. 4중 안전장치
(1) worker concurrency 4 고정 — 6+ 금지 swap 보호. (2) BACKFILL 보수적 (4pda RSS 30, droidsans 신규 2페이지). (3) 각 트랙 전후 `free -m` 실측. (4) DRY_RUN→preserve 본런 순차.

## 10. 잔여 (드리프트 + 미해결)
- **SignalForge celery worker 부재** — beat (PID 213552)만 가동, worker PID 1179333 실제 미가동. 4pda/anandtech/droidsans INSERT 6+329+6=341건은 본 라운드 직접 호출 결과로 추정, 정기 스케줄은 worker 재가동 필요.
- **R4 kaskus DB row 미생성** — tasks/celery 등록만 완료, platforms INSERT 미실행. 다음 라운드 R4 재시도 필요.
- **R3 bluesky .env 키** — HANDLE/PASSWORD 라인 부재. 등록 후에야 수집 가동.
- **공백 3국 잔존** (NL 16/CA 26/CN 31) — TH만 해소, 다음 라운드 3개 추가 후보.
- voc 총합 +336 (4pda 1 + anandtech 329 + droidsans 6 = 336) — 정확 매칭.

## 사장 collector 부활 누적
Stage 5 (4pda) + Stage 5B (anandtech, droidsans 신규) = **3종 가동**, bluesky/kaskus 등록만 (실수집 보류). 사장 코드 47KB 중 30KB 부활.
