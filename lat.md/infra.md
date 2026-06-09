# Infra

Apptainer 인스턴스 기반 인프라. [[architecture]] 참조.
모든 서비스는 **host networking**으로 실행되므로 서비스 간 통신은 `localhost:PORT`를 사용한다.

## Services

8개 Apptainer 인스턴스로 오케스트레이션된다.

| 인스턴스 | SIF 이미지 | 포트 | def 파일 |
| --- | --- | --- | --- |
| `sf-postgres` | postgres.sif (docker pull) | 5432 | — |
| `sf-redis` | redis.sif (docker pull) | 6379 | — |
| `sf-backend` | backend.sif | 8000 | `apptainer/backend.def` |
| `sf-celery-worker` | crawler.sif | — | `apptainer/crawler.def` |
| `sf-celery-beat` | crawler.sif | — | `apptainer/crawler.def` |
| `sf-mcp` | mcp.sif | 8001 | `apptainer/mcp.def` |
| `sf-nginx` | nginx.sif (docker pull) | 80 | — |

## Environment Variables

Source: `.env.example`

필수 변수:

- `DATABASE_URL` — `postgresql+asyncpg://signalforge:...@localhost:5432/signalforge`
- `REDIS_URL` — `redis://localhost:6379/0`
- `ANTHROPIC_API_KEY` — Claude API (NLP + MCP)

선택 변수:

- `DEEPL_API_KEY` — DeepL 번역 (없으면 Google Translate 무료 사용)
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` — PRAW API
- `PROXY_URL` — 봇 감지 우회용 프록시

## Quick Start

새 환경에서 SignalForge를 기동하는 최소 명령어 순서.

```bash
cp .env.example .env
# .env 편집 후

./scripts/build.sh all   # SIF 이미지 빌드 (최초 1회)
./scripts/up.sh          # 전체 서비스 시작 (DB 초기화 + 마이그레이션 자동)
```

## Instance Management

Apptainer 인스턴스 기반으로 `scripts/` 디렉토리의 셸 스크립트로 관리한다.

- `scripts/build.sh [all|backend|crawler|mcp|frontend]` — SIF 이미지 빌드
- `scripts/up.sh` — 전체 서비스 시작
- `scripts/down.sh [--all]` — 앱 서비스 중지 (`--all`이면 DB 포함)
- `scripts/status.sh` — 인스턴스 상태 + 포트 확인
- `scripts/logs.sh [서비스명]` — 실시간 로그
- `scripts/db.sh [migrate|seed|reset|psql]` — DB 관리
