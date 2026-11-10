# cae00 배포 적용 체크리스트 — 2026-11-10 (코어와 잇기 — VOC 부품 · 증상 → 코어 part · failure_mode 대응표)

이 문서는 **cae00(배포서버)에서 작업하는 AI/운영자**를 대상으로 한다.
[2026-11-07 체크리스트](DEPLOY_CAE00_2026-11-07.md)까지 먼저 적용하라. 이 문서는 그 뒤다.

## 무엇이 바뀌었나

사용자가 11-04 결정('SignalForge 는 코어와 잇지 않는다')을 뒤집었다(2026-11-10 — 포털 core-link Q-2 · design §9 · CK-15).
SignalForge 의 VOC 부품 16 · 증상 32 를 코어 `part` · `failure_mode` key 에 잇는 대응표를 받기 뒤마다 짓는다.

| 고친 것 | 커밋 |
|---|---|
| PLM 거울 — 받을 타입 기본값에 `part` · `failure_mode`(그 전에는 plm_project · plm_task · plm_model 셋뿐이라 코어 어휘가 걸러졌다). 사본에서 고른 타입을 읽는다 | `4f76571` |
| `crawler/base/core_link.py` — 대응표 짓기. 이름(key · 한국어 · 영어 ↔ 코어 key · 이름 · 별칭)이 **코어 객체 하나로 정해질 때만** `rule` 선, 여럿이거나 없으면 후보만. 다시 지을 때 `rule` 선만 바꾸고 사람 선은 둔다. 사람 선의 코어 key 가 바뀌면 따라가고, 사라지면 '사용 안 함', 합쳐지면 '확인 필요'. `scripts/plm-sync.sh` 가 받은 뒤 저절로 부른다. 운영 CLI `crawler/scripts/core_link.py` | `5074c99` |
| `scripts/plm-sync.sh` · `scripts/up.sh`(worker) 에 `/shared` 바인드 — SignalForge 부품 · 증상 이름을 `shared/ontology` 에서 읽는다 | `5074c99` |

**대응표는 리포 밖이다** — `crawler/data/plm/core-link/map.json`(대응표) · `review.json`(후보 · 확인 필요 · 사용 안 함). 코어 key ·
이름은 허브 데이터라 공개 리포에 두지 않는다(사용자 결정 2026-11-10). VOC DB 밖이라 Drive 백업 · 덤프에도 안 실린다(PLM 거울과 같다).
이미지 · cron 은 그대로다.

## 0. 코드 받기

update-all(또는 `git pull origin main`). cron 의 `scripts/plm-sync.sh`(6시간마다)가 다음 차례부터 코어 어휘도 받고 대응표를 짓는다
— 스크립트가 `/shared` 를 스스로 바인드하므로 따로 재기동할 것이 없다. beat 로 받는 박스(Redis · worker 가 있는 곳)라면 worker 를
다시 띄워야 `/shared` 가 붙는다(11-07 문서 4절의 워치독 락 쥐기 그대로).

## 1. 받을 타입 — `.env` 가 셋으로 박혀 있지 않은지

```bash
grep -n "^SF_SP_CORE_TYPES" .env || echo "(없다 — 기본값이 part · failure_mode 를 받는다)"
```

값이 있으면 그것이 이긴다. 셋(plm_project · plm_task · plm_model)만 적혀 있으면 `part,failure_mode` 를 더하거나 줄을 지운다.
토큰 범위(`core:read`)가 두 타입을 보여야 한다 — 안 보이면 받기가 '확인 필요: 공개 안 된 타입' 으로 끝난다(종료 코드 2).

## 2. 처음 받기 · 처음 짓기

```bash
bash scripts/plm-sync.sh --full; echo "exit=$?"
```

마지막 JSON 줄의 `core_link` 를 본다.

| `core_link.status` | 뜻 |
|---|---|
| `ok` · `check` | 지었다. `rule`(자동 선) · `candidates`(사람이 볼 개념) · `core`(받은 코어 객체 수) · `check`(확인 필요) |
| `no_core_types` | 사본에 part · failure_mode 가 없다 — 1절(받을 타입 · 토큰 범위) |
| `error` | 까닭을 본다. `ImportError … ontology` 면 `/shared` 바인드가 없는 옛 스크립트다 — 0절의 pull 을 확인한다 |

`core_link` 가 아예 없으면 받기가 끝까지 돌지 못한 것이다(받기 status 가 error · busy · not_configured).

## 3. 초안 확인 — 사람 선 넣기 (BOX-12 와 같은 꼴)

```bash
CL() { apptainer exec --bind "$PWD/crawler:/crawler" --bind "$PWD/shared:/shared" --pwd /crawler \
  apptainer/sif/crawler.sif python3 -m scripts.core_link "$@"; }
CL show                                   # 개념마다 — rule · 사람 · 후보(why · options) · 사용 안 함 · 확인 필요
```

- `rule` 줄이 맞으면 그대로 둔다. 틀리면 사람 선으로 덮는다(사람 선이 이긴다).
- 후보 줄은 사람이 정한다 — `CL set part hinge <코어 key>` · `CL set symptom swelling <코어 key>`.
- 코어에 맞는 것이 없으면 까닭을 단다 — `CL none symptom lag "성능 체감 — 코어 고장 모드에 없음"`.
- 거두기 — `CL unset part hinge`(다음 짓기에서 rule 선 · 후보로 돌아간다).
- `set` 은 사본에 그 key 가 살아 있는지 보고 넣고, 넣은 뒤 바로 다시 짓는다. 사람 선은 다음 받기에서도 남는다.

## 4. 알린다

`crawler/data/plm/core-link/review.json` 의 `counts` · `check` · `unused` 와 사람이 정한 줄 수를 받은 편지함으로 보낸다. 코어 key ·
이름은 허브 데이터라 포털 지시대로 사용자에게만 간다. VOC 본문은 들어 있지 않다.

## 5. 운영

- cron 그대로. 받기마다 `rule` 선을 통째로 다시 짓는다 — 코어가 바뀌면 저절로 따른다.
- **확인 필요** — 합쳐진 코어 key 에 걸린 사람 선이다. `CL show` 의 '→ 이긴 쪽' 을 보고 `CL set` 으로 고친다(저절로 옮기지 않는다 —
  사용자 결정 2026-11-10).
- **사용 안 함** — 사라진 코어 key(또는 SignalForge 에서 사라진 개념)에 걸린 사람 선이다. 지우지 않는다. 다시 정하려면 `CL set` ·
  `CL none` · `CL unset`.

## 6. 남은 것

- 코어 별칭 칸 이름(`ALIAS_FIELDS` — aliases · alias · synonyms · name_en · name_ko)은 짐작이다. 받은 편지함 Q6 의 실물 칸으로 맞추는
  커밋이 뒤따를 수 있다.
- 코어 `part` 가 종류 어휘가 아니라 개별 부품(인스턴스)이면 '부품 16 → key 하나' 꼴이 맞지 않는다 — Q6 답을 보고 다시 정한다.
