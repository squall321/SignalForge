# SignalForge 새 서버 셋업 가이드

원본 서버에서 검증된 절차 (2026-06-12). 소요 약 30분 (Drive 다운로드 속도 의존).

## 0. 선행 패키지 (sudo 1회)

```bash
sudo apt update && sudo apt install -y redis-server git curl
curl https://rclone.org/install.sh | sudo bash

# apptainer 1.3+ (PostgreSQL 컨테이너용)
sudo add-apt-repository -y ppa:apptainer/ppa && sudo apt install -y apptainer

# python 3.12+ / node 20+ 확인 (없으면 deadsnakes / nodesource)
python3 --version && node --version
```

Redis 에 비밀번호 설정 (.env 의 `REDIS_PASSWORD` 와 동일하게):

```bash
sudo sed -i 's/^# requirepass .*/requirepass <REDIS_PASSWORD>/' /etc/redis/redis.conf
sudo systemctl restart redis-server
```

## 1. 코드

```bash
git clone git@github.com:squall321/SignalForge.git
cd SignalForge
```

## 2. rclone 연결 (1회)

```bash
bash scripts/drive-sync/setup-drive-sync.sh
```

- 헤드리스 서버면: 브라우저 되는 PC 에서 `rclone authorize "drive"` 실행 → 출력된 JSON 토큰을 프롬프트에 붙여넣기
- remote 이름 `ApptainerImages`, 폴더 `SignalForge` 는 스크립트가 자동 처리

## 3. Drive 에서 자산 수신

```bash
bash scripts/sync-from-drive.sh            # SIF 6종 (~1GB) + 최신 DB dump + .env.example
# 옵션: --dry-run (목록만) / --no-sif (DB+env 만)
```

## 4. .env 비밀 채우기 (유일한 수동 단계)

```bash
cp .env.example .env
# 원본 서버에서 안전 채널로 복사 (가장 빠름):
#   scp koopark@<원본서버>:~/claude/SignalForge/.env .env
# 또는 직접 편집 — 비밀 12개:
#   POSTGRES_PASSWORD / REDIS_PASSWORD / API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY
#   DEEPL_API_KEY / REDDIT_CLIENT_SECRET / REDDIT_PASSWORD / NAVER_PASSWORD
#   TWITTER_PASSWORD / BLUESKY_PASSWORD / SAMSUNG_PASSWORD
```

포트는 기본값 그대로 (postgres 5434 / backend 18000 / frontend 17370 / redis 6379).

## 5-6. 가동 + 복원 + 검증 — 원샷 자동

```bash
bash scripts/bootstrap-new-server.sh
```

3~6 단계를 전부 자동 수행:
수신(이미 받았으면 skip) → `up.sh` 기동 → 연결 정지 → 최신 dump 복원 → MV refresh
→ backend/worker/beat 재가동 → **검증 게이트 5종 자동** (worker redis banner `:**@` /
health / regression 14 checks / data-quality mx_match / collection-stats) → 결과 표.
게이트 미통과 시 exit 1 + 실패 항목 표시.

옵션: `--no-sync` (Drive 수신 생략) / `--no-restore` (빈 DB, alembic 스키마만)

frontend 는 별도 (dev 모드):

```bash
cd frontend && npm install && npm run dev &    # :17370
```

<details><summary>수동으로 단계별 실행하려면 (참고)</summary>

```bash
bash scripts/up.sh
pkill -f "celery -A celery_app"; pkill -f "uvicorn app.main:app"   # DROP 위해 연결 정지
bash scripts/drive-sync/restore-db.sh ./backups/sf-db-*.sql.gz --yes
# 재가동 + 게이트는 bootstrap-new-server.sh 의 4-5 단계 참조
```

</details>

## 7. 원본 서버 추종 모드 (전환일까지, 선택)

원본이 계속 수집하는 동안 새 서버가 5분마다 자동 따라오게:

```bash
crontab -e
*/5 * * * * /절대경로/SignalForge/scripts/auto-pull.sh >> /tmp/sf_pull.log 2>&1
```

- 원본 celery beat 가 30분마다 dump+`LATEST.json` push (가동 중)
- `auto-pull.sh` 는 sha256 변동 시에만 다운로드→복원→검증, 실패 시 자동 롤백
- **주의: 추종 모드 동안 새 서버의 worker/beat 는 띄우지 말 것** (수집 주체는 한쪽만):

```bash
pkill -f "celery -A celery_app"   # up.sh 가 띄운 worker/beat 중지
```

## 8. 전환일 (cut-over)

1. 새 서버 `auto-pull.sh` 마지막 성공 확인 (voc 카운트 원본과 ±수백)
2. 새 서버: cron 의 auto-pull 제거 → worker/beat 기동 (`scripts/up.sh` 재실행이 간단)
3. 원본: worker/beat 중지 (`pkill -f "celery -A celery_app"`) — 이제 새 서버가 송신 주체 (beat 의 `auto-sync-to-drive-30m` 이 새 서버에서 Drive push 인계)
4. 포털/DNS: HWAX `routes.env` 의 signalforge 대상 IP 를 새 서버로 → `gen-nginx-conf.sh` + reload

## 알려진 환경 의존 (graceful — 없어도 가동)

- `OPENAI_BASE_URL=http://127.0.0.1:11434/v1` (로컬 ollama) — 없으면 LLM 요약만 비활성
- `EXTERNAL_API_KEY` (Groq) / `ALERT_WEBHOOK_URL` (Slack) / `BLUESKY_HANDLE` — 미입력 시 자동 skip
- HWAX 포털 — 없으면 frontend :17370 직접 접속

## 트러블슈팅

| 증상 | 원인/조치 |
|---|---|
| worker 가 곧 죽음, 로그에 `Authentication required` | REDIS_URL env 누락 기동 — `set -a; source .env; set +a` 후 재기동, banner `:**@` 확인 |
| `up.sh` 가 POSTGRES 포트 충돌 | `.env` 의 `POSTGRES_PORT` 변경 |
| restore 후 backend 500 | `REFRESH MATERIALIZED VIEW kpi_overview;` 1회 (psql) |
| 수집 0건 지속 | `redis-cli -a <PW> LLEN celery` 로 큐 확인 + worker 로그 |
