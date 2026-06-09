# SignalForge × Drive-Sync 인수인계 문서

> **작성일**: 2026-06-01
> **출처**: AIDataHub `deploy/templates/drive-sync/` (표준 키트)
> **검증 기원**: MXWhitePaper (실운영, 일일 백업) + AIDataHub (2026-06-01 활성화)
> **이식 상태**: 키트 + SF 용 `PROJECT.conf` 채워진 상태로 `scripts/drive-sync/` 에 배치 완료
> **검증된 동작**: SF DB 22MB dump → Drive 업로드 + sha256 검증 (2026-06-01 20:17 UTC)
> **검증 안 된 동작**: 다른 머신에서 sync-from-drive → restore → SF backend 정상 동작 (E2E)

---

## TL;DR — SF 운영자가 할 일 (3단계)

```bash
# 1) 소스 서버 (현재 PC) — 1회 셋업 (이미 완료, 재실행 안전)
cd /home/koopark/claude/SignalForge/scripts/drive-sync
bash setup-drive-sync.sh

# 2) 백업 (검증됨, 22MB → ApptainerImages:SignalForge/db-dumps/)
bash backup-to-drive.sh

# 3) 다른 머신에서 SF DB 통째로 가져오기
cd /home/koopark/claude/SignalForge && git pull
cd scripts/drive-sync
bash setup-drive-sync.sh
bash sync-from-drive.sh
```

---

## 1. 이 키트가 하는 일

```
[소스 서버 — 현재 PC]               [Drive (단일 진실)]              [타깃 서버 — 새 PC]
┌──────────────────┐                                                ┌──────────────────┐
│ pg_dump          │  backup-to-drive.sh                            │ sync-from-drive  │
│ ↓                │ ───────────────────▶ ApptainerImages:          │ ↓                │
│ gzip + sha256    │   (retain 최신 5개)   SignalForge/db-dumps/    │ rclone copy      │
│ ↓                │                       ├ sf-db-*.sql.gz         │ ↓                │
│ rclone copy      │                       ├ *.sha256               │ sha256 검증      │
└──────────────────┘                       └ RESTORE-GUIDE-*.md     │ ↓                │
                                                       ↑            │ DROP+CREATE      │
                                                       │            │ ↓                │
                                                       └────────────│ restore          │
                                                                    │ ↓                │
                                                                    │ (옵션) /health   │
                                                                    └──────────────────┘
```

핵심:
- **rclone remote 공유** — `ApptainerImages:` 1개를 MXWP/AIDH/SF 가 공유
- **프로젝트별 폴더 분리** — `SignalForge/db-dumps/` 로 격리
- **TS 기반 파일명** — `sf-db-YYYYMMDD-HHMMSSZ.sql.gz`, "최신" = `sort | tail -1`
- **sha256 동봉** — 네트워크 손상 감지 + 복원 전 무결성 검증
- **안전백업 후 DROP+CREATE+restore** — restore 실패해도 직전 상태 복귀
- **retain N개** — Drive 용량 폭주 방지 (기본 5개)

---

## 2. 이식된 파일 (10개)

| 파일 | 역할 | 수정 필요? |
|---|---|---|
| `HANDOFF.md` | (이 문서) 인수인계 | — |
| `ONBOARDING.md` | 표준 키트 SF 맥락 안내 | — |
| `README.md` | 표준 키트 일반 사용법 | — |
| `PROJECT.conf` | **SF 용으로 미리 채워짐** | `PROJ_HEALTH_URL` 만 추후 채우기 권장 |
| `PROJECT.conf.example` | 다른 프로젝트로 또 이식할 때 참고용 | — |
| `_drive_common.sh` | 공용 헬퍼 (PROJECT.conf 로딩 + PG 명령 추상화) | 수정 불필요 |
| `setup-drive-sync.sh` | 1회 셋업 (rclone remote + Drive 폴더) | 수정 불필요 |
| `backup-db.sh` | 로컬 dump (Drive X) | 수정 불필요 |
| `backup-to-drive.sh` | dump + Drive 업로드 + 보존정책 | 수정 불필요 |
| `restore-db.sh` | 로컬 sql.gz → 안전백업 후 restore | 수정 불필요 |
| `sync-from-drive.sh` | Drive 최신 → restore (one-shot) | 수정 불필요 |

---

## 3. SF 의 채워진 PROJECT.conf

```bash
PROJ_PREFIX=sf                                            # dump prefix
PROJ_NAME=SignalForge                                     # Drive 폴더명
PROJ_PG_INSTANCE=sf_postgres                              # apptainer instance list 로 확인됨
PROJ_ENV_FILE=/home/koopark/claude/SignalForge/.env       # POSTGRES_* 출처
PROJ_DUMP_DIR=/home/koopark/claude/SignalForge/backups
PROJ_DRIVE_REMOTE_DEFAULT=ApptainerImages                 # 호스트 공용 rclone remote
PROJ_DRIVE_RETAIN_DEFAULT=5
PROJ_HEALTH_URL=                                          # SF backend health endpoint 정해지면 채우기
```

키트가 SF `.env` 에서 직접 읽는 값들 (확인됨):
- `POSTGRES_USER=signalforge`
- `POSTGRES_PASSWORD=signalforge_pass`
- `POSTGRES_DB=signalforge`
- `POSTGRES_PORT=5434`
- `POSTGRES_HOST=127.0.0.1`

---

## 4. 정직한 한계 — 이 키트 만든 사람이 SF 내부를 얼마나 알고 만들었나

### 확인한 것 (실제로 들여다본 것)

| 항목 | 어떻게 |
|---|---|
| PG 인스턴스명 `sf_postgres` | `apptainer instance list` 출력 |
| PG 포트 `5434` | `.env` grep |
| PG user/pass/db | `.env` grep |
| `scripts/` 위치 | `find` |
| backup 스크립트 없음 | 위 find 결과 0건 |
| 실제 backup 동작 | 22MB dump 성공 (sha256: `c2a6d47967ddbcc17806a89a0eaa8a0658e9b20abea24f3771c53c69e0e63b7f`) |

### 확인 안 한 것 (가정으로 처리)

| 가정 | 위험 |
|---|---|
| `voc_records` 가 핵심 테이블 | AIDH 통합 때 본 적 있어 추측 — SF schema 전체는 안 봄 |
| backend health endpoint URL | 모름 (`PROJ_HEALTH_URL=` 비워둠) |
| SF stack 가동 절차 (`scripts/up.sh`) | 파일 존재만 확인, 내부 안 봄 |
| `--clean --if-exists` pg_dump 옵션이 SF schema 와 호환 | 일반적이라 됐지만, 특수 trigger/role 이 있으면 깨질 수 있음 |
| Postgres extensions (pgvector, postgis 등) 사용 여부 | 안 봄. AIDH 는 pgvector 쓰는데 SF 는 확인 안 함 |
| 대용량 BLOB / large objects 사용 여부 | `pg_dump` 가 LO 따로 처리 옵션 필요한데 안 다룸 |
| Alembic / migration 의존 여부 | restore 후 alembic head 일치 여부 검증 로직 없음 |

### 보수적 결론

**현재 키트는 "표준 PostgreSQL 데이터 백업" 으로는 충분히 동작**합니다 (22MB dump 성공이 증명). 하지만 SF 의 도메인 특성을 모른 채 표준 패턴만 적용한 상태입니다.

---

## 5. SF 측에서 결정/보강해야 할 것 (4가지)

### 5.1 (필수) E2E 검증 1회

```bash
# 소스 (현재 PC) — 이미 검증됨
bash backup-to-drive.sh

# 타깃 (새 PC) — SF stack 가동 상태에서
bash sync-from-drive.sh

# row count 비교
PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge \
  -c "SELECT count(*) FROM voc_records;"
```

소스/타깃 row 수가 같으면 PASS. 추가로 SF backend 띄워서 API 호출까지 가야 완전 PASS.

### 5.2 (권장) SF schema 점검 후 키트 보강

```bash
# SF stack 가동 중 상태에서
PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge <<'EOF'
\dx        -- extensions: pgvector, postgis 등 있나?
\dn        -- schemas: public 외 다른 schema 있나?
\dt        -- tables 전체 목록
\dl        -- large objects 사용 여부
SELECT current_setting('server_version_num');  -- PG 버전
EOF
```

위 결과 따라:
- **pgvector 사용** → `--clean --if-exists` 가 extension 도 drop. restore 전에 `CREATE EXTENSION` 자동 보장 로직 필요할 수 있음
- **다른 schema 사용** → pg_dump 가 default 로 public 만 안 잡음 (full DB dump 라 OK 일 가능성 높지만 검증 필요)
- **large objects 사용** → `pg_dump -b` 옵션 추가 필요

### 5.3 (권장) `PROJ_HEALTH_URL` 채우기

SF backend health endpoint 가 정해지면:
```bash
# PROJECT.conf 편집
PROJ_HEALTH_URL=http://127.0.0.1:8000/api/v1/health
```

→ `sync-from-drive.sh` 가 restore 후 자동 검증.

### 5.4 (권장) 자동 백업 cron

```cron
# crontab -e
30 4 * * *  /bin/bash /home/koopark/claude/SignalForge/scripts/drive-sync/backup-to-drive.sh \
            >> /home/koopark/sf-backup.log 2>&1
```

매일 04:30 UTC 자동 백업. Drive 의 보존 정책 (최신 5개) 도 자동 적용.

---

## 6. 운영 명령 치트시트

```bash
# 1회 셋업
bash setup-drive-sync.sh

# 로컬 dump 만 (Drive X)
bash backup-db.sh

# dump + Drive 업로드 + 보존정책
bash backup-to-drive.sh

# 보존 개수 임시 override
PROJ_DRIVE_RETAIN=10 bash backup-to-drive.sh

# 로컬 sql.gz → restore (안전백업 자동)
bash restore-db.sh /path/to/sf-db-YYYYMMDD-HHMMSSZ.sql.gz
bash restore-db.sh /path/to/sf-db-YYYYMMDD-HHMMSSZ.sql.gz --yes   # 확인 프롬프트 스킵

# Drive 최신 → 다운로드 → restore (one-shot)
bash sync-from-drive.sh
bash sync-from-drive.sh --dry-run                                  # 어떤 파일 받을지만 확인

# Drive 에 뭐가 있나
rclone lsf ApptainerImages:SignalForge/db-dumps

# 특정 dump 의 공유 링크
rclone link ApptainerImages:SignalForge/db-dumps/sf-db-20260601-201729Z.sql.gz
```

---

## 7. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| `rclone: remote not found` | `setup-drive-sync.sh` 1회 실행 (브라우저 PC 에서 `rclone authorize "drive"` 후 토큰 붙여넣기) |
| `permission denied` on `/var/log/sf-backup.log` | cron 경로를 사용자 home 로 변경 (예: `~/sf-backup.log`) |
| `sha256 mismatch` | 다운로드 중 손상 — `sync-from-drive.sh` 재시도 |
| `apptainer instance not found: sf_postgres` | SF stack 미가동 — `scripts/up.sh` 먼저 |
| `pg_dump: error: connection failed` | `.env` 의 PG 정보 확인 + SF stack 가동 확인 |
| Drive 용량 초과 | `PROJ_DRIVE_RETAIN_DEFAULT` 줄이거나 backup 재실행 (정책 재적용) |
| restore 후 SF backend 가 500 에러 | 5.2 의 schema 점검 — pgvector / extensions / large objects 확인 |

---

## 8. 표준 키트 상위 유지 정책

이 키트의 본체는 `AIDataHub/deploy/templates/drive-sync/` 가 단일 진실.

업데이트 받기:
```bash
cp /home/koopark/claude/AIDataHub/deploy/templates/drive-sync/{_drive_common,*.sh} \
   /home/koopark/claude/SignalForge/scripts/drive-sync/
# PROJECT.conf, HANDOFF.md, ONBOARDING.md 는 덮어쓰지 말 것!
```

SF 전용 변경 (예: `PROJECT.conf`, 추가 cron, 도메인 hook) 은 SF repo 안에서 자유롭게.

---

## 9. 같은 호스트의 다른 프로젝트 비교

| 프로젝트 | Drive 폴더 | 활성화 상태 | 첫 dump |
|---|---|---|---|
| **MXWhitePaper** | `ApptainerImages:MXWhitePaper/data-dumps/` | 실운영 (cron 등록됨) | `mxwp-data-20260521-081320Z.tar.gz` |
| **AIDataHub** | `ApptainerImages:AIDataHub/db-dumps/` | 활성화 (cron 미등록) | `aidh-db-20260601-200355Z.sql.gz` (2.6MB) |
| **SignalForge** | `ApptainerImages:SignalForge/db-dumps/` | **본 이식으로 활성화** (cron 미등록) | `sf-db-20260601-201729Z.sql.gz` (22MB) |

세 프로젝트가 같은 rclone remote (`ApptainerImages:`) 를 공유, 폴더만 분리.

---

## 10. 변경 이력

| 일자 | 변경 | 비고 |
|---|---|---|
| 2026-06-01 | 표준 키트 초기 이식 (AIDH 검증 패턴) | 22MB dump 검증 완료 |

---

## 11. 연락

이슈/질문/패치 — AIDataHub 측 표준 키트 (`/home/koopark/claude/AIDataHub/deploy/templates/drive-sync/`) 가 단일 진실. SF 적용 특이사항은 본 문서 + `PROJECT.conf` 에 누적.
