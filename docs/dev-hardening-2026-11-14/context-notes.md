# dev 접속 막기 · HN 해시 채우기 — 사실 · 결정 (2026-11-14)

## 사실 (dev 실측)
- **Postgres** — `listen_addresses='*'`(pgdata 의 postgresql.conf). pg_hba 는 루프백(로컬 소켓 · 127.0.0.1 · ::1)이 trust,
  그 밖의 주소는 scram. 그래서 위험은 바깥 주소로 붙어 공개된 기본 비밀번호(.env.example 과 같았다)로 슈퍼유저가 되는 길이다.
  바깥 주소 접속은 0. 포털 백업은 인스턴스 안에서 127.0.0.1 로 붙고 비밀번호는 SignalForge .env 에서 읽는다.
- 그 비밀번호를 박은 곳 — .env.example · CI · alembic.ini · 크롤러 모듈의 기본 DSN · 시험 파일 셋(첫 커밋부터 공개 히스토리).
  다른 프로젝트 · 추적 안 되는 파일에는 없다. alembic env.py 와 크론은 DATABASE_URL · .env 를 읽는다.
- `sync-to-drive.sh` 가 Drive 로 보내는 것은 .env.example 이다(.env 아님) — dev 비밀번호를 바꿔도 cae00 에 가지 않는다.
- **Redis** — 시스템 서비스(redis 사용자 · /etc/redis), 비밀번호는 있으나 모든 인터페이스 · protected-mode no. 비밀번호는
  .env.example 기본값과 같았다. Sentinel 도 시스템 서비스로 모든 인터페이스에서 듣고 같은 비밀번호이며, 가상 브리지 주소로
  Redis 를 감시한다(동료 sentinel 0 · quorum 2 · 복제 0 — 넘김은 일어나지 않는다). 다른 클라이언트는 모두 루프백.
  NewsKooo 기본값은 비밀번호 없는 localhost:6379 라 지금도 쓰지 못한다(바꿔도 더 깨지지 않는다).
- **HN content_hash** — 빈 행 50,651 은 모두 2026-06-10 HN 연도 슬라이싱 역사 수집 행(활성 32,474). 그 뒤 HN 행은 해시가 있다.
  색인 idx_voc_content_hash 는 유일 색인이 아니다. 채우면 빠진 행끼리 357 · 이미 해시가 있는 글과 331 이 겹친다(최대 약 690).

## 결정
- **루프백 trust 는 그대로 둔다** — 이번에 고른 것은 비밀번호 교체 · 묶기다. trust 를 scram 으로 바꾸면 비밀번호 없이 붙던
  것이 깨질 수 있어 따로 묻는다.
- **Redis 는 루프백 + 가상 브리지에만** — 루프백만이면 Sentinel 감시가 끊긴다. 가상 브리지 주소는 `-` 접두로 없어도 기동되게.
- **해시는 채우기만** — 중복 정리(dedup_voc --execute)는 지우는 일이고, 병합은 dev 가 지운 행을 cae00 에서 지우지 않아 두
  서버가 갈라진다. 숫자만 보고한다.

## 짓는 동안 정한 것
- **비밀번호는 화면 · 명령줄에 남기지 않는다** — psql · redis-cli 에는 표준입력으로 넘기고, 확인은 저장된 SCRAM 검증값을 Python 으로
  견줘 '맞음/안 맞음' 만 찍었다. 루프백이 trust 라 접속 성공만으로는 비밀번호가 바뀌었는지 알 수 없어서다.
- **Redis 본체는 sudo 없이** — 인증된 연결에서 CONFIG SET(bind · protected-mode · requirepass) 뒤 CONFIG REWRITE 로 설정 파일에
  저장했다. 이미 붙은 연결은 인증이 유지돼 SF 가 멈추지 않는다(설정을 고치고 재시작하면 .env 를 바꾸기 전까지 끊긴다). 계획은
  '사용자 sudo' 였지만 사용자 몫이 줄어드는 쪽으로 바꿨다. Sentinel 의 묶기 · 자체 비밀번호는 재시작이 필요해 사용자 sudo 로 남겼다.
- **.env 권한** — 644 라 다른 계정도 읽을 수 있었다. 읽는 쪽은 모두 같은 계정이라 600 으로 좁혔다(backend/.env 도).
- 해시 표본 대조에서 1/20 이 어긋나 보였던 것은 psql 의 마지막 레코드 줄바꿈 탓이었다 — JSON 으로 받으니 200/200 같았다.

## 남은 것 (사용자에게 묻는다)
- 루프백 trust → scram, .env.example 의 기본 비밀번호(새 박스가 그대로 쓴다) · 크롤러 모듈의 기본 DSN · 옛 문서에 남은 옛 값(이제 무효).
