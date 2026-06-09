# SignalForge 키-없이 작업 라운드 통합 보고 (2026-06-03)

## 1. 트랙별 결과 + verify

| 트랙 | Build | Verify 판정 | 비고 |
|---|---|---|---|
| A. 알림 운영 모니터링 | 완료 | PASS | 9/9 alert vitest, 2/2 backend, 라이브 curl `/alert-monitor` 200 |
| B. 수집 채널 대시보드 | 완료 | PASS (수치 미세 drift) | health active 23→22 / slow 16→17 (총 73 일치), 24h 9213→9189 |
| C. Compare 4-제품 확장 | 완료 | PASS (번들 + 20.3%) | typecheck/vitest 통과, Compare chunk 보고 6.90 → 실측 8.30 KB |
| D. Compare LLM 카드 (14b) | 완료 | PASS (테스트 수 보정) | grounding 0.35+ 가능, 14b 라우팅은 uvicorn 프로세스의 env 필요 |
| E. Drive 백업 검증 | 완료 | PASS | 50 backend pytest 모두 통과 (보고 49 → 실측 50, regression 없음) |

## 2. 신규 endpoint (4건, 모두 localhost-only)

| 메서드 | 경로 | 파일 | TTL |
|---|---|---|---|
| GET | `/_internal/alert-monitor` | `backend/app/api/_internal.py` | 120s |
| GET | `/_internal/collection-status` | `backend/app/api/_internal.py` | 300s |
| GET | `/_internal/backup-status` | `backend/app/api/_internal.py` | 미캐시 |
| POST | `/api/v1/insights/compare-llm` | `backend/app/api/insights.py` | 라우터 캐시 |

backend endpoint 70 → 74 (+4).

## 3. 신규 페이지

- `/collection` (Track B) — `CollectionStatus-sKS4TM1r.js` 5.49 KB, 지역/health 정렬 + 배지 4종.

## 4. Compare 보강 결과

- 4-제품 KPI 행, 트렌드/카테고리 차트, 이슈 표.
- 신규 서비스 `compareApi.ts` 가 5종 endpoint 병렬 호출, 일부 실패는 `null` 강등 → UI partial 유지.
- 번들: Compare chunk 8.30 KB (보고 6.90 KB 대비 +20.3% — LLM 카드 추가분 반영).

## 5. LLM 비교 카드 grounding 실측

- 라우팅 로직: `OPENAI_HIGH_MODEL_SHARED → qwen2.5:14b` (llm_provider.py:548-562).
- 단, uvicorn 프로세스에 env 미주입 시 fast tier(7b) 로 폴백. 운영 시 `set -a; source .env; set +a` 필요.
- crawler `test_compare_insight.py` 3/3 PASS, backend `test_compare_llm.py` 1/1 PASS.
- grounding 임계 0.35 게이트 유지.

## 6. 백업 검증 동작

- `verify-backup.sh` (5.3 KB exec) + `test_verify_backup.sh` (1.3 KB exec).
- Celery 스케줄 `verify-backup-daily-02` 등록, `verify_backup` task 등록.
- `/backup-status` endpoint + `system.backup_ok` 알림 metric 등록 (`_BOUNDS` 갱신).

## 7. 가동 절차

```bash
# backend env 주입 (LLM 14b 라우팅 보장)
cd /home/koopark/claude/SignalForge/backend && set -a && source ../.env && set +a && uvicorn app.main:app --reload

# frontend
cd /home/koopark/claude/SignalForge/frontend && npm run build && npm run preview

# 백업 검증 단발 실행
/home/koopark/claude/SignalForge/scripts/drive-sync/verify-backup.sh
```

## 8. 측정 수치 종합

| 지표 | 이전 | 현재 |
|---|---|---|
| backend endpoint | 70 | 74 (+4) |
| backend pytest | 40/40 | 50/50 |
| frontend vitest | 162/162 | 190/190 |
| frontend main bundle | 22.25 KB | 24.11 KB (+8.4%) |
| Alerts chunk | — | 13.93 KB |
| Compare chunk | — | 8.30 KB |
| CollectionStatus chunk | — | 5.49 KB |
| voc 누적 | 127,847 | 진행중 (24h ~9.2k) |
| 활성 플랫폼 | 62 | active 22 / slow 17 / stale 25 / dead 9 |

## 9. 다음 단계 (키 없이 가능)

1. Track B drift 해소 — `_classify_health` slow/active 경계 1건 안정화 (24h count cache 동기화).
2. Track D 14b 라우팅을 systemd unit 으로 영속화 — env 누락 시 grounding 0.35 미달 위험.
3. Track C 번들 +20.3% 회수 — LLM 카드 lazy import 검토 (현재 동기 import).
4. Track E `system.backup_ok` 임계 튜닝 — 24h 미실행 시 경고 룰 추가.
5. 수집 dead 9건 사이트 차단 원인 진단 (collection 페이지에서 즉시 드릴다운).
