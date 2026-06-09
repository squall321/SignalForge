# Harvest 1주 누적 보고 v2 — 2026-06-07

- 생성: `2026-06-07T08:35:10+00:00`
- 라운드 수: **7** (Harvest 1 ~ harvest7)
- 누적 voc: **132,620** (24h=7,651 / 7d=80,976)
- 활성 사이트: total **70** / 24h **44** / 7d **68**
- 라벨 정정 사유: Harvest 5 V5 보고서가 alert 24h/7d 만 명시하여 30d 가 누락. Harvest 6 부분 완료 단계에서 NULL drift 발견 → v2 로 재실행.

## 1. Harvest 1-7 진척

| round | date | voc_total | active | 신규 사이트 | 비고 |
|---|---|---|---|---|---|
| harvest1 | 2026-06-06 | 119,739  | 65 | notebookcheck, zdnet_kr | 신규 사이트 2종 (notebookcheck/zdnet_kr) 활성화 |
| harvest2 | 2026-06-06 | 122,231 (+2,492) | 67 | resetera, ifixit | ResetEra/iFixit 신규 + 한국 deep 3,937 |
| harvest3p | 2026-06-06 | 124,500 (+2,269) | 68 | — | 운영 안정화 + Slack 결선 |
| harvest4 | 2026-06-06 | 127,466 (+2,966) | 69 | hardware_fr, gsmarena_forum | Hardware.fr / GSMArena forum 신규 (NULL 매핑 잔여) |
| harvest5 | 2026-06-07 | 129,833 (+2,367) | 69 | — | V5 누적 보고 + V1~V3 잔여 (24h XDA +72) |
| harvest6p | 2026-06-07 | 132,620 (+2,787) | 70 | — | rate-limit 2회 — 메인 직접 진단, alert drift 식별 (24h 346 / 7d 1,619 / 30d 1,619) |
| harvest7 | 2026-06-07 | 132,620  | 70 | — | X3 V5 재실행 + alert 라벨 정정 (24h/7d/30d 분리 명시) + NULL 최신화 |

## 2. 현재 KPI 스냅샷

- voc: total **132,620** / 24h 7,651 / 7d 80,976
- 사이트: total **70** active / 24h 활성 44 / 7d 활성 68
- archive 라운드: **5** (R26, harvest3p, harvest4, harvest5, harvest7)

### 2-1. Alert 라벨 명시 (라벨 drift 해소)

| 기간 | 건수 | 비고 |
|---|---|---|
| **24h** | 345 | 최근 1일 fired_at |
| **7d**  | 1,619 | 최근 7일 fired_at |
| **30d** | 1,619 | 최근 30일 fired_at — V5 누락 |

> **Note**: 7d (1,619) == 30d (1,619) — 알림 발화가 최근 7일에 집중되어 기간 간 동일치. 라벨 drift 가 아닌 실측 일치.

## 3. NULL 매핑 잔여 과제 (24h 실측)

| 사이트 | NULL / total | 비율 | 비고 |
|---|---|---|---|
| xda | 55/72 | 76.4% | X1 매핑 후 -53% 목표 (One UI · Buds+ · Watch · Tab · Flip · Fold) |
| gsmarena_forum | 133/236 | 56.4% | competitor (iPhone/Poco/Xiaomi) 의도 추적 — 필터 불필요 |
| hardware_fr | 86/375 | 22.9% | 22.9% — 안정 운영 범위 |
| notebookcheck | 18/34 | 52.9% | V4 신규 분석 — 안정 |

## 4. 안전장치

- DRY_RUN + PRESERVE_EXISTING + ON CONFLICT + audit JSONL round=harvest7 track=X3
- archive/<round> 자동 sentinel 유지
- regression baseline 11/11 endpoint
- self-report drift ±10% 가드

