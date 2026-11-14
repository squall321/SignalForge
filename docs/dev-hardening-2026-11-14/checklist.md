# dev 접속 막기 · HN 해시 채우기 (2026-11-14)

## 계획

사용자가 고른 것(2026-11-14) — '둘 다 막기' · '해시만 채우기'. 까닭과 사실은 context-notes.

1. **Postgres(dev)** — 비밀번호를 바꾸고(.env 의 POSTGRES_PASSWORD · DATABASE_URL) `listen_addresses` 를 localhost 로. postgres 와 DB 를
   쓰는 SF 서비스를 워치독 락을 쥐고 다시 띄운다. 시험 파일에 박힌 비밀번호는 환경에서 읽게 고친다.
2. **Redis · Sentinel(dev)** — 시스템 서비스라 사용자가 sudo 로. 루프백과 가상 브리지에만 묶고 protected-mode, 비밀번호 교체.
   그 뒤 SF 의 REDIS_PASSWORD · REDIS_URL 을 맞추고 다시 띄운다.
3. **HN content_hash** — 빈 50,651 행을 수집기와 같은 공식(sha256 앞 16자)으로 채운다. 지우는 행 없음. 드러난 중복 후보는 숫자만.
4. cae00 — 같은 기본 비밀번호인지 확인을 포털에 부탁한다(SignalForge main 묶음이 풀린 뒤 문서로).

## 체크리스트

- [x] Postgres — 새 비밀번호(.env, 백업은 리포 밖 600) · ALTER ROLE · listen_addresses=localhost · 재기동.
      5434 는 루프백만 · 바깥 주소 접속 거절 · 저장된 SCRAM 검증값이 새 비밀번호와 맞고 옛 것과 안 맞음 · 서비스 정상
- [x] .env · backend/.env 권한 644 → 600
- [x] 시험 파일 — 박힌 비밀번호 대신 환경(5c5dbe1 · f143d81, 로컬)
- [x] HN content_hash — 50,651 채움 · 남은 0 · 수집기 공식과 표본 200/200 같음 · test_voc_dedup 통과 ·
      dedup dry-run 중복 후보 610(473 묶음, 0.05%) — 지우지 않음
- [x] Redis 본체 — 루프백 · 가상 브리지에만 · protected-mode · 새 비밀번호 · 설정 파일 저장 · Sentinel 감시용 비밀번호 ·
      SF .env 맞추고 재기동. 옛 비밀번호 거절 · 새 비밀번호 PONG · Sentinel 이 master 정상 · 워커 ping 응답
- [x] Sentinel 자체(묶기 · 자체 비밀번호) — sudo 스크립트(리포 밖). 설정에 requirepass 와 함께 Sentinel 이 적어 둔
      'user default … #옛 해시' 가 있어 둘 다 새 값으로 바꿨다. 옛 비밀번호 거절 · 새 비밀번호 PONG · master 정상.
      redis.conf · sentinel.conf 의 저장된 해시가 새 값뿐(옛 해시 0)이라 재시작해도 옛 비밀번호가 되살아나지 않는다.
      바깥 주소로 5434 · 6379 · 26379 모두 거절
- [ ] 커밋 · 묶음 풀리면 push · 포털에 cae00 확인 부탁
