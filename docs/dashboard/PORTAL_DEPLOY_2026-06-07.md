# SignalForge — HWAX 포털 배포 운영 가이드 (2026-06-07)

> 4-Stage 배포 (S1 apptainer · S2 frontend prefix · S3 HWAX 등록 · S4 Drive 동기화) 완료 후의 일상 운영 절차. 현 가동 서비스 (backend :18000 · vite :17370 · sf_postgres :5434 · celery · HWAX :8088) 무중단 전제.

---

## 1. 진입 URL

| 용도 | URL | 검증 |
|---|---|---|
| 포털 홈 | http://127.0.0.1:8088/ | 200 |
| SignalForge SPA | http://127.0.0.1:8088/signalforge/ | 200 (현재 vite dev 통과) |
| SignalForge API health | http://127.0.0.1:8088/signalforge/api/v1/_internal/key-status | 200 |
| 백엔드 직결 | http://127.0.0.1:18000/health | 200 |
| 프런트 직결 (개발) | http://127.0.0.1:17370/ | 200 |

> **잔여**: `/signalforge/api/v1/health` 자체는 404 (백엔드가 `/health` 만 노출, `/api/v1/health` 미정의). 헬스체크는 위 표의 `_internal/key-status` 사용.

---

## 2. 새 서버 1줄 셋업 (콜드 부트)

```bash
# Drive → 로컬 동기화 (sif + db 최근본 + env 템플릿)
bash /home/koopark/claude/SignalForge/scripts/sync-from-drive.sh
# (자동) sif latest 다운 → SHA 검증 → /home/.../apptainer/sif/ 배치
# (자동) db 최신 dump 다운 → /home/.../backups/ 배치
```

이후 서비스 가동:

```bash
# postgres instance (이미 가동이면 skip)
apptainer instance list | grep -q sf_postgres || \
  apptainer instance start /home/koopark/claude/SignalForge/apptainer/sif/postgres.sif sf_postgres
# DB 복원 (최초 1회)
bash /home/koopark/claude/SignalForge/scripts/restore-db.sh /home/koopark/claude/SignalForge/backups/sf-db-YYYYMMDD-HHMMSSZ.sql.gz
# backend / frontend (개발 모드 유지)
cd /home/koopark/claude/SignalForge/backend && uvicorn app.main:app --host 0.0.0.0 --port 18000 &
cd /home/koopark/claude/SignalForge/frontend && npm run dev -- --host 0.0.0.0 --port 17370 &
```

HWAX 포털은 별 프로젝트이므로 (`/home/koopark/claude/HWAXPortal/`) 별도 nginx reload 만:

```bash
sudo nginx -s reload   # routes.env / systems.yaml / hwax.conf 변경 시
```

---

## 3. 일일 백업 + Drive 동기화

cron / systemd timer 권장 (예: 04:30 KST):

```bash
# 백업 (gzip + sha256, /home/koopark/claude/SignalForge/backups/)
bash /home/koopark/claude/SignalForge/scripts/backup-db.sh
# Drive 업로드 (sif latest + db 최근 7일 보존)
bash /home/koopark/claude/SignalForge/scripts/sync-to-drive.sh
```

### dry-run

`sync-to-drive.sh --dry-run`, `sync-from-drive.sh --dry-run` 모두 지원 (실 파일 변경 없음, rclone `--dry-run` 위임).

> **주의**: 구 `scripts/drive-sync/backup-to-drive.sh` 는 `--dry-run` 미지원으로 실 백업·업로드·retention 삭제까지 수행. **신 스크립트만 사용**.

### Drive 레이아웃 (현재)

- `ApptainerImages:SignalForge/sif/latest/` — backend / frontend / mcp / postgres / postgres-base + `SHA256SUMS` (5종, ~557 MiB)
- `ApptainerImages:SignalForge/sif/sif-YYYYMMDD-HHMMSSZ/` — 타임스탬프 스냅샷
- `ApptainerImages:SignalForge/db-dumps/` — sf-db-*.sql.gz + .sha256 (7 dumps · 28 objects · 1.33 GiB)
- `ApptainerImages:SignalForge/env/.env.example` — 4281B

> **drift**: `crawler.sif` (433 MB) 는 `SHA256SUMS` 와 `latest/` 에서 누락. 의도된 제외인지 확인 필요. 필요 시 `sync-to-drive.sh` 의 `SIF_LIST` 에 `crawler` 추가.

---

## 4. 롤백 절차

### 4a. SIF 롤백 (배포 후 문제 시)

```bash
# Drive 타임스탬프 스냅샷으로 회귀
rclone copy ApptainerImages:SignalForge/sif/sif-20260607-231406Z/ \
            /home/koopark/claude/SignalForge/apptainer/sif/ --progress
# 가동 서비스 재시작 (apptainer instance stop/start)
apptainer instance stop sf_postgres && \
  apptainer instance start /home/koopark/claude/SignalForge/apptainer/sif/postgres.sif sf_postgres
```

### 4b. DB 롤백

```bash
# 최근 안전 dump 확인
ls -lt /home/koopark/claude/SignalForge/backups/sf-db-safety-*.sql.gz | head -3
# 복원 (활성 세션 종료 필요 — backend/celery 먼저 stop)
bash /home/koopark/claude/SignalForge/scripts/restore-db.sh \
     /home/koopark/claude/SignalForge/backups/sf-db-safety-20260607-231641Z.sql.gz
```

> **주의**: `sync-from-drive.sh` 의 자동 복원은 활성 세션이 있으면 DROP DATABASE 실패로 안전 중단됨 (S4 검증 중 실증). DB 손상 없음 — 의도된 안전장치.

### 4c. HWAX 포털 등록 해제

```bash
# /home/koopark/claude/HWAXPortal/backend/config/routes.env 에서 signalforge 2줄 제거
# /home/koopark/claude/HWAXPortal/backend/config/systems.yaml 에서 signalforge 카드 제거
# /home/koopark/claude/HWAXPortal/infra/nginx/hwax.conf 에서 location /signalforge/{,api/} 블록 제거
sudo nginx -t && sudo nginx -s reload
```

---

## 5. 자산 인벤토리 (2026-06-07 23:34 기준)

| 파일 | 크기 | SHA256 (선두 12자) |
|---|---|---|
| `apptainer/sif/backend.sif` | 145.0 MB | `008bb98629a8` |
| `apptainer/sif/crawler.sif` | 432.9 MB | `7ea59f2fc335` |
| `apptainer/sif/frontend.sif` | 71.1 MB | `4c817ae38410` |
| `apptainer/sif/mcp.sif` | 136.9 MB | `a772deab4b2b` |
| `apptainer/sif/postgres.sif` | 102.2 MB | `d16f9d27e8bd` (5/15 baseline 유지) |
| `apptainer/sif/postgres.sif.new` | 102.2 MB | `79144f02e7b3` (재빌드본, 미적용) |
| `apptainer/sif/MANIFEST.json` | 1.4 KB | round=portal_deploy, track=S1 |

MANIFEST.json 은 6 엔트리 (postgres / postgres_new / postgres_base / backend / crawler / mcp). **frontend 엔트리 누락** — Stage 1 스코프(4종) 후 S2 에서 추가 빌드된 것이라 manifest 갱신 필요.

---

## 6. 알려진 잔여 (next-round 백로그)

1. **frontend.sif 미사용**: 현재 HWAX → vite dev (`:17370`) 로 통과. `dist/` 베이크 nginx 서빙 패턴으로 전환 시 `routes.env` 의 `signalforge=` 를 `:17371` (nginx-on-sif) 로 변경.
2. **MANIFEST.json 누락 엔트리**: `frontend` 추가, `crawler` 는 이미 포함됨.
3. **`/signalforge/api/v1/health` 404**: 백엔드 `/api/v1/health` 엔드포인트 정의 필요 (현재 `/health` 만 존재).
4. **postgres.sif.new 미적용**: 5/15 baseline 유지 중. 신본 적용 시 instance stop → 파일 교체 → instance start.
5. **`crawler.sif` Drive 누락**: 433 MB 비용 vs 회귀 빈도 trade-off 후 결정.
6. **archive sentinel `reports/archive/portal_deploy/` 부재**: R26 이후 라운드별 archive 패턴 미적용 (audit JSONL `reports/audit/portal_deploy.jsonl` 만 존재).

---

## 7. 가동 상태 (재확인 명령)

```bash
curl -sI http://127.0.0.1:8088/signalforge/ | head -1                        # 200
curl -s http://127.0.0.1:18000/health                                        # {"status":"ok"}
apptainer instance list | grep sf_postgres                                   # RUNNING
ps -ef | grep -E "uvicorn|celery|vite" | grep -v grep                        # PID 확인
bash /home/koopark/claude/SignalForge/scripts/status.sh                      # 통합 헬스
```

---

작성: portal_deploy 라운드 운영 결선 / audit `reports/audit/portal_deploy.jsonl` 2 events
