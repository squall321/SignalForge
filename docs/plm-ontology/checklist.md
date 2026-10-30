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
