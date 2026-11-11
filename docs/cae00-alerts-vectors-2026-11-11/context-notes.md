# cae00 알림 · 벡터 켜기 — 결정 · 까닭 (2026-11-11)

## 사실 (dev 실측 · 코드)
- **알림 화면은 DB 를 읽는다** — GET /alerts/recent 가 `alert_events` 를, 운영 패널이 /_internal/alert-monitor 의 규칙별 개수를 읽는다.
  WS(/alerts/ws)는 backend 프로세스가 직접 보낸 것만 민다(/alerts/test 평가 때). worker 가 INSERT 한 알림은 dev 에서도 WS 로 가지
  않고 화면을 열 때 보인다.
- **dev 에서 나는 알림(최근 7일)** — 사용자용 defect_anomaly 5(6시간마다)와 운영 알림 넷(collection_health 58 · ops_status_violation 53
  · operations_monitor 37 · backup_fail 3). 모두 worker beat 가 쓴다.
- **화면의 지표 규칙 다섯(RuleEngine)은 dev 에서도 0건** — 그것을 도는 `evaluate_alert_rules`(backend /alerts/test 를 부른다)가 beat ·
  cron 어디에도 걸려 있지 않았다(celery_app 에 한 번도 들어간 적이 없다).
- **바깥 알림** — slack-notifier(5분)가 `dispatched_channels` 에 slack · slack:dry · slack:fail 이 없는 알림을 보낸다. 웹훅이 비면
  slack:dry 만 붙인다. dev 의 알림은 지금 slack:dry 다.
- **글 id 가 다르다** — 병합은 글을 (플랫폼 code, external_id)로 번역해 넣어 cae00 의 글 id 가 dev 와 다르다. 벡터 조각(5만 행 ·
  약 4.9GB)의 id 는 그 박스의 글 id 다. 질의 임베딩도 그 박스의 ollama bge-m3 가 한다.
- **defect_anomaly 의 payload 는 제품 code 를 쓴다** — 정수 id 가 없어 번역할 것이 없다. alert_rules.name 은 유일 색인이 있다.

## 결정 (사용자 11-11 — 포털이 전함, 넷 모두 권장안)
- **알림은 dev 가 계산해 병합에 싣는다(①)** — 지금 '수집은 dev, cae00 은 병합' 꼴과 같다. 계산이 한 곳이라 두 박스의 알림이 어긋나거나
  바깥으로 두 번 가지 않는다. 늦음은 30분 덤프 + 병합만큼이다. cae00 cron 으로 따로 계산하는 길(②)은 운영 알림을 거기서 돌리면 엉뚱하게
  나고(수집 · worker 가 없다), Redis + beat(③)는 수집 · 번역까지 돌아 병합 모델을 깬다.
- **운영 알림 넷은 cae00 화면에서 가린다** — 병합이 싣지 않는다(새로 오지 않는다). 그래도 cae00 DB 에 예전 것이 남아 있으면 화면
  목록(최근 50)에 끼고 개수에 잡히므로 backend 에 `ALERT_HIDDEN_RULES` 를 두어 /alerts/recent · /alerts/rules · alert-monitor 에서 뺀다.
  지우지 않는다(병합은 비파괴). dev 는 비워 두어 화면이 그대로다.
- **다시 보내지 않는 것은 두 겹** — cae00 은 웹훅 주소를 비운다(사용자 결정). 병합한 알림에는 `merged` 를 붙이고 slack-notifier 가
  건너뛴다 — 덤프 직전에 생겨 표지 없이 온 알림도, 누가 나중에 cae00 에 웹훅을 넣어도 다시 가지 않는다.
- **지표 규칙은 켜지 않는다** — 쓰임을 본 뒤 다시 정한다.
- **벡터는 cae00 cron(②)** — id 가 cae00 것이라 번역이 필요 없다. dev 벡터를 싣는 길(①)은 4.9GB 와 조각마다 id 다시 쓰기가 들고,
  그래도 질의 임베딩용 ollama 는 cae00 에 있어야 한다.

## 짓는 동안 정한 것
- **가리기는 이름으로, SQL 에서** — `name <> ALL(CAST(:hidden AS text[]))`. 빈 배열이면 ALL 이 늘 참이라 dev 는 질의 결과가 그대로다.
  alert-monitor 는 규칙 목록만 거르면 cooldown 위반 합계 · metric 분포에 가린 규칙이 남아 셋 다 거른다. 가린 이름은 payload 함수의
  인자로 넘겨 redis 캐시 키에 들어간다(설정을 바꾸고 다시 띄운 직후 옛 캐시를 보지 않게).
- **백엔드 설정은 `.env` 에서 바로 닿는다** — up.sh 가 load_env(set -a)로 내보내고 apptainer 가 환경을 넘긴다. dev uvicorn 의
  /proc/environ 에 루트 .env 의 키가 있는 것을 확인했다. backend/.env(up.sh 가 쓰는 파일)에 줄을 더할 필요가 없다.
- **백엔드 시험은 TEMP 표로** — 세션 TEMP 표가 같은 이름의 진짜 표를 가린다(slack-notifier 시험과 같은 수). `LIKE … INCLUDING
  INDEXES` 여야 한다 — 기본 키가 없으면 `GROUP BY r.id` 가 다른 열을 못 끌어와 GroupingError. DB 확인용 연결은 같은 루프 안에서
  engine.dispose() 해야 다음 asyncio.run 이 닫힌 루프의 연결을 쓰지 않는다.
- **dev 실시간 알림 시험 10개는 401** — dev 백엔드는 포털 SSO 게이트가 켜져 있고(PORTAL_JWKS_URL) 옛 시험은 X-API-Key 를 보내지
  않는다. 기본 주소(:8000)로 돌리면 건너뛰는 시험들이라 이번 일과 무관하다. 대신 X-API-Key 로 세 엔드포인트를 직접 불러 확인했다.
- **맞춰 보기를 넣은 까닭** — 10-17 문서가 '덤프 복원 구조면 dev 벡터를 복사해도 된다' 고 했다. 그 시절 복사해 둔 저장소가 cae00 에
  있으면, 병합으로 갈라진 뒤쪽 id 부터 다른 글에 벡터가 붙고 커서도 dev 값이라 증분이 조용히 멈춘다. 저장된 벡터를 같은 id 의 지금
  본문으로 다시 임베딩해 견주면 확실히 가려진다(같은 글 1.0, 다른 글은 한참 낮다). 첫 · 가운데 · 마지막 조각에서 고르는 것은
  복원 뒤 갈라진 꼴이 뒤쪽에만 나타나기 때문이다. 본문이 바뀐 행도 differ 로 나오지만 그때도 다시 채우는 쪽이 안전하다.
- **embed-new.sh 는 DB 주소를 환경으로** — `--env DATABASE_URL=…` 은 ps 에 비밀번호가 보인다. 예산만 `--env` 로 넘긴다. 프록시는
  export_proxy 가 NO_PROXY 에 127.0.0.1 을 넣어 ollama 가 프록시를 타지 않는다.
- **MCP 결함 급등 도구도 덕을 본다** — `defect_anomalies` 가 `payload->>'type' = 'defect_anomaly'` 로 alert_events 를 읽는다. dev 의
  8건 모두 그 type 이라 cae00 은 첫 병합 뒤부터 빈 목록이 아니다.
- **SIF · Drive 는 필요 없다** — 바뀐 것은 스크립트 · 코드 · 문서다. crawler.sif 의 numpy 는 10-27(SG-01) 이미지에 이미 있다.
