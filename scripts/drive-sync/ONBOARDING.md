# SignalForge × Drive-Sync 이식 안내

> **출처**: AIDataHub `deploy/templates/drive-sync/` (검증 기원: MXWhitePaper 실운영)
> **이식 일자**: 2026-06-01
> **상태**: SignalForge 용 `PROJECT.conf` 채워진 채로 SF repo 에 이식 완료. 운영자가 1회 실행만 하면 됩니다.

## TL;DR — SF 운영자가 할 일

```bash
# 1) SF 소스 서버에서 1회 셋업
cd /home/koopark/claude/SignalForge/scripts/drive-sync
bash setup-drive-sync.sh        # rclone remote 확인 + Drive 폴더 보장

# 2) 첫 백업 (검증)
bash backup-to-drive.sh
# → ApptainerImages:SignalForge/db-dumps/sf-db-YYYYMMDD-HHMMSSZ.sql.gz 생성

# 3) (선택) 일일 자동 백업 — crontab
crontab -e
# 추가:
#   30 4 * * *  /bin/bash /home/koopark/claude/SignalForge/scripts/drive-sync/backup-to-drive.sh \
#               >> /var/log/sf-backup.log 2>&1
```

새 서버에서 SF DB 통째로 가져올 때:

```bash
cd /home/koopark/claude/SignalForge && git pull
cd scripts/drive-sync
bash setup-drive-sync.sh        # 토큰 있는 호스트면 자동 통과
bash sync-from-drive.sh         # Drive 최신 dump → sha256 검증 → DROP+CREATE+restore
```

## 이식된 파일 (8개)

| 파일 | 역할 |
|---|---|
| `README.md` | 표준 키트 일반 사용법 |
| `ONBOARDING.md` | (이 문서) SF 맥락 전달 사항 |
| `PROJECT.conf` | **SF 용으로 미리 채워짐** (수정 불필요) |
| `PROJECT.conf.example` | 다른 프로젝트로 또 이식할 때 참고용 |
| `_drive_common.sh` | 공용 헬퍼 (수정 불필요) |
| `setup-drive-sync.sh` | 1회 셋업 |
| `backup-db.sh` | 로컬 dump (Drive X) |
| `backup-to-drive.sh` | dump + Drive 업로드 + 보존정책 |
| `restore-db.sh` | 로컬 sql.gz → 안전백업 후 restore |
| `sync-from-drive.sh` | Drive 최신 → restore (one-shot) |

## SF 의 채워진 PROJECT.conf

```bash
PROJ_PREFIX=sf
PROJ_NAME=SignalForge
PROJ_PG_INSTANCE=sf_postgres                  # apptainer instance list 로 확인됨
PROJ_ENV_FILE=/home/koopark/claude/SignalForge/.env
                                              # POSTGRES_USER=signalforge / DB=signalforge / PORT=5434
PROJ_DUMP_DIR=/home/koopark/claude/SignalForge/backups
PROJ_DRIVE_REMOTE_DEFAULT=ApptainerImages     # 호스트 공용 rclone remote
PROJ_DRIVE_RETAIN_DEFAULT=5
PROJ_HEALTH_URL=                              # SF backend health endpoint 정해지면 채우기
```

`PROJ_HEALTH_URL` 만 SF backend 의 실제 health endpoint 로 추후 채우시면
`sync-from-drive.sh` 가 restore 후 자동 검증합니다.

## 동작 보증

이 키트는 다음과 동일한 패턴 (실운영):

- **MXWhitePaper**: 일일 백업 운영 중 (`ApptainerImages:MXWhitePaper/data-dumps/`)
- **AIDataHub**: 2026-06-01 활성화, 첫 dump (`aidh-db-20260601-200355Z.sql.gz`, 2.6MB, sha256 검증) Drive 업로드 완료

## SF 측 결정 사항 (3가지)

1. **자동 백업 cron 등록 여부** — 운영 환경이면 권장 (위 TL;DR 3번)
2. **health endpoint URL** — `PROJ_HEALTH_URL` 채우기
3. **외부 backup destination 추가 필요 여부** — 현재는 Drive 단일. S3 등 추가하려면 `backup-to-drive.sh` 의 `rclone copy` 라인 복제

## E2E 검증 (이식 후 1회 필수)

```bash
# 소스 (현재 PC)
bash backup-to-drive.sh

# 타깃 (새 PC) — SF stack 가동 상태에서
bash sync-from-drive.sh

# row count 비교
PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge \
  -c "SELECT count(*) FROM voc_records;"
```

소스 / 타깃 row 수가 같으면 PASS.

## 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| `rclone: remote not found` | `setup-drive-sync.sh` 1회 실행 (토큰 입력) |
| `permission denied` on `/var/log/sf-backup.log` | cron 경로를 사용자 home 로 변경 (예: `~/sf-backup.log`) |
| `sha256 mismatch` | 다운로드 중 손상 — 재시도 (`sync-from-drive.sh` 다시) |
| `apptainer instance not found: sf_postgres` | SF stack 미가동 — `scripts/up.sh` 먼저 |
| Drive 용량 초과 | `PROJ_DRIVE_RETAIN_DEFAULT` 줄이거나 `bash backup-to-drive.sh` 다시 (정책 재적용) |

## 표준 키트 상위 유지 정책

- **키트 본체 (`_drive_common.sh` 외 6개)** 의 패치는 AIDH 표준 템플릿에서 발생.
  업데이트 동기화 권장:
  ```bash
  cp /home/koopark/claude/AIDataHub/deploy/templates/drive-sync/{_drive_common,*.sh} \
     /home/koopark/claude/SignalForge/scripts/drive-sync/
  # PROJECT.conf 는 덮어쓰지 말 것!
  ```
- **SF 전용 변경** (예: `PROJECT.conf`, `ONBOARDING.md`) 은 SF repo 안에서 자유롭게.
