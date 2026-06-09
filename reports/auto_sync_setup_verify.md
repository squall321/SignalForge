# auto_sync 셋업 검증 보고서 (Stage 4.5 · Y5 트랙)

작성: 2026-06-07 23:48 UTC · round=`auto_sync` track=`Y5`

---

## 1. 산출물

| 항목 | 경로 | 비고 |
|---|---|---|
| 신규 endpoint | `backend/app/api/_internal.py` `/sync-status` | localhost only · 약 +290 LoC |
| 단위 테스트 | `backend/tests/test_sync_status.py` | 3 케이스 (missing/valid/corrupt) |
| 운영 가이드 | `docs/dashboard/AUTO_SYNC_2026-06-07.md` | 7 섹션 (활성/셋업/구조/모니터링/장애/잔여/명령) |
| 검증 보고 | `reports/auto_sync_setup_verify.md` | 본 문서 |

---

## 2. 검증 결과

### 2a. pytest

```
$ cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_sync_status.py -v
tests/test_sync_status.py::test_sync_status_missing_files PASSED          [ 33%]
tests/test_sync_status.py::test_sync_status_with_events_and_manifest PASSED [ 66%]
tests/test_sync_status.py::test_sync_status_graceful_skip_bad_lines PASSED [100%]
========================= 3 passed, 1 warning in 0.25s =========================
```

### 2b. 라이브 endpoint

```
$ curl -sf -w "HTTP:%{http_code}\n" http://127.0.0.1:18000/api/v1/_internal/sync-status
HTTP:200
```

응답 본문 핵심 (실 운영 데이터, jq 추출):
- `audit_available: true` · `audit_path: /home/koopark/claude/SignalForge/logs/audit/portal_deploy.jsonl`
- `send.available: true` · `send.last_event.event: "end"` · `send.last_run.ok: true` · `send.counters_24h: {runs:8, ok:8, fail:0, dry_runs:7}`
- `recv.available: true` · `recv.last_event.event: "end"` · `recv.last_run.ok: true` · `recv.counters_24h: {runs:7, ok:5, fail:2, dry_runs:7}`
- `latest_manifest.available: false` · `latest_manifest.path: /home/koopark/claude/SignalForge/apptainer/sif/LATEST.json` (Y2 트랙 미적용 — 의도된 잔여)
- `summary: {send_ok_24h:true, recv_ok_24h:true, any_fail_24h:true, latest_present:false}`

### 2c. 라이브 무중단 검증

- backend uvicorn 재기동 1회 (구 PID 2458969 종료 → 신 PID 201849). 다운타임 약 3초.
- 재기동 후 `/health` 200 · 기존 `/api/v1/_internal/key-status` 200 회귀 없음.
- sf_postgres / sf_celery / vite :17370 / HWAX :8088 무영향 (포트 미관여).

---

## 3. self-report (drift / 한계)

1. **`latest_present: false`**: 송신 측 `scripts/sync-to-drive.sh` 가 아직 `LATEST.json` 을 만들지 않음. 본 endpoint 는 그 잔여를 정직하게 노출 (graceful, error 아님). Y2 트랙 (Celery push task 도입) 적용 시 자연 해결.
2. **`recv.counters_24h.fail: 2`**: portal_deploy 라운드 (S4) 의 dry-run 디버깅 잔여 (sif_skip/db_skip 이벤트 2건). auto_sync 라운드 실 실행에서 추가 fail 발생 시 5절 장애 대응 참조.
3. **uvicorn `--reload` 미적용**: 새 endpoint 반영을 위해 backend 재기동 필요. 운영 정책상 `--reload` 추가는 별도 검토 (코드 변경 빈도 vs 재기동 비용).
4. **HWAX 경유 검증 미실행**: `http://127.0.0.1:8088/signalforge/api/v1/_internal/sync-status` 는 nginx `deny all` 정책으로 403 예상 — `/api/v1/_internal/` 는 localhost 전용. 운영자가 ssh 터널로만 접근.

---

## 4. 결선 명령 (재현)

```bash
# 테스트
cd /home/koopark/claude/SignalForge/backend && PYTHONPATH=. .venv/bin/pytest tests/test_sync_status.py -v

# 라이브 호출
curl -sf http://127.0.0.1:18000/api/v1/_internal/sync-status | jq '.summary'

# 가이드 읽기
cat /home/koopark/claude/SignalForge/docs/dashboard/AUTO_SYNC_2026-06-07.md
```
