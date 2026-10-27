# cae00 수정 요청 작업 노트 (2026-10-27)

## SG-01 — dev 에서는 왜 됐나

- dev 의 mcp.sif(07-07 빌드)에도 numpy 가 **없었다.** apptainer 가 `$HOME` 을 바인드해 컨테이너 파이썬이 호스트
  `~/.local/lib/python3.12/site-packages`(numpy 2.4.5 · mcp 1.27.1 · typing_extensions 4.15 …)를 이미지보다 먼저 잡았다.
  `PYTHONNOUSERSITE=1` 로 막으면 옛 이미지는 `ModuleNotFoundError: numpy`. cae00(09-28 빌드)은 사용자 site 에 numpy 가
  없어 그대로 드러났다. crawler 이미지도 같다(벡터 저장소·임베딩 적재가 numpy 를 못 찾는다). backend 는 괜찮다.
- 그래서 세 def 에 `PYTHONNOUSERSITE=1` 을 박았다 — dev 가 cae00 과 같은 조건으로 돈다. 시험도 사용자 site 없이 —
  pytest 만 따로 설치한 디렉터리를 PYTHONPATH 로 준다.
- **새로 지은 이미지에 mcp 2.x 가 깔렸다.** requirements 의 `mcp>=1.0.0` 이 2.x 로 풀려 `mcp.server.fastmcp` 가 없다 —
  서버가 아예 안 뜬다. 시험 165개는 통과했다(server.py 를 import 하는 시험이 없었다 — 기존 entrypoint 시험은 소스만
  읽는다). `mcp<2` 로 묶고, server.py 를 실제로 import 해 도구 수와 시맨틱 import 를 보는 시험을 더했다.
- 사용자 site 를 섞은 채 돌린 새 이미지 시험은 3개가 떨어졌다(호스트의 옛 mcp·typing_extensions 와 섞임). 섞지 않으면 165/165.

## SG-02 · SG-15 — id 가 어긋난 길

- cae00 은 `scripts/drive-sync/merge-from-drive.sh` 로 dev 덤프의 voc_records 를 **정수 id 그대로** 병합한다.
  주석의 가정 — "products/platforms 는 seed_master 로 양쪽 결정적 동일". 틀렸다. 카탈로그 id 는 설치 순서로 매겨진다 —
  dev 는 seed → 마이그레이션·추가(897제품 · 101플랫폼), 새로 깐 DB 는 마이그레이션(옛 제품) → seed. dev 의 id 3 은
  GS25U 인데 cae00 의 3 은 2012년 모델이다. FK 거름은 "그 id 가 있나" 만 봐서 엉뚱한 제품·플랫폼에 조용히 붙었다.
- 병합은 voc_records·voc_categories 뿐이라 voc_product_links·voc_defects 는 cae00 에 0행이다(SG-05 의 근거와 같다).

## SG-02 — 병합을 code 로 다시 짓기

- 번역 — 원천 카탈로그(stage)와 대상 카탈로그를 code 로 잇는 표(_pmap · _rmap)를 한 트랜잭션 안 임시 표로 만든다.
  voc_records 의 키는 (번역한 platform_id, external_id), 링크·결함의 voc_id 는 자연키로 번역한 대상 id(_vmap).
- 옛 정수 복사 행 고치기는 **멱등**이어야 한다(병합이 30분마다 돈다). 판정 — 대상 행 t 를 '원래 id 로 읽은 원천 행'
  과 맞춰 source_url 이나 content_hash 가 같고, '번역한 자리로 읽은 원천 행' 과는 같지 않을 때만 옮긴다. 고친 뒤에는
  번역한 자리로 읽어도 같은 글이라 다시 옮기지 않는다. 같은 external_id 가 두 플랫폼에 있어 자리를 맞바꾸는 경우
  (합성 시험 same1)는 external_id 를 잠시 비켜 놓았다가 옮긴다.
- dev 에 없는 대상 행은 세기만 한다 — 이 서버가 모은 글이거나 병합 뒤 dev 가 지운 글(dedup_voc 는 손으로 돌리는
  도구라 드물다). 옛 정수 자리에 있어도 원천 근거가 없어 옮기지 않는다.
- 실규모 첫 시도는 너무 느렸다 — 옮길 행을 비켜 놓기(UPDATE) · 되돌리기 · upsert 로 세 번 썼다. voc_records 는
  platform_id·product_id 가 색인 열이라 HOT 이 안 되고, 행마다 FTS GIN(to_tsvector) 까지 다시 쓴다. 비켜 놓기 한 문장이
  7분을 넘겨(WALWrite 대기) 끊었다. 고친 판 — 지금 빈 자리로 가는 행부터 차례로 옮기며 원천 값을 함께 넣어 한 번에
  끝내고, 고리만 비켜 놓는다. upsert 는 새것·바뀐 것만 넣는다(ON CONFLICT 는 WHERE 를 보기 전에 기존 행을 잠가
  행 머리에 쓴다 — 그대로 넘기면 30분마다 전 행 페이지가 더러워진다).
- 가드 — 원천 표본(기본 300)을 대상에서 code 로 찾아 제품·본문·링크·결함을 맞춘다. 하나라도 어긋나면 RAISE →
  트랜잭션 통째 되돌림. 합성 시험 T6 이 product_id 를 지우는 트리거로 확인했다.

## SG-04 — 왜 새 서버에 지금 파는 제품이 없었나

- dev 의 products id 42~48(GS26·GS26P·GS26U·GZF8·GZFL8·GB4·GB4P)과 플랫폼 93개는 마이그레이션도 seed 도 아닌
  길로 들어왔다. 빈 DB 에 alembic 만 돌리면 제품 849 · 플랫폼 8(실측). 0027 이 GS26 출시일을 채우는 UPDATE 도
  새 DB 에서는 0행이다.
- 별칭 칸은 카탈로그에 만들지 않았다(스키마 변경 — 물어야 할 일). 별칭의 정본은 태깅이 쓰는 제품 매처 사전이라
  resolve_product_keyword 가 그 사전으로 잇는다 — 이름표가 하나뿐인 카탈로그보다 넓다('폴드 와이드' 까지).

## SG-10 — comparison 이 1위인 까닭

- categorizer 가 모델 이름만 나와도 model_mention 을 붙이고 그때마다 comparison 에 +1 을 더한다. 시험
  test_galaxy_model_triggers_comparison 이 '모델만 언급해도 comparison' 을 지키는 의도된 규칙이라 바꾸지 않았다 —
  이슈를 줄 세우는 곳에서 빼는 것까지. 규칙을 바꿀지는 방침 결정이다.

## SG-11 · 18 — 가리지 않은 것

- 라벨 없는 'R+10자' 일련번호 꼴은 dev 본문 실측에서 시계 SKU(SM-R530NZAATUR)·펌웨어(R60XXS1BYH1)와 겹쳤다 —
  라벨(S/N·시리얼·일련번호·제조번호·IMEI·MEID)이 붙은 것만 가린다. 라벨 없는 15자리는 Luhn 이 맞을 때만(링크 해시·
  '100000000000000%' 를 피한다).
- 본문의 일반 사람 이름은 개체명 인식이 없어 못 찾는다 — @·u/ 꼴 계정과 작성자 칸만.

## 이미지 교체 — 워치독과 겹치지 않게

- 워치독은 락(/tmp/sf-watchdog.lock)을 다른 워치독이 쥐고 있으면 조용히 끝난다(보유자 cmdline 앞 40자에 'watchdog.sh').
  재기동 동안 `exec -a "watchdog.sh(maint-hold)" sleep` 로 락을 쥐어 두면 tick 이 끼어들지 않는다 — crontab 을
  건드리지 않고 저절로 풀린다(10-23 고아 고리의 재발 방지).

## 실규모 병합 시험(dev, 2026-10-27 22:44~23:19)

- 대상 — dev 덤프(22:00)를 복원한 사본의 platforms·products code 를 돌려(모든 id 가 다른 code) 링크·결함을 비웠다.
- 첫 실행 27분 03초 — 1,229,676행 제자리(이동 때 원천 값까지 넣어 upsert 는 0), 링크 +535,562 · 결함 +113,246(dev 와 같은
  수), 표본 300 일치, 뷰 7개 REFRESH. 같은 시각 30분 덤프와 backend 시험이 IO 를 나눠 썼다.
- 두 번째 6분 22초 — 변경 0(대부분 staging 복원). dev 무작위 5,000행을 code 로 대조 5,000/5,000 · 제품 어긋남 0.
- 처음 판(비켜 놓기 · 되돌리기 · upsert 세 번 쓰기)은 비켜 놓기 한 문장이 7분을 넘겨 끊었다(취소 → 트랜잭션 되돌림,
  '운영 DB 는 그대로' 문구까지 확인).

## 이미지 교체(dev, 23:22)

- 워치독 락을 쥐고(23:23 tick 은 'skip' 한 줄) 네 인스턴스를 하나씩 멈춘 뒤 up.sh — 'sf-mcp semantic import ok'.
  supervisor 1 · 레지스트리 있음 · 23:24 tick 조용. 이미지 안 numpy 2.5.4(/usr/local), PYTHONNOUSERSITE=1, mcp 1.28.1 ·
  anthropic 0.116.0 · openai 2.44.0(옛 이미지와 같음). backend 에는 numpy 가 없다 — 쓰는 곳이 없다(import 전수 확인).
- 옛 이미지는 apptainer/sif/*.sif.bak-20261027 — `*.sif` 꼴이 아니라 Drive 업로드(05:20 cron)에 실리지 않는다.
- 살아 있는 MCP(HTTP) — 도구 37, search_voc 객체 · strategy semantic · 작성자 가림, 낱말 넷 0건이면 시도한 경로 notes,
  '폴드8' → GZF8(alias), get_ontology error 없음, defect_profile ok, site_health 워커 alive, 차트 메타 태그 분리.
