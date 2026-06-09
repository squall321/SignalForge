# Sync Polish 2026-06-08 — Drive 동기화 정합 보강

## 사용자 질문 답변
"지금 자동 수집 데이터 모두 동기화 가능한가? 지금까지 데이터도 다 올렸나?"

- **보강 전 (00:48 UTC)**: db-dump `sf-db-20260607-235040Z.sql.gz` 23:50 → 현 DB 와 약 20분 격차(200-500건 미반영), LATEST.json 부재로 수신측 변경 감지 불가.
- **보강 후 (00:55 UTC)**: 신규 dump `sf-db-20260608-005147Z.sql.gz` push 완료 (52.7MB, SHA `af95345c…`), LATEST.json 업로드(voc_count 138,813), DB 실측 138,813 = drift **0건**. SIF 4종(backend/crawler/frontend/mcp) SHA256SUMS 일치, `sif_changed: []`.
- 향후: celery beat 30분 주기 자동 push 가동 중(다음 회 01:00 또는 01:30).

## Z1 — sync-to-drive.sh LATEST.json 통합
`scripts/sync-to-drive.sh` (11,028B) Stage 4.5 에 `lib/latest-meta.sh build` 호출 통합, LATEST.json 생성 후 `rclone copy` 로 `ApptainerImages:SignalForge/LATEST.json` 업로드. 테스트 `test_sync_to_drive_z1.sh` 13/13 PASS, regression 12/12 PASS. **drift**: helper schema(Y3, sync_run_id/alembic_head/voc_count/db_dump.sha256/sif_sha256sums) 와 Python `auto_sync.py` schema v1 키 차이 잔존 — 동일 LATEST.json 을 두 경로가 덮어쓰므로 마지막 writer 가 우선. 단일화 후속 과제.

## Z2 — 즉시 1회 강제 push
실행 run_id `z2-1780879899-403311` (00:51:39Z → 00:53:15Z). 신규 dump 52,687,285B, 로컬·Drive SHA 동일(`af95345c17c39059636898a021893c07e31a2242bd71e9c93e87cde5d62b9d57`). `rclone cat ApptainerImages:SignalForge/LATEST.json` → 26줄 JSON, voc_count 138813, alembic_head 0018, ts 2026-06-08T00:52:39Z. Archive sentinel `track_z2_force_push_z2-…json` (2,031B) 생성. db-dumps 보존 5종.

## Z3 — celery beat 30분 주기 검증
beat PID 213552 (etime 1h04m, 무재시작), worker PID 405456 재기동(이전 272892 는 06-06 01:03 시작이라 `run_auto_sync_to_drive` 미등록 → 워커 교체로 해소). schedule `auto-sync-to-drive-30m` 등록 확인, `test_beat_registered.sh` PASS. 00:00/00:30 의 `unregistered task` KeyError 2건은 워커 교체 전 잔재.

## Z4 — 수신측 시뮬 + archive sentinel
3 트랙 sentinel 모두 생성: sync_polish(829B)/portal_deploy(503B)/auto_sync(621B). 수신 시뮬은 `/tmp/sf_receiver_sim/sim.lock` 만(dry-run). **drift**: Z4 audit 에 `end` 만 기록, `start` 별도 이벤트 없음(sentinel 이 대체). pytest exit 0 이나 export/llm_status 3건 사전 실패는 Z4 무관.

## 라이브 무중단
postgres 5434 PID 1973 / redis / celery beat 213552 / worker 405456 / backend 8080 모두 정상. voc_records 138,813 ⇄ LATEST.json 일치.

## 잔여 drift
1. latest-meta.sh(Y3) ↔ auto_sync.py(v1) schema 단일화 미완.
2. audit 채널은 sync_polish/portal_deploy/auto_sync 3분리 OK 이나 Y1/Y3/Y5 트랙별 키 부재.
3. Z4 sentinel 의 `start` 이벤트 누락(end-only).
4. 수신측 실제 pull(state.json·재구성) 미구현 — 송신 정합만 검증.
