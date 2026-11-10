# 과제·개발모델 온톨로지 — 과제코드·개발명·모델코드로 VOC 찾기 (2026-10-23)

## 계획

사용자 요청 — 과제코드·개발명(과제명) 같은 사내 식별자로 검색해도 그 제품의 VOC 를 찾게 한다.
참고 사례 — StepForge 가 StandardPlatform(SP) 허브의 코어 API 로 과제(`plm_task`)·개발모델(`plm_model`)·
프로젝트(`plm_project`)를 받아 읽기 전용 거울로 둔다(`StepForge/docs/STANDARD_PLATFORM.md`, `core/sp_core.py`).

사용자 결정(10-23)
1. 원천 — **SP 허브 코어 API**(`core:read` 토큰).
2. 보관 — **VOC DB 밖 별도 파일.** dev DB 는 30분마다 Google Drive 로 백업되고 cae00 으로 덤프가 간다.
   과제코드·과제명은 사내 정보라 거기 싣지 않는다. 허브를 볼 수 있는 서버가 직접 받는다.

다리가 먼저다 — 과제·개발모델은 SignalForge 제품(GS25U 등)을 모른다. 이어 주는 것은 개발모델의
**기본 모델코드**(SM-S938)인데, SignalForge 에는 모델코드 사전이 없다(product_match·레거시 사전·products 표
모두 0). 그 사전은 VOC 본문 태깅에도 쓴다 — 최근 13만 행 중 모델코드가 든 글 377행, 그중 25% 미태깅.

| 층 | 무엇 | 어디 |
|---|---|---|
| 모델코드 사전 | `SM-<문자><숫자3>` → 제품 코드 | `crawler/base/model_codes.py`(정본) |
| 본문 태깅 | 모델코드만 쓴 글을 그 제품에 | `infer_all_product_codes` 길목 |
| 질의 해석 | 'SM-S938B 발열' → S25 Ultra | `shared/ontology.py` |
| PLM 거울 | 과제·개발모델·프로젝트 전체 스냅샷 → SQLite 파일(원자 교체) | `crawler/base/plm_mirror.py` → `crawler/data/plm/sp_mirror.sqlite` |
| 수신 | beat 6시간, 주소·토큰이 없으면 건너뜀 | `tasks.run_plm_sync` |
| 질의 해석 | 과제코드·과제명·개발모델명·양산모델코드 → 그 과제의 제품 범위 | `shared/ontology.py` |

## 체크리스트

- [x] 말뭉치 모델코드 실측 — 기본 코드 584종·언급 3,432·미태깅 725. 가장 많은 코드는 출시 전 모델(S27·Fold8)
- [x] 모델코드 사전 — EXACT 447코드·300제품, FAMILY(출시 전 S27) 4. 동시 출현 대조 183 일치 · 31 표시(전부 태거 약점·목록 글로 설명) · 카탈로그 공백 코드는 뺌
- [x] 본문 태깅 — infer_all_product_codes 에 연결(이름 없으면 레거시보다 먼저, 이름 있으면 함께 언급)
- [x] 재태깅 — 모델코드 글 2,715 중 제품 채움 404 · 링크 추가 359, 표본 25/25 맞음(사진 EXIF 1건은 기기는 맞고 글 가치가 낮다)
- [x] 질의 해석(모델코드) — 불변성 + 제품이 함의한 브랜드 말('삼성 S25 울트라')도 남는 말에서 뺐다(1단계부터의 결함)
- [x] PLM 거울 — 전체 스냅샷·원자 교체·급감 보류·401/403·404 둘·재시도·토큰 비노출, 가짜 허브 회귀 9
- [x] beat `plm-sync-6h` → `tasks.run_plm_sync` — dev 는 not_configured
- [x] 질의 해석(과제) — 과제코드(옛 코드)·과제명(조사·차수 접미어·띄어쓰기)·개발모델명·양산코드 → 제품, 여럿이면 한 묶음, 못 이으면 남는 말 + 알림
- [x] MCP 도구 설명·README — get_ontology 에 model_codes · plm 상태
- [x] cae00 문서 — docs/DEPLOY_CAE00_2026-10-23.md
- [x] 테스트 — 새 시험 plm_mirror 9 · model_codes 23 · ontology 56(+8) · legacy_fallback 50(+1). crawler 회귀는 DB·망·백엔드를 쓰는 24파일을 빼고 돌렸다(기존 실패 셋만 — gsmarena·reddit_rss·topic_classifier). MCP 의 DB 시험 묶음은 메모리 부족(cpptools 45GB)으로 earlyoom 이 pytest 를 두 번 끊어 못 끝냈다 — 컨테이너 안 통합 점검('SM-S938B 발열' = 'S25 울트라 발열' 229건 같은 순위 · S27 묶음 · get_ontology)으로 대신했다
- [x] 재기동·커밋·push·메모리

## SP 키트 2026-10-21 맞추기 (2026-10-31)

### 계획

사용자 지시(포털 세션을 거쳐, 10-31) — SP 키트를 쓰는 플랫폼은 SP 최신판을 받고 맞출 것을 맞춘다. 지금 최신판은
SP main `778db2f`(0.4.67) · `KIT_VERSION = "2026-10-21"`(받는 쪽 규칙 16 · 시험장 8단계). SignalForge 의 거울 수신
(`crawler/base/plm_mirror.py`)은 10-14 판(`sp_core_pull.py@f7485c0`, 규칙 열하나)을 보고 만든 **전체 스냅샷 + 원자 교체**다 —
선(관계)을 안 받고, 공개 거둔 타입 하나가 전체를 멈추고, 합쳐진 과제의 `merged_into` 를 잃고, 잠금이 없다. 시험장
1단계(선)부터 못 넘는다.

- 층 1(받기) = 키트의 `sp_core_pull.py` **그대로**(키트 README — 이 스크립트는 고치지 말고 사본을 읽는 코드를 따로
  둔다, 파이썬 플랫폼은 받기 층으로 써도 된다). 키트 폴더를 고치지 않고 `crawler/sp_core_kit/` 에 둔다.
- 감싸는 쪽(`plm_mirror.sync`) — 잠금(규칙 14) · 실행 기록(정상 · 확인 필요 · 실패와 사유) · 주 1회 처음부터 받기 ·
  토큰은 자식 프로세스 환경으로만 · 수신 이력 CSV 는 남기지 않는다.
- 층 2(반영) = `PlmIndex` 가 사본(`crawler/data/plm/sp-core/sp_core.sqlite`)의 활성 행을 읽는다. 바깥 모양(by_code ·
  by_name · by_model · project_of …)은 그대로.
- 시험장 8단계를 crawler 시험으로 넣는다(가짜 SP 를 띄우고 단계마다 받기 → 사본 내보내기 → 채점기).

### 체크리스트

- [x] 키트 넣기 — `crawler/sp_core_kit/`(SP `778db2f`, 고치지 않음 — diff 없음 · 내부 주소 · 토큰 없음)
- [x] `plm_mirror.sync` — 키트 받기를 감싼다(잠금 · 주 1회 처음부터 · 결과 · 토큰 · CSV)
- [x] `PlmIndex` — 사본 읽기(활성 행 · 옛 key 표 · 합쳐진 것) · 시험용 `from_rows`
- [x] `scripts/plm-sync.sh`(`--full` · `--allow-shrink`, `--allow-drop` 은 `--full --allow-shrink`) · `tasks.run_plm_sync` · get_ontology 의 plm(마지막 결과 · 확인 필요 사유) — 가짜 SP 로 스크립트 끝까지 돌림(1단계 exit 0 · 7단계 exit 2)
- [x] 시험 — 시험장 8단계 모두 통과 · 감싸는 쪽 5 · 색인 4(키트 save_rows 로 쓴 사본) · plm_caution · ontology 는 from_rows
- [x] 전체 시험(crawler · MCP)
- [x] cae00 문서 — `docs/DEPLOY_CAE00_2026-10-31-plm-kit.md`(처음 받기가 연결 점검 · 건수 대조 · sf-mcp 재기동은 10-31 문서 4절과 한 번에 · 옛 거울 파일)
- [x] 커밋 · push · 포털에 보고(규칙 대조표 · 시험장 결과 · CLAUDE.md 규칙은 사용자 확인) — 92e2f7e · e5001e4

## SP 키트 2026-11-03 맞추기 (2026-11-04)

SP main `ab41cff`(0.4.72) · `KIT_VERSION = "2026-11-03"`. 받기(층 1, `sp_core_pull.py`)는 판 표시만 바뀌었고, 바뀐 것은 반영(층 2)
지시서 — 받은 선을 플랫폼에 넣는 법(§6.2) · 확인표의 선 표(§8.1) · 하지 말 것 넷(§9) · 물을 것(§2) · 관계의 유효 기간 ·
수량 · 계보 칸(§5.2). 포털이 signalforge-7f 에 맡겼으나 그 탭이 사용자에게 안 보여 c6 이 사용자 답을 받아 마쳤다.

- [x] 키트 폴더를 SP `ab41cff` 판으로(7f 가 넣어 둔 것 — diff 없음 확인) · 판 표시(plm_mirror 머리말 · CLAUDE.md '지금 판')
- [x] §2 물음 — 선을 쓸까 → **지금대로 안 쓴다**(사용자 결정 11-04). 그래서 §6.2 · §8.1 선 표 · §9 는 넣는 코드가 없어 해당 없음
- [x] 시험장 8단계(새 fake_sp — 선에 유효 기간 · 수량 · 계보 칸) · PLM 시험 · 전체 시험
- [x] cae00 — 받기 코드가 그대로라 사람 몫 없음(다음 plm-sync 가 새 키트 파일로 돈다). PLM 키트 문서의 '절반 넘게 안 옴'
  까닭 문구를 새 판에 맞춤

## 코어와 잇기 — Q-2 다시 엶 (2026-11-10)

### 계획

포털(hwaxportal-a5)이 cae00 운영의 11-09 제안서(포털 `docs/core-link/request.md` 1-1 · Q-2, 계약 `design.md` §9)를 넘겼다.
11-04 결정('코어와 잇지 않는다')을 사용자가 11-09 포털에서 뒤집었고, 11-10 이 세션에서 직접 다시 확인했다(키트 §2 물음과
함께). 할 일은 대응표 둘이다 — VOC 부품 16 → 코어 `part` key, 증상 32 → 코어 `failure_mode` key.

- 뼈대 — 정확 일치(키 · 이름 · 별칭이 코어 객체 하나로 정해지는 것)만 `rule` 선. 짐작은 후보로만. 다시 지을 때 `rule` 선만
  지우고 사람 선은 둔다(키트 01-AI-GUIDE §6.2 의 '표시가 있는 연결만 끊는다' 와 같은 꼴).
- 대응표 위치 — **공개 리포 밖**(사용자 11-10). 코어 key 는 허브 데이터라 `crawler/data/plm/core-link/`(PLM 거울 옆 — VOC DB
  밖이라 Drive 백업 · cae00 덤프에 안 실린다)에 둔다. 리포에는 읽는 코드와 규칙만. 초안 · 확인은 포털 받은 편지함으로 오간다.
- 바뀔 때 — **기본 제안대로**(사용자 11-10). 사라진 key 의 선은 '사용 안 함', 합쳐지면 `rule` 선만 이긴 쪽으로, 사람 선은
  '확인 필요' 로 남겨 알린다.
- 막힌 것 — 코어 `part` · `failure_mode` 의 key · 이름 · 별칭은 허브에만 있다(dev 에 토큰 없음). cae00 받은 편지함 Q6 으로
  청했고 사용자를 거쳐 온다. 그 전에는 받는 틀과 짓는 틀만 시험으로 만든다.
- 보류 — 가지 `core-link-q2`(worktree `SignalForge-corelink`)에서만 짓는다. 포털이 cae00 11-08 · 11-09 update-all 끝을 알리기
  전에는 main 에 올리지 않는다.

### 체크리스트

- [x] 키트 판 확인 — SP main `dbed567`(0.4.77), KIT_VERSION 그대로 2026-11-03. coreapi 변경은 '떼어 둔 종류 칸을 안 내보냄'
  하나라 받는 쪽 그대로
- [x] 사용자에게 직접 확인 — 다시 잇는다 · 대응표는 공개 리포 밖 · 바뀔 때 기본 제안대로
- [x] 층 1 — 받을 타입 기본값에 `part` · `failure_mode`(RECEIVE_TYPES), `_read_replica(path, types)` + 시험
- [x] 층 2 — `crawler/base/core_link.py`(rule · 후보 · 사람 선 지키기 · 바뀐 key 따라가기 · 사용 안 함 · 확인 필요) + 시험 8
- [x] 받기 뒤 저절로 다시 짓기(`after_sync` — beat run_plm_sync · scripts/plm-sync.sh), 결과 JSON 에 core_link 수
- [x] worker · plm-sync.sh 에 /shared 바인드(SignalForge 어휘는 shared/ontology 가 정본) · 운영 CLI `scripts/core_link.py`
- [x] CLAUDE.md '선(관계)' 옆에 '코어와 잇기' 줄(11-10 결정)
- [ ] 시험장 8단계 · PLM 시험 · 전체 시험
- [ ] 코어 어휘(Q6)가 오면 초안 — 리포에 싣지 않고 받은 편지함으로
- [ ] 포털 알림 뒤 main 에 병합 · cae00 문서
