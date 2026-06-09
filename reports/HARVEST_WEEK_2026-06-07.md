# Harvest 1주 누적 보고 — 2026-06-07

- 생성: `2026-06-07T00:57:33+00:00`
- 라운드 수: **5** (Harvest 1 ~ harvest5)
- 누적 voc: **129,833** (24h=8,574 / 7d=80,464)
- 활성 사이트: total **69** / 24h **51** / 7d **69**

## 1. Harvest 1-5 진척

| round | date | voc_total | active | 신규 사이트 | 비고 |
|---|---|---|---|---|---|
| harvest1 | 2026-06-06 | 119,739  | 65 | notebookcheck, zdnet_kr | 신규 사이트 2종 (notebookcheck/zdnet_kr) 활성화 |
| harvest2 | 2026-06-06 | 122,231 (+2,492) | 67 | resetera, ifixit | ResetEra/iFixit 신규 + 한국 deep 3,937 |
| harvest3p | 2026-06-06 | 124,500 (+2,269) | 68 | — | 운영 안정화 + Slack 결선 |
| harvest4 | 2026-06-06 | 127,466 (+2,966) | 69 | hardware_fr, gsmarena_forum | Hardware.fr / GSMArena forum 신규 (NULL 매핑 잔여) |
| harvest5 | 2026-06-07 | 129,833 (+2,367) | 69 | — | V5 누적 보고 + V1~V3 잔여 — 진행 중 |

## 2. 현재 KPI 스냅샷

- voc: total **129,833** / 24h 8,574 / 7d 80,464
- 사이트: total **69** active / 24h 활성 51 / 7d 활성 69
- alert_events: 24h=301 / 7d=1,472
- archive 라운드: **4** (R26, harvest3p, harvest4, harvest5)

## 3. Harvest 5 잔여 과제

| 트랙 | KPI | 현재 | 비고 |
|---|---|---|---|
| V1 XDA news_tag | xda voc 24h | 0 (total 77) | forum 차단 → news_tag 정식 도입 평가 중 |
| V2 GSMArena 매핑 | NULL 24h | 161/236 (68.2%) | A57/A37/A78 단독 토큰 패턴 필요 |
| V3 Hardware.fr 매핑 | NULL 24h | 198/375 (52.8%) | forum thread title 추가 매핑 필요 |

## 4. 안전장치

- DRY_RUN + PRESERVE_EXISTING + ON CONFLICT + audit JSONL round=harvest5 track=V5
- archive/<round> 자동 sentinel — 4 라운드 폴더 유지
- regression baseline 11/11 — Harvest 4 부터 hardware_fr_voc 포함
- self-report drift ±10% 가드 유지

