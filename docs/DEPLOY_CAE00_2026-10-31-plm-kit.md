# cae00 배포 적용 체크리스트 — 2026-10-31 (PLM 거울 받기를 SP 키트 2026-10-21 로)

이 문서는 **cae00(배포서버)에서 작업하는 AI/운영자**를 대상으로 한다.
같은 날의 [2026-10-31 체크리스트](DEPLOY_CAE00_2026-10-31.md)와 함께 적용한다 — **sf-mcp 재기동은 그 문서 4절과 한 번에** 한다.

## 무엇이 바뀌었나

SP(StandardPlatform)의 코어 연동 키트가 2026-10-21 판(SP main `778db2f`, 받는 쪽 규칙 16 · 시험장 8단계)으로 올라갔다.
SignalForge 의 PLM 거울 받기는 10-14 판을 보고 만든 전체 스냅샷 + 원자 교체였다 — 선(관계)을 안 받고, 공개를 거둔 타입
하나가 전체를 멈추고, 합쳐진 과제의 이긴 쪽을 잃고, 잠금이 없었다. 시험장 1단계부터 못 넘는다.

| 고친 것 | 커밋 |
|---|---|
| 키트를 `crawler/sp_core_kit/` 에 그대로 둔다(SP `778db2f`, 고치지 않는다) | `92e2f7e` |
| 받기 = 키트의 `sp_core_pull.py` 를 자식 프로세스로 — 증분 · 옛 식별자 옮기기 · 지움 · 합침 · 안 보임 · 선 · 처음부터 받은 뒤 부재 대조와 절반 안전장치 · reset · 404 둘 · 정의 판. 감싸는 쪽이 잠금 · 결과 기록(정상 · 확인 필요 · 실패와 사유) · 주 1회 처음부터 받기 · 토큰은 자식 프로세스 환경으로만 | `e5001e4` |
| 조회 색인은 키트 사본(`crawler/data/plm/sp-core/sp_core.sqlite`)의 활성 행을 읽는다. 바뀐 식별자 · 합쳐진 과제의 옛 코드로도 지금 과제를 찾는다 | `e5001e4` |
| `scripts/plm-sync.sh --full` · `--allow-shrink`. 종료 코드 정상 0 · 확인 필요 2 · 멈춤 1. get_ontology 의 plm 에 `last_result` · `problems` | `e5001e4` |
| 시험장 8단계를 crawler 시험으로(`crawler/tests/test_sp_core_testbed.py`) — 모두 통과(1~6 정상 · 7 · 8 확인 필요) | `e5001e4` |

이미지 · `.env` · cron 은 그대로다(crawler 이미지에 requests 가 이미 있다). 거울 자리가 바뀐다 —
`crawler/data/plm/sp_mirror.sqlite`(옛) → `crawler/data/plm/sp-core/`(키트 사본 · 기준 시각 · 카탈로그). VOC DB · Drive
백업 밖인 것은 같다.

## 0. 코드 받기

10-31 문서 0절의 `git pull` 로 함께 들어온다.

```bash
git log --oneline -10 | grep -E "92e2f7e|e5001e4"
```

pull 뒤에는 박스 cron 의 `scripts/plm-sync.sh`(6시간마다)가 다음 차례부터 새 받기를 돈다.

## 1. 처음 받기 — 연결 점검을 겸한다

기다리지 말고 한 번 손으로 처음부터 받는다.

```bash
bash scripts/plm-sync.sh --full; echo "exit=$?"
```

**기대**
- 첫 줄 `키트 2026-10-21 · 시스템 … · 정의 판 … · 공개 타입 [...]`, 타입마다 `plm_task: N건 수신(삭제 n) · p페이지`.
  허브가 관계를 열었으면 `plm_model 관계: …` 줄도 있다.
- 마지막 줄 JSON 의 `"status": "ok"` · `counts`(타입별 활성 수) · `exit=0`.
- `exit=2`(확인 필요)면 `problems` 를 본다. `plm_project: 공개되지 않은 타입` 이면 허브가 프로젝트를 안 연 것이다 —
  과제 · 개발모델 검색은 돌고, 프로젝트 이름으로 보는 주의 칸만 빈다.
- `exit=1`(멈춤)이면 `error` 가 까닭이다 — 인증(401/403, 토큰과 core:read 범위), 주소(404, 끝이 /api 인지), 연결(프록시).

토큰은 지금처럼 `crawler/data/plm/sp_token`(또는 `SF_SP_TOKEN_FILE`)에서 읽어 받기 자식 프로세스의 환경으로만 넘긴다.
`sp-core/config.ini` 에는 주소 · 타입 · 저장 위치만 있다. 키트의 `check.sh` 는 토큰을 curl 명령줄 인자(ps 에 보인다)로
넘기므로 쓰지 않고, 이 처음 받기를 연결 점검(카탈로그 읽기 · 첫 쪽 받기)으로 삼는다.

**옛 거울과 건수 대조**

```bash
apptainer exec --bind "$PWD/crawler:/crawler" apptainer/sif/crawler.sif python3 -c "
import sqlite3, sys
sys.path.insert(0, '/crawler')
from base import plm_mirror as P
old = sqlite3.connect('file:/crawler/data/plm/sp_mirror.sqlite?mode=ro', uri=True)
print('옛', dict(old.execute('SELECT type, count(*) FROM objects GROUP BY type').fetchall()))
print('새', P.replica_counts(P.default_root() / P.MIRROR))" 2>&1 | grep -v '^INFO:'
# 기대: 거의 같은 수(둘 다 지움 · 안 보임을 뺀 활성만 센다). 차이는 옛 거울을 받은 뒤 허브에서 바뀐 만큼
```

## 2. sf-mcp 재기동 — 10-31 문서 4절과 한 번에

새 색인은 sf-mcp 가 다시 떠야 읽는다. 10-31 문서 4절의 sf-mcp 재기동 한 번으로 둘 다 들어간다. 그 전까지 떠 있는
sf-mcp 는 옛 거울 파일을 읽는다 — 새 받기는 그 파일을 고치지 않으니 PLM 은 재기동 때까지 마지막으로 받은 그대로다.
PLM 을 먼저 바꾸려고 sf-mcp 만 따로 띄우면 10-31 문서 '순서' 에 적은 빈 결과가 그동안 생긴다.

## 3. 확인 — get_ontology 의 plm

10-17 문서 1절의 세션 변수 `H` · `SID` · `P` 그대로.

```bash
curl -s "${H[@]}" -H "mcp-session-id: $SID" -X POST "http://127.0.0.1:$P/mcp" \
  -d '{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"get_ontology","arguments":{}}}' \
  | sed -n 's/^data: //p' \
  | python3 -c "import json,sys; r=json.loads(json.loads(sys.stdin.read())['result']['content'][0]['text']); print(json.dumps(r['plm'], ensure_ascii=False))"
# 기대: active true · tasks · models · projects 가 1절의 새 수 · kit 2026-10-21 · last_result ok · problems []
```

과제코드 · 과제명 질의는 예전처럼 풀려야 한다 — 10-23 문서의 확인 질의를 cae00 질의 기록의 실제 이름으로 다시 돈다.

## 4. 정리 · 운영

- **옛 거울 파일** — 3절이 맞으면 `crawler/data/plm/sp_mirror.sqlite` 를 지운다(새 코드는 읽지 않는다).
- **cron 은 그대로**(`17 */6 * * * bash …/scripts/plm-sync.sh`). 주 1회 처음부터 받기는 스크립트가 한다(마지막 처음부터
  받기가 7일을 넘으면). `exit=2`(확인 필요)도 알릴 대상이다 — 예전 `held` 자리다.
- **'절반 넘게 안 와서 정리하지 않았습니다'**(확인 필요) — 토큰의 부서 · 공개 범위가 바뀌었는지 SP 담당자에게 확인한
  뒤, 정말 줄었으면 `bash scripts/plm-sync.sh --full --allow-shrink`. 받은 것은 이미 반영돼 있고 정리만 멈춘 상태다.

## 5. 남은 것

- 합쳐진 과제의 옛 코드로 이긴 과제를 찾는 것은 키트 §2-4 의 '참조를 이긴 쪽으로' 를 고른 것이다(SignalForge 의 참조는
  질의뿐이라 옮길 레코드가 없다). 바꾸려면 사용자 결정.
- 선(관계)은 사본에 받아 두지만 아직 색인에 쓰지 않는다.
