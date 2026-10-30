# 참고 구현 — `sp_core_pull.py` 사용법

「{SYSTEM_NAME}」 이 공개한 코어 온톨로지를 받는 쪽 **정답 구현**입니다. 받는 쪽 규칙(`01-AI-GUIDE.md`
§5)을 모두 지킵니다. 이 스크립트가 하는 일은 **이 컴퓨터의 사본(CSV · SQLite)을 SP 와 맞춰 두는 것까지**
입니다 — 그 데이터를 우리 플랫폼의 표 · 화면에 넣는 것(반영)은 따로 만들어야 합니다(`01-AI-GUIDE.md` §6).
처음이면 `00-START-HERE.md` 부터 보십시오.

- 연동 주소(API 루트): `{BASE}` — 카탈로그는 `{BASE}/core`
- 공개 타입: {TYPES_LONG}
- 정의 판(revision): `{REVISION}`
- 키트 판: `{KIT_VERSION}` · 생성 시각: {AT}

**예전에 키트를 받아 쓰고 있다면** `UPGRADE.md` 를 먼저 보십시오 — 파일을 바꾸고 처음부터 한 번
다시 받으면 그동안 어긋난 것이 맞춰집니다.

## 1. 준비 (5분)

```bash
pip install requests
cp config.example.ini config.ini
# config.ini 의 token 에 발급받은 액세스 토큰을 입력합니다(범위 core:read).
```

토큰은 공개 측 관리자가 시스템마다 개별 발급합니다. 파일에 기록하지 않으려면
환경변수 `SP_TOKEN` 을 사용합니다.

## 2. 연결 점검

```bash
./check.sh                          # config.ini 의 base_url · token 을 읽습니다
SP_TOKEN=발급받은토큰 ./check.sh      # 토큰을 파일에 적지 않았으면
```

세 가지를 확인합니다 — 인증 없는 호출이 거부되는가, 카탈로그를 읽을 수 있는가,
첫 페이지를 받을 수 있는가. 여기까지 통과하면 연결은 완료입니다.

## 3. 수신

```bash
python sp_core_pull.py --config config.ini          # 증분 수신
python sp_core_pull.py --config config.ini --full   # 처음부터 다시 수신(끝나면 안 온 것을 정리)
python sp_core_pull.py --version                    # 키트 판
```

첫 실행은 전량을, 이후에는 **직전 수신 이후 변경분만** 받습니다. 처음부터 다시 받으면
(`--full` · 공개 측의 `reset` · 정의 판 변경) **마지막 쪽까지 받은 뒤** 이번에 안 온 객체는
비활성으로, 안 온 선은 지웁니다. 결과는 `out/` 에 저장됩니다.

| 파일 | 내용 |
| --- | --- |
| `out/<타입>.csv` | **수신 이력** — 받은 행을 이어 적습니다(같은 `key` 가 여러 번 나옵니다) |
| `out/<타입>-relations.csv` | 그 타입에서 **출발하는 선**의 수신 이력(관계가 열린 타입만) |
| `out/sp_core.sqlite` | **현재 상태** — 객체는 `key` 기준, 선은 `src·relation·dst·dst_type` 기준으로 갱신됩니다. 삭제 행은 `deleted=1`, **끊긴 선은 지웁니다** |
| `out/sp_core.sqlite` 의 `<타입>__previous_keys` | 바뀐 식별자의 옛 → 새 짝 — 참조 칸에 남은 옛 값을 여기서 찾습니다 |
| `out/state.json` | 어디까지 받았는지(수신 기준 시각) |
| `out/catalog.json` | 직전 카탈로그 — 정의 판이 바뀌면 직전 것은 `catalog.prev.json` 으로 남고, 달라진 타입 · 칸 · 관계가 화면에 찍힙니다 |

객체만 받으면 점만 있고 **선이 없습니다.** 카탈로그의 `relations` 에 값이 있는 타입은
`GET /core/<타입>/relations` 도 같은 규칙(`since` · `next` · `as_of`)으로 받습니다 —
클라이언트가 이미 그렇게 합니다(모든 타입의 객체를 먼저 받고, 선은 그 뒤에).

끝나는 값으로 결과를 알 수 있습니다.

| 값 | 뜻 |
| --- | --- |
| `0` | 정상 |
| `1` | 멈춤 — 인증 · 주소 · 연결 · 서버 오류. 마지막 줄이 까닭입니다 |
| `2` | 끝까지 돌았지만 **사람이 볼 것**이 있음 — 「확인 필요」 줄(공개되지 않은 타입 · 정리를 멈춘 타입) |

## 4. 자동 실행

```cron
0 3 * * *  cd /opt/sp-core-client && SP_TOKEN=... python sp_core_pull.py --config config.ini >> pull.log 2>&1 || echo "코어 수신 확인 필요" | mail -s sp-core 담당자@회사
```

야간 1회를 권장합니다. 끝나는 값이 `0` 이 아니면 알리게 해 두십시오. **수동 실행 경로를 함께
마련하십시오** — 야간 수신이 실패했을 때 다음 날까지 대기하지 않아야 합니다. 주 1회쯤
`--full` 로 받아 두면, 토큰 주인의 소속이 줄어 표식 없이 빠진 것까지 맞춰집니다.

## 5. 우리 플랫폼에 넣으려면

**이 스크립트는 고치지 마십시오.** 이 스크립트는 받기 층이고, `out/sp_core.sqlite` 가 그 사본입니다.
우리 플랫폼에 넣는 반영은 **사본을 읽는 코드를 따로** 둡니다(`01-AI-GUIDE.md` §3 · §6). 그래야 키트를
새 판으로 바꿔도 우리 코드는 그대로입니다. 예전처럼 `save_rows()` 를 고쳐 우리 DB 로 보내면, 식별자
옮김 · 선 끊기 · 정리가 사본만 바꾸고 우리 DB 에는 안 갑니다.

`out/sp_core.sqlite` 의 표:

| 표 | 칸 | 뜻 |
| --- | --- | --- |
| `<타입>` | `key` · `label` · `status` · `updated_at` · `deleted` · `merged_into` · `properties`(JSON) | 객체 — `deleted=1` 이 비활성 |
| `<타입>__relations` | `src` · `relation` · `dst` · `dst_type` · `evidence_note` · `updated_at` · `properties` | 그 타입에서 출발하는 선 — 지금 이어진 것만 |
| `<타입>__previous_keys` | `old_key` · `key` | 바뀐 식별자의 옛 → 새 |

반영 코드는 매번 이 표를 통째로 읽어 우리 표와 대조하는 것이 가장 단순합니다(수만 건까지).

## 6. 수신 측이 지켜야 할 규칙

규칙은 `01-AI-GUIDE.md` §5.6 에 열여섯 가지로 있습니다 — 이 스크립트는 모두 지킵니다. 직접 구현하면
`testbed/` 시험장 8단계로 확인하십시오(`01-AI-GUIDE.md` §8).

## 7. 응답 형식

봉투(고정) + `properties`(공개 타입의 정의에 따름) 두 층입니다.

```json
{
  "as_of": "2026-09-24T02:00:00.000000Z",
  "next": null,
  "items": [
    { "key": "SS400", "label": "일반구조용강", "status": "active",
      "updated_at": "2026-09-23T08:11:00.000000Z", "deleted": false,
      "properties": { "density": 7.85, "supplier": "ACME-001" } },
    { "key": "SS400-2", "renamed_from": "SS400", "previous_keys": ["SS400"],
      "label": "일반구조용강", "updated_at": "2026-09-24T01:10:00.000000Z", "deleted": false,
      "properties": { "density": 7.85 } },
    { "key": "OLD-1", "deleted": true, "merged_into": "SS400-2", "properties": {} }
  ]
}
```

선은 이렇게 옵니다(`GET /core/<타입>/relations`).

```json
{
  "as_of": "2026-09-24T02:00:00.000000Z",
  "next": null,
  "items": [
    { "src": "P-1", "relation": "supplies", "dst": "ACME-001", "dst_type": "vendor",
      "evidence_note": "계약서 3.2", "properties": { "n": 2 },
      "updated_at": "2026-09-23T08:11:00.000000Z", "deleted": false },
    { "src": "P-1", "relation": "supplies", "dst": "HAN-002", "dst_type": "vendor",
      "updated_at": "2026-09-24T01:00:00.000000Z", "deleted": true }
  ]
}
```

`deleted: true` 인 선은 **끊긴 선**입니다 — 자기 쪽에서도 끊으십시오. 양끝 객체의 타입이 모두
공개된 선만 옵니다(한쪽이 공개되지 않았으면 찾을 수 없는 끝점을 받게 되므로).

관계 종류의 끝이 **인터페이스**(여러 타입이 따르는 공통 모양)이면 그것을 구현한 타입 중 공개된
것의 객체를 잇는 선만 오고, `dst_type` 이 선마다 다를 수 있습니다. `key` 는 타입 안에서만 하나라
같은 `dst` 가 두 타입에 있을 수 있으므로, 선은 **`src · relation · dst · dst_type` 넷으로** 구분합니다
— `sp_core_pull.py` 의 SQLite 표가 그렇게 저장합니다(이전 키트가 만든 표는 첫 실행에서 내용을 옮겨
키를 바꿉니다). 무엇이 올 수 있는지는 카탈로그의 `relation_kinds` 가 알려 줍니다.

너무 오래 안 받아 갔으면 대신 이것이 옵니다.

```json
{ "as_of": null, "items": [], "reset": true,
  "reset_reason": "90일보다 오래된 시점부터는 끊긴 선을 알려 줄 수 없습니다 — `since` 를 비우고 처음부터 받으세요." }
```

| 값 | 표기 |
| --- | --- |
| 참조 속성 | 대상 객체의 `key` (`"ACME-001"`) |
| 다중 값 | 배열 (`["A","B"]`) |
| 날짜 · 일시 | `2026-09-24` · `2026-09-24T01:23:45.000000Z` |
| 바뀐 식별자 | `renamed_from` 에 마지막 옛 식별자, `previous_keys` 에 전부(오래된 것부터) |
| 빈 값 | 해당 키가 존재하지 않음 |
| 첨부 파일 | 제공하지 않음 |

전체 명세는 `{BASE}/docs` 에서 확인할 수 있습니다.

## 8. 문제가 발생하면

| 증상 | 확인할 것 |
| --- | --- |
| HTTP 401 · 403 | 토큰 오류 또는 범위 부족(`core:read` 필요) |
| 「주소를 찾지 못했습니다(HTTP 404)」 | `config.ini` 의 `base_url` — 끝이 `/api` 여야 합니다(`/api/core` 아님) |
| 「확인 필요: 공개되지 않은 타입」 | 그 타입의 공개가 거두어졌거나 이름이 틀림 — 카탈로그(`{BASE}/core`)로 확인. 나머지 타입은 받았습니다 |
| 「확인 필요: … 정리하지 않았습니다」 | 처음부터 받았는데 가진 것의 절반 넘게 안 옴 — 토큰의 부서 · 공개 범위가 바뀌었는지 공개 측에 확인. 정말 줄었으면 `--full --allow-shrink` |
| 「커서를 읽을 수 없다는 답이 되풀이됩니다」 | 공개 측에 문의 — 한 번은 저절로 다시 받습니다 |
| 「정의 판이 바뀌었습니다」 | 정상입니다. 달라진 타입 · 칸 · 관계가 그 아래에 찍히고, 전부 처음부터 다시 받았습니다. 자체 DB 의 칸을 맞추십시오 |
| 수신 건수가 0 | 정상입니다. 직전 수신 이후 변경분이 없다는 의미입니다 |
| 처음부터 다시 받고 싶음 | `--full` |
| 「처음부터 다시 받음(reset)」 이 찍힘 | 정상입니다. 너무 오래 안 받아 가서 공개 측이 `reset` 을 보냈고, 전체를 다시 받은 뒤 안 온 것을 정리했습니다. 자주 보이면 수신 주기를 줄이십시오 |
