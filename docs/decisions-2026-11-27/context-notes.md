# 열린 결정 여섯 가지 적용 — 사실 · 결정 (2026-11-27)

## 사용자 결정 (2026-11-27)

- Postgres — 'scram 으로 바꾸기' · 백엔드 — 'dev 만 루프백' · 옛 기본 비밀번호 — '60곳까지 모두' · 백업 — '11-14 에 만든 것만' ·
  서브레딧 — '검색 브랜드 범위에만 넣기' · cae00 Q1 — '같았다 — 바꾸게 하기'.

## 사실 (dev, 읽기만으로 확인)

- pg_hba — `local all all trust`, 루프백 TCP(127.0.0.1/32 · ::1/128) trust(all · replication), 그 밖은 scram. listen 은 localhost.
  루프백 trust 라 이 서버의 다른 사용자 · 프로세스가 비밀번호 없이 슈퍼유저로 붙을 수 있다. 소켓(local)은 인스턴스 안에만 있어
  다른 사용자가 못 닿는다.
- 비밀번호를 넘기는가 — `.env` · `backend/.env` 의 DATABASE_URL 에 비밀번호가 있다. up.sh 가 POSTGRES_* 로 DATABASE_URL 을 지어
  backend · mcp · worker · beat 에 넘기고, 스크립트의 psql · pg_dump 는 PGPASSWORD 를 준다. 크론의 백필 자식 스크립트도 DATABASE_URL
  을 직접 넘긴다. 포털의 'signalforge-mcp' 는 우리 sf-mcp 다(같은 DATABASE_URL).
- 백엔드 — `apptainer/backend.def` 의 %runscript · %startscript 에 `--host 0.0.0.0` 이 박혀 있다(`.env` 의 API_HOST 는 아무도 안 읽음).
  포털 nginx 는 `/signalforge/api/` → `127.0.0.1:18000/api/`, 포털 health 도 127.0.0.1. WS `/ws/realtime` 은 앱 뿌리에 있어
  SSO 게이트(/api/v1) 밖이고 포털 길(/api/)로도 안 닿는다 — 0.0.0.0 이면 바깥에서 바로 닿는다.
- 옛 기본 비밀번호 — Postgres 옛 기본값이 추적 파일 60곳(코드 기본 DSN · 시험 · CI · alembic.ini · 문서), Redis 옛 값이 3곳
  (`.env.example` · STATUS.md · 문서 하나). dev 는 11-14 에 바꿔 무효다. cae00 은 Q1 결과 기본값과 같았다 — cae00 에는 아직 산 값이다.
- 백업 — `~/.config/signalforge/` 에 env.bak-20261114-{pg,redis}(옛 비밀번호), crontab 백업 둘, sentinel-harden.sh. `/etc/redis/` 에
  11-14 백업과 2025 년 백업 10여 개(2025 년 것은 11-14 작업이 만든 것이 아니라 지우지 않는다 — 사용자 결정).
- 서브레딧 — 글의 서브레딧은 `source_url`(`/r/<이름>/`)에만 있다. source_url 색인은 없다(platform_id · external_id 유일 색인뿐).
  질의 해석 — '애플워치 배터리' · '애플 워치' · 'apple watch' → 기기 watch · 브랜드 apple(넓은 범위), '에어팟' → buds · apple,
  '가민 워치' → 브랜드 garmin + watch, '오우라 링' → 제품 OURA, 'whoop strap' → WHP, '레이밴 메타' → MRB(브랜드 대표 제품 코드),
  '픽셀 워치' → PW1(1세대 — 일반명이 1세대로 가는 매처 버릇, 이번 범위 밖). 온톨로지 BRANDS 에 oura · whoop · meta 는 없다.
- 11-27 밤 dev 는 부하가 크다(부하 평균 41, 덤프 COPY 17분 · 집계 쿼리 8분) — 측정 쿼리가 60~90초 상한을 넘겼다. 성능 확인은 한가할 때.

## 결정 · 한 일

- **백업(4)** — 지운 것 셋 — `~/.config/signalforge/env.bak-20261114-pg` · `-redis`, `/etc/redis/sentinel.conf.bak-20261114`(sudo).
  `/etc/redis/redis.conf.backup.20251114_…` 은 2025 년 것이라 손대지 않았다.
- **소켓도 scram(1)** — 소켓 파일이 호스트 경로 `data/postgres-run/` 에 srwxrwxrwx 로 있고 상위 폴더도 남이 지나갈 수 있어
  `local … trust` 로도 다른 사용자가 비밀번호 없이 붙었다. 사용자 결정의 뜻('다른 사용자 · 프로세스의 비밀번호 없는 접속을 막는다')
  대로 소켓 줄까지 바꿨다. 소켓으로 붙는(-h 없는) 호출은 없다(pg_isready 는 인증 안 함).
- **trust 가 틀린 비밀번호를 가려 왔다(1 · 3)** — scram 을 켜고 보니 옛 기본 비밀번호로 붙는 자리가 trust 덕에 통하고 있었다.
  워커 환경에는 DATABASE_URL · POSTGRES_PASSWORD 만 있고 PGPASSWORD 는 없어, `tasks.py` 알림 규칙 평가의 psql 이 옛 값을 썼다.
  크론 백필(kr · global)은 DATABASE_URL 만 넘기는데 스크립트의 psql 은 옛 값을 박아 썼다. MCP 시험 conftest 도 옛 값이었다.
  scram 을 켠 15분(22:52~23:07) 동안 실패한 접속은 0건이었지만, 코드를 고쳐 띄우기 전까지 pg_hba 를 바꾸기 전 판으로 되돌렸다
  (`~/.config/signalforge/pg_hba.conf.scram-20261127` 에 scram 판). 코드 배포 뒤 다시 켠다.
- **비밀번호 도우미(3)** — `crawler/base/pg_env.py` 의 `pg_password()` — PGPASSWORD → POSTGRES_PASSWORD → DATABASE_URL 의 비밀번호,
  다 없으면 RuntimeError. 크론 백필은 DATABASE_URL 만 넘기므로 거기서 꺼내야 한다. 모듈 수준 기본 DSN(health_check · dedup · mcp db)은
  빈 값으로 두고 쓸 때 멈춘다(health_check 는 시험이 import 해서 import 때 멈추면 안 된다).
- **MCP 시험 conftest** — DATABASE_URL 이 없으면 저장소 `.env`(DATABASE_URL, 없으면 POSTGRES_*)로 짓고, 그것도 없으면 닿지 않는
  주소 — DB 시험만 실패하고 해석 · 단위 시험은 돈다. worktree 에는 `.env` 가 없어 시험 때 main 의 DATABASE_URL 을 넘긴다.
- **CI** — 일회용 DB 비밀번호 `ci-only-not-secret`, job env 에 DATABASE_URL(백엔드 설정이 DATABASE_URL 을 기본값 없이 요구한다 —
  예전 CI 는 이것 없이 돌았다).
- **예시 · up.sh** — `.env.example` 의 Postgres · Redis 비밀번호를 비우고, up.sh 는 POSTGRES_PASSWORD 가 비면 만드는 명령과 함께
  멈춘다. Redis 는 시스템 서비스라 requirepass 가 없으면 빈 값이 맞아 멈추지 않는다.
- **새 박스 initdb** — `POSTGRES_INITDB_ARGS=--auth-local=scram-sha-256 --auth-host=scram-sha-256`(새 pgdata 에만). 공식
  엔트리포인트가 초기화 중 psql 에 PGPASSWORD 를 준다(이미지 안 스크립트로 확인).
- **백엔드(2)** — `backend.def` 가 `${API_HOST:-0.0.0.0}` 를 읽는다. 새 이미지를 `backend.sif.new` 로 지어 패키지 74개 판이 같은 것을
  확인하고(pip freeze 같음) 바꿔 끼웠다(옛 것은 `backend.sif.bak-20261127`). dev `.env` 의 API_HOST=127.0.0.1 — 18000 은
  127.0.0.1 만, 바깥 주소는 거절, 포털 nginx(:8088) 길은 그대로(게이트 401).
- **서브레딧(5)** — 브랜드와 기기가 함께 맞을 때('애플워치' · '에어팟' · '가민 워치')와 대표 제품 코드(OURA · WHP · MRB)일 때만.
  브랜드만으로는 넣지 않는다 — '아이폰 배터리' · '애플 폰 배터리' 도 브랜드 apple 로만 풀려(기기 없음) 애플워치 글이 섞였다.
  넓은 범위 + 증상이면 후보에 거는 조건(f_subreddit), 아니면 범위 CTE 의 한 갈래(prank 0.2 — 브랜드 이름 0.25 보다 조금 낮게).
  레딧 플랫폼 id 로 먼저 좁히고 source_url ILIKE 로 가른다(source_url 색인 없음 — 색인은 DB 이관이라 하지 않았다).

## 시험 돌리기 (2026-11-27 뒤)

- **크롤러 DB 시험은 DB 환경을 넘겨야 한다** — health_check · ops_analysis · ops_backlog · refresh_mv · refresh_p3_mvs · reports 의
  10개는 dev DB 에 실제로 붙는 시험인데, 지금까지는 `--cleanenv` 로 환경 없이 돌려도 옛 기본값 + trust 로 붙어 통과했다. 이제는
  `.env` 의 DB 값만 골라(`grep -E '^(DATABASE_URL|POSTGRES_USER|POSTGRES_PASSWORD|POSTGRES_DB|POSTGRES_PORT|POSTGRES_HOST)=' .env`)
  `--env-file` 로 넘긴다. 넘기면 통과한다(11-27 밤 부하 평균 40 에서 MV 새로고침 '30초 이하' 시간 단언 둘만 실패 — 새로고침은 ok).
- **MCP 시험** — worktree 에는 `.env` 가 없어 main 의 DATABASE_URL 과 EMB_DIR 을 넘긴다.
- **start_mcp.sh 의 set -e** — `set -euo pipefail` 이라 `.env` 에 없는 키를 grep 하면 대입이 실패해 조용히 끝난다. `_env` 가
  `|| true` 로 늘 성공하게 했다(없는 POSTGRES_PORT 는 5434, 없는 비밀번호는 분명한 메시지로 멈춤 — 임시 .env 로 확인).
