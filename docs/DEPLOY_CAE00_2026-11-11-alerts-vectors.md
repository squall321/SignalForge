# cae00 배포 적용 체크리스트 — 2026-11-11 (결함 급등 알림 · 시맨틱 검색 벡터 증분)

이 문서는 **cae00(배포서버)에서 작업하는 AI/운영자**를 대상으로 한다.
[2026-11-10 체크리스트](DEPLOY_CAE00_2026-11-10-core-link.md)까지 먼저 적용하라. 이 문서는 그 뒤다.

## 무엇이 바뀌었나

cae00 에는 Redis · worker · beat 가 없다. 알림을 계산하는 beat 작업도, 새 글을 임베딩하는 beat `embed-new-2h` 도 돌지 않아
알림 화면은 비어 있고 시맨틱 검색의 벡터는 멈춰 있었다. 사용자가 2026-11-11 다음처럼 정했다(포털 경유).

- **알림 — dev 가 계산하고 병합이 싣는다.** 싣는 것은 사용자용 결함 급등(`defect_anomaly`)뿐이다. 운영 알림 넷(수집 상태
  `collection_health` · 운영 위반 `ops_status_violation` · 운영 감시 `operations_monitor` · 백업 `backup_fail`)은 dev 의 일이라
  싣지 않고 cae00 화면에서도 가린다. dev 화면은 그대로다.
- **바깥 웹훅은 dev 만 보낸다.** cae00 은 웹훅 주소를 비운다. 실린 알림에는 `merged` 표시가 붙고 발송기가 그것을 건너뛴다.
  두 겹이라 언젠가 cae00 에 worker 를 띄워도 다시 보내지 않는다.
- 지표 규칙 다섯(`anomaly_z_high` · `negative_rate_spike` · `new_term_spike` · `platforms_negative_share` ·
  `platforms_negative_share_watch`)은 꺼 둔 채다. dev 에서도 평가를 스케줄하지 않고, cae00 화면에서도 가린다. **cae00 화면에는
  결함 급등만 보인다.**
- **벡터 — cae00 이 자기 VOC id 로 직접 짓는다.** cron 이 2시간마다 600초씩 커서를 이어받아 임베딩한다. cae00 은 병합으로 받아
  글의 id 가 dev 와 다르다. **dev 벡터를 복사해 오지 않는다** — 10-17 문서 4절의 '덤프 복원 구조면 복사해도 된다' 는 이제
  해당하지 않는다.

| 고친 것 | 커밋 |
|---|---|
| 병합이 `alert_rules` · `alert_events` 를 싣는다. 고른 규칙만(`SF_MERGE_ALERT_RULES`, 기본 `defect_anomaly`), 규칙은 이름으로 맞추고(dev 가 정본) 알림은 (규칙, 발화 시각)이 없을 때만 넣는다. `dispatched_channels` 에 `merged` | `ebfc4fe` |
| 발송기(slack_notifier)가 `merged` 를 건너뛴다 | `154e2d1` |
| 백엔드 `ALERT_HIDDEN_RULES`(쉼표) — 적은 규칙을 알림 화면 셋(규칙 목록 · 최근 알림 · 운영 모니터링)의 목록과 합계에서 뺀다. 행은 지우지 않는다. 비면 다 보인다 | `9e34804` |
| `scripts/embed-new.sh` — 벡터 증분(cron) · 처음 채우기 · `--status`(남은 행 · 이 DB 로 지은 저장소인지 맞춰 보기) | `0055230` |

**이미지(SIF) · Drive 는 그대로다.** 코드와 `.env` 만 바뀐다. 사람 몫은 ollama · bge-m3(4절)와 cron 한 줄(6절)이다.

## 0. 코드 받기

```bash
git pull origin main          # 또는 update-all
git log --oneline -10 | grep -E "ebfc4fe|154e2d1|9e34804|0055230"
```

병합 스크립트는 다음 병합부터 새 코드로 돈다. 따로 띄울 것은 없다. 백엔드는 1절에서 `.env` 를 바꾼 뒤 다시 띄운다.

## 1. `.env` — 웹훅 주소를 비운다 · 결함 급등 말고는 가린다

웹훅 주소를 비운다. 그리고 가릴 규칙 아홉(운영 알림 넷 · 지표 규칙 다섯)을 적는다.

```bash
awk -F= '/^(SLACK_WEBHOOK_URL|ALERT_WEBHOOK_URL)=/{print $1, ($2=="" ? "비어 있음" : "값 있음")}' .env   # 주소 자체는 찍지 않는다
sed -i -E 's/^(SLACK_WEBHOOK_URL|ALERT_WEBHOOK_URL)=.*/\1=/' .env
sed -i '/^ALERT_HIDDEN_RULES=/d' .env          # 앞서 넷만 넣었어도 아홉으로 바뀐다
echo 'ALERT_HIDDEN_RULES=collection_health,ops_status_violation,operations_monitor,backup_fail,anomaly_z_high,negative_rate_spike,new_term_spike,platforms_negative_share,platforms_negative_share_watch' >> .env
awk -F= '/^(SLACK_WEBHOOK_URL|ALERT_WEBHOOK_URL)=/{print $1, ($2=="" ? "비어 있음" : "값 있음")}' .env; grep '^ALERT_HIDDEN_RULES=' .env
# 기대: 둘 다 '비어 있음'(줄이 없어도 된다) · ALERT_HIDDEN_RULES= 이름 아홉
```

가린 규칙의 행은 지우지 않는다. 나중에 dev 에서 지표 규칙을 켤 때는 9절을 본다.

백엔드만 다시 띄운다. 백엔드는 `.env` 를 환경으로 물려받는다(up.sh 의 load_env). 11-07 문서 4절처럼 워치독 락을 쥔다.

```bash
( exec 9>/tmp/sf-watchdog.lock; flock -w 90 9 && exec -a "watchdog.sh(maint-hold)" sleep 600 ) &
HOLD=$!
apptainer instance stop sf-backend
for t in $(seq 1 60); do ps -eo args | grep -q "[A]pptainer instance: $(id -un) \[sf-backend\]" || break; sleep 1; done
bash scripts/up.sh 2>&1 | grep -E "✓|⚠|→ instance"
kill $HOLD
```

**검증**

```bash
set -a; source .env; set +a
B="http://127.0.0.1:${API_PORT:-8000}/api/v1"
curl -s -H "X-API-Key: $API_KEY" "$B/alerts/channels" | python3 -c "import json,sys; print(json.load(sys.stdin)['slack'])"
# 기대: 'enabled': False, 'dry_run': True — 웹훅을 비웠다
curl -s -H "X-API-Key: $API_KEY" "$B/alerts/rules" | python3 -c "import json,sys; print(sorted(r['name'] for r in json.load(sys.stdin)))"
# 기대: ['defect_anomaly'] (첫 병합 전이고 그 규칙이 아직 없으면 []). 다른 이름이 보이면 이 박스에서 따로 만든 규칙이다 — 그것도 위 줄에 더한다
curl -s -H "X-API-Key: $API_KEY" "$B/_internal/alert-monitor?days=7" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['summary'], [r['name'] for r in d['rules']])"
# 기대: rules 는 ['defect_anomaly'] 하나, summary 의 발화 수도 결함 급등 것만
```

## 2. 알림이 실리는지 — 다음 병합

dev 덤프(30분마다)가 병합되면 병합 출력에 '✓ 알림' 줄이 생긴다. 기다리지 않으려면 손으로 한 번 병합한다(10-27 문서 2절).

```bash
bash scripts/drive-sync/merge-from-drive.sh 2>&1 | grep -E "알림|표본 대조"
# 기대(첫 병합): ✓ 알림 — 규칙 +1 ~0 · alert_events +<dev 의 결함 급등 수> (싣는 규칙: defect_anomaly)
#   규칙이 이미 있으면 +0 ~1(정의가 달랐다) 또는 +0 ~0. 다음 병합부터는 새로 발화한 것만 + 가 붙는다
```

```bash
PGPASSWORD=$POSTGRES_PASSWORD apptainer exec instance://sf_postgres psql -h 127.0.0.1 -p $POSTGRES_PORT -U $POSTGRES_USER -d $POSTGRES_DB -Atc "
SELECT r.name, count(e.id), count(e.id) FILTER (WHERE 'merged' = ANY(e.dispatched_channels)), max(e.fired_at)::date
FROM alert_rules r LEFT JOIN alert_events e ON e.rule_id = r.id GROUP BY r.name ORDER BY 1;"
# 기대: defect_anomaly 의 알림은 모두 merged. 다른 규칙에 옛 알림이 있으면(박스를 처음 깔 때 들어온 것) 화면에서는 1절이 가린다
```

**화면** — 알림 페이지의 최근 알림에 결함 급등만 보이고, 채널 칸에 `merged` 꼬리표가 붙는다(dev 에서 실려 온 알림). 규칙
목록과 운영 모니터링에도 결함 급등 하나만 남는다(운영 알림 넷 · 지표 규칙 다섯은 1절이 가린다). 실시간 배지(WebSocket)는 dev 와
같이 백엔드가 직접 낸 알림만 민다. 병합 알림은 목록에 보인다.

MCP `defect_anomalies` 도구도 같은 알림을 읽는다. 첫 병합 뒤부터 빈 목록이 아니다.

## 3. 벡터 저장소 — 지금 무엇이 있나

```bash
bash scripts/embed-new.sh --status
# 예(dev): {"vectors": 1274998, "cursor": 4573644, "remaining": 50, "alignment": {"checked": 12, "same": 12, "missing": [], "differ": [], "min_cos": 1.0}}
```

| 출력 | 뜻 · 할 일 |
|---|---|
| `"vectors": 0, "cursor": 0` | 저장소가 없다. 4절(ollama) 뒤 5절로 처음부터 채운다 |
| `alignment` 의 `same` 이 `checked` 와 같고 `differ` · `missing` 이 비었다 | 이 DB 로 지은 저장소다. 5절은 건너뛰고 6절 cron 으로 이어 간다 |
| `differ` 나 `missing` 이 있다 | **다른 DB(dev)에서 옮겨 온 저장소다.** 같은 id 에 다른 글이 붙어 있다. 아래처럼 치우고 처음부터 채운다 |
| `alignment` 에 `error` | ollama · bge-m3 가 없어 맞춰 보지 못했다. 4절 뒤 다시 본다 |
| `ModuleNotFoundError: numpy` | 옛 crawler 이미지다. 10-27 문서 1절의 이미지로 바꾼다 |

```bash
mv crawler/data/vectors/bge-m3 crawler/data/vectors/bge-m3.moved-$(date +%Y%m%d)   # 지우지 않고 치운다(약 5GB). 다 채운 뒤 지운다
```

`alignment` 는 첫 · 가운데 · 마지막 조각에서 12행을 골라, 지금 DB 의 같은 id 본문을 다시 임베딩해 저장된 벡터와 견준다.
코사인 0.98 미만이면 `differ` 다. 같은 글이면 1.0 에 가깝다(dev 실측 12/12 · 최소 1.0). 덤프 복원 뒤 병합으로 갈라진 저장소는
뒤쪽 조각에서 걸린다.

## 4. ollama · bge-m3 (사람 몫)

```bash
command -v ollama && ollama --version
curl -s --max-time 5 http://127.0.0.1:11434/api/tags | python3 -c "import json,sys; print([m['name'] for m in json.load(sys.stdin)['models']])"
# 기대: 'bge-m3:latest' 가 있다. 없으면 ollama pull bge-m3 (약 1.2GB) — 첫 pull 이 503 이면 다시
curl -s --max-time 120 http://127.0.0.1:11434/api/embed -d '{"model":"bge-m3","input":["배터리가 빨리 닳아요"]}' \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)['embeddings'][0]))"
# 기대: 1024
ollama ps          # PROCESSOR 칸이 GPU 인가 CPU 인가 — 5절의 시간이 갈린다
```

ollama 가 다른 주소에 있으면 `.env` 에 `EMB_URL=http://<호스트>:<포트>` 를 넣는다. embed-new.sh 와 sf-mcp(질의 임베딩)가 둘 다
이 값을 읽는다(sf-mcp 는 다시 띄워야 읽는다). 은유 해석용 `qwen2.5:7b` 는 이번 일과 따로다(10-17 문서 4절, 선택).

## 5. 처음 채우기 (한 번)

```bash
nohup bash scripts/embed-new.sh 0 > logs/embed-first.log 2>&1 &     # 0 = 예산 없이 끝까지
tail -f logs/embed-first.log      # 2만 행마다 '누적 … 건/초', 끝나면 '완료 {… 'stopped': 'end' …}'
```

**예상 시간** — 3절의 `remaining`(활성 행 수) ÷ 처리 속도.

- GPU — dev(RTX 5070 Ti) 실측 약 230건/초. 127만 행이 약 1.5시간이다.
- CPU 만 — dev 에서 재지 않았다. `bash scripts/embed-new.sh 300` 을 한 번 돌려 '완료' 줄의 `scanned ÷ elapsed_sec` 으로
  건/초를 잰다. 30건/초면 약 12시간, 10건/초면 약 35시간이다. 다른 서비스가 느려지면 `EMB_WORKERS=1 nohup bash …` 로 동시
  요청을 줄인다.

끊겨도(재부팅 · kill) 다시 돌리면 manifest 의 커서부터 이어 간다. 도는 동안 cron 이 불러도 저장소 잠금으로 건너뛴다('다른 적재가
돌고 있다'). 채우는 중에도 MCP 는 manifest 가 바뀔 때마다 다시 읽어 그때까지의 벡터로 검색한다. 디스크는 약 5GB 쓴다.

## 6. cron 한 줄

```
37 */2 * * *  cd ~/Projects/SignalForge && bash scripts/embed-new.sh >> logs/embed-new.log 2>&1
```

2시간마다 600초 예산으로 커서부터 이어 간다(dev beat `embed-new-2h` 와 같은 일). 처음 채우기를 cron 에만 맡길 수도 있다. 하루
7,200초라 GPU 면 하루 안에 끝나고 CPU 면 여러 날 걸린다. 종료 코드는 정상 · 건너뜀 0, 임베딩 실패 1 이다. 실패해도 그 앞까지는
썼고 다음 차례가 이어 간다. 계속 1 이면 4절의 ollama 를 본다.

## 7. 끝났는지 본다 (읽기만)

```bash
bash scripts/embed-new.sh --status
# 기대: "remaining": 0 · alignment 12/12 — 다 따라잡았다. 그 뒤로는 병합으로 새로 들어온 행만 cron 이 2시간마다 채운다
grep "완료" logs/embed-new.log | tail -1      # 'stopped': 'end' 면 끝까지 갔다. 'budget' 이면 예산에서 멈추고 다음 차례에 잇는다
```

시맨틱 검색이 벡터를 쓰는지는 10-17 문서 4절의 검증(`semantic_search` '폰이 손난로 같아요' → `vector.used` True)으로 본다.

## 8. 알린다

받은 편지함으로 보낸다 — 2절의 '✓ 알림' 줄, 1절 검증 세 줄의 결과(규칙 이름 목록과 summary), 7절 `--status` 한 줄, 처음
채우기에 걸린 시간과 GPU/CPU. VOC 본문은 들어 있지 않다. 웹훅 주소 · 토큰 · 내부 주소는 적지 않는다.

## 9. 운영

- 싣는 알림 규칙을 바꾸려면 `.env` 에 `SF_MERGE_ALERT_RULES=defect_anomaly,…` 를 넣는다(비우면 알림을 싣지 않는다). 병합이
  다음 차례에 읽는다.
- 가리는 규칙을 바꾸려면 `ALERT_HIDDEN_RULES` 를 고치고 백엔드를 다시 띄운다(1절). 가린 알림의 행은 DB 에 그대로 있다.
- **나중에 dev 에서 지표 규칙을 켜면, cae00 에서는 그 이름을 `ALERT_HIDDEN_RULES` 에서 빼야 보인다.** 그 알림까지 cae00 에
  실으려면 `SF_MERGE_ALERT_RULES` 에도 더한다(병합은 거기 적힌 규칙만 싣는다).
- dev 가 결함 급등 규칙의 임계 · 설명을 바꾸면 다음 병합에서 따라온다(`규칙 +0 ~1`).
- 보관(archive)된 행의 벡터는 저장소에 남고 검색 SQL 이 걸러낸다. dev 와 같다.
