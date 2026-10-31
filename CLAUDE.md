# SignalForge — 이 리포에서 일할 때의 규칙

## SP(StandardPlatform) 키트는 늘 최신판을 받는다

작업을 시작할 때 SP main HEAD 와 키트 판(`KIT_VERSION`) · `CHANGES.md` 를 확인하고, 바뀌었으면 맞춘다(사용자 지시 2026-10-31).

- **지금 판** — `crawler/sp_core_kit/` = SP `778db2f`(0.4.67)의 `backend/app/modules/coreapi/kit` · `KIT_VERSION = "2026-10-21"`.
  키트 폴더는 고치지 않는다(그 판과 diff 가 없어야 한다).
- **확인** — 복제 없이 된다.

  ```bash
  git ls-remote https://github.com/4leaf321-nox321/StandardPlatform.git refs/heads/main       # 778db2f… 면 그대로
  curl -s https://raw.githubusercontent.com/4leaf321-nox321/StandardPlatform/main/backend/app/modules/coreapi/kit/sp_core_pull.py \
    | grep -m1 '^KIT_VERSION'
  grep -m1 '^KIT_VERSION' crawler/sp_core_kit/sp_core_pull.py                                   # 둘이 같아야 한다
  ```

  HEAD 만 바뀌고 키트가 그대로면 `coreapi`(응답 칸 · 오류 코드)가 받는 쪽에 닿는지 본다.
- **바뀌었으면** — SP 를 스크래치에 새로 복제해(StepForge 의 `external/StandardPlatform` 은 옛 판일 수 있다) 키트 폴더를 통째로
  바꾸고, 위 '지금 판' 과 `crawler/base/plm_mirror.py` 머리말의 판을 고친다. 키트 `UPGRADE.md` · `CHANGES.md` · `01-AI-GUIDE.md`
  대로 받는 쪽 — 층 1 감싸는 쪽(`plm_mirror.sync` — 잠금 · 결과 기록 · 주 1회 처음부터 · 토큰은 자식 프로세스 환경으로만) ·
  층 2(`PlmIndex`) — 을 맞추고 시험장 8단계(`crawler/tests/test_sp_core_testbed.py`) · `crawler/tests/test_plm_mirror.py` ·
  MCP `tests/test_plm_caution.py` · `tests/test_ontology.py` 를 돌린다. SP 리포에는 쓰지 않는다.
- **고정 판** — 서브모듈 · 경계 시험이 없다. 키트 폴더와 위 '지금 판' 이 고정 판이다.
- 같은 SP 키트를 받는 다른 플랫폼과 **같은 판**에 둔다 — 올리면 포털(HWAX) 세션에 커밋을 알린다.
- **cae00** — 받는 쪽이 바뀌면 날짜별 적용 문서(`docs/DEPLOY_CAE00_<날짜>.md`)에 처음 받기(`bash scripts/plm-sync.sh --full`)와
  확인을 적는다. 공개 리포라 허브 주소 · 토큰 · 가린 이름표 · 과제코드는 적지 않는다.
- 기록 — 2026-10-31 키트 2026-10-21 판으로 바꾼 것은 `docs/plm-ontology/checklist.md` · `context-notes.md` 의 'SP 키트 2026-10-21
  맞추기' 절.
