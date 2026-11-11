# cae00 알림 · 시맨틱 검색 벡터 켜기 (2026-11-11)

## 계획

cae00 에는 Redis 가 없어 worker · beat 가 돌지 않는다. 그래서 알림이 생기지 않고, 시맨틱 검색 벡터에 새 글이 들어가지 않는다.
사용자가 포털(hwaxportal-a5)에서 권장안 넷을 골랐다(11-11) — 비교 · 근거는 context-notes.

1. **알림은 dev 가 계산하고 병합에 싣는다** — `merge-from-drive.sh` 가 고른 규칙(기본 `defect_anomaly`)의 `alert_rules` · `alert_events` 를
   옮긴다. 규칙은 이름으로, 알림은 (규칙 → 대상 rule_id, fired_at)으로 없는 것만 넣는다. cae00 화면에는 운영 알림 넷(collection_health ·
   ops_status_violation · operations_monitor · backup_fail)을 가린다. 병합이 그것을 싣지 않고, 이미 있는 것은 backend 의
   `ALERT_HIDDEN_RULES` 가 화면 · 개수에서 뺀다(dev 는 비워 두어 그대로).
2. **다시 보내지 않는다** — 병합한 알림의 `dispatched_channels` 에 `merged` 를 붙이고, slack-notifier 는 그 줄을 건너뛴다. cae00 은
   웹훅 주소(SLACK_WEBHOOK_URL · ALERT_WEBHOOK_URL)를 비운다 — 바깥으로 보내는 것은 dev 만.
3. **지표 규칙 다섯은 켜지 않는다** — evaluate_alert_rules 는 dev 에도 걸지 않는다(쓰임을 본 뒤 다시 정한다).
4. **벡터는 cae00 cron** — `scripts/embed-new.sh`(build_embeddings 를 600초 예산으로, 커서 이어받기)를 2시간마다. cae00 에 ollama +
   bge-m3 는 사람 몫.

## 체크리스트

- [x] 병합 — alert_rules · alert_events(고른 규칙만, 이름 번역, `merged` 표지, 멱등) + 병합 시험 T8(14/14) — `ebfc4fe`
- [x] slack-notifier 가 `merged` 줄을 건너뛴다 + 시험(실제 SQL 을 TEMP 표에) — `154e2d1`
- [x] backend `ALERT_HIDDEN_RULES` — /alerts/recent · /alerts/rules · /_internal/alert-monitor(목록 · 위반 · 분포 셋) + 시험 — `9e34804`.
      dev 백엔드를 새 코드로 다시 띄워 비어 있을 때 그대로인지 실측(규칙 10 · 모니터 활성 8)
- [x] `scripts/embed-new.sh` — cae00 cron 용 증분 실행기 + `build_embeddings --status` 맞춰 보기 + 시험 — `0055230`.
      dev 실측 — 증분 85행 4.3초 · status 12/12 · 최소 코사인 1.0
- [x] 전체 시험 — 병합 14/14 · crawler 2015 통과(실패 4 는 전부터의 넷) · MCP 189/189 · backend 76 통과 · 실패 14 → GFIT4
      기대 목록 2개를 고쳐(따로 커밋) 12. 12 중 11 은 10-30 실행에도 있던 것(dev SSO 게이트 401 · 데이터 기대값), 1 은
      test_voc_dedup — 6-10 HN 역사 수집 행 50,651 에 content_hash 가 없다(보유율 95.67%, 이번 일과 무관 — 사용자에게 알림)
- [x] cae00 문서 — `docs/DEPLOY_CAE00_2026-11-11-alerts-vectors.md`
- [x] 커밋 · push · 포털 알림(커밋 · 시험 수 · SIF 필요 여부 · cae00 사람 몫 목록 · 문서 경로) — hwaxportal-a5
- [x] 11-11 추가 결정 — cae00 은 지표 규칙 다섯도 가린다. 문서 ALERT_HIDDEN_RULES 를 이름 아홉으로, dev 에서 지표 규칙을 켤 때의
      한 줄(목록에서 빼기 · SF_MERGE_ALERT_RULES 에 더하기) — 커밋 뒤 포털에 그 한 줄을 보낸다
