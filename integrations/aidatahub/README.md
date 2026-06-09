# SignalForge → AX Hub (Mobile eXperience AI Data Hub) 통합

> SignalForge 가 수집·분류한 VOC 데이터를 AX Hub 로 동기화하여
> 다른 사업부 데이터(설계/CAE/시뮬레이션/품질)와 결합 분석 가능하게 한다.

작성일: 2026-05-28
대상 시스템: AX Hub v0.8 이상 (alembic 0026/0027 적용)
SignalForge 측 변경 사항: **0** (기존 API 그대로 사용)

---

## 1. 핵심 동선 — 2가지 모드

### A. 초기 backfill (1회) — **SignalForge 측이 push**
대량 데이터를 한 번에 AX Hub 로 적재.

```bash
python aidatahub_sync.py \
  --mode=push-all \
  --config=config.yml \
  --batch-size=100
```

내부 동작:
1. SignalForge DB (또는 API) 에서 VOC 전부 추출 — 제품 코드 6개(GS/GZ/GA/GW/GB/GR) 별 반복
2. 매핑 룰 적용 (config.yml 의 `sync.mapping_rules`)
3. AX Hub `POST /api/records/import?external_source=signalforge&auto_seq=true` 일괄 호출
4. 실패 row 는 `dead_letter.{timestamp}.json` 으로 별도 저장

### B. 정기 update — **AX Hub 측이 pull** (또는 SF 가 push)
초기 backfill 후 변경분만 동기화.

**옵션 B-1: AX Hub 가 pull (운영 기본)**
- AX Hub 운영자가 `sync_sources` 에 SignalForge 1회 등록 (아래 §3 참조)
- AX Hub 의 외부 cron 이 매 30분 `POST /api/sync/sources/{id}/run` 호출
- SignalForge 측 추가 작업 0

**옵션 B-2: SignalForge 가 push (Celery beat 통합)**
- 본 폴더의 `celery_task.py` 를 SignalForge 의 `app/services/celery_app.py` 등에서 import 등록
- 5~30분 주기로 since 기반 증분 push
- 옵션: 신규 VOC 발생 즉시 trigger 가능 (real-time)

> **권장**: B-1 (AX Hub pull). SF 변경 없음 + AX Hub 자체 throttle/dead-letter/재시도 자동.

---

## 2. SignalForge 측 준비 (한 번만)

### 2-1. API 키 발급
AX Hub 가 SignalForge VOC list 호출 시 사용할 API 키. SignalForge 의 인증 정책에 따라:
- 기존 X-API-Key 헤더 인증 그대로
- 또는 별도 read-only 토큰 발급 권장

### 2-2. AX Hub 측 sync_source 1회 등록 (SF 운영자가 AX Hub 측에 요청)
```bash
curl -X POST http://aidatahub:8001/api/sync/sources \
  -H "X-API-Key: $AIDH_API_KEY" \
  -H "Content-Type: application/json" \
  --data @sync_source_for_aidatahub.json
```

> `sync_source_for_aidatahub.json` 은 AX Hub repo 의 `examples/MX/voc-signalforge/sync_source.example.json` 에 표준 매핑 룰로 준비되어 있다. AX Hub 운영자에게 전달.

---

## 3. 본 폴더 파일 구성

| 파일 | 용도 |
|---|---|
| `README.md` | 본 문서 |
| `AIDATAHUB_CLIENT_SPEC.md` | **AX Hub 의 record 스키마·import 사양** — LLM/사람 모두 읽음. SignalForge 측 LLM 이 통합 코드를 자동 생성할 때 시스템 프롬프트로 직접 사용 |
| `aidatahub_sync.py` | push-all / push-recent 두 모드 동작 — 표준 라이브러리 + httpx + pyyaml 만 |
| `requirements.txt` | 최소 의존성 |
| `config.example.yml` | URL/키/매핑 룰 — 환경 변수 치환 가능 |
| `celery_task.py` | 옵션 — Celery beat 등록용 task (push 모드) |

---

## 4. 매핑 규약 핵심 (SignalForge VOC → AX Hub Record)

| SignalForge 필드 | AX Hub Record |
|---|---|
| `id` (internal int) | `_external_id` (external_id_map 자동 매핑) |
| `external_id` (crawler 원본 ID) | tags 에 `voc:source-id:{value}` |
| `content_original` | `content.sections[0].content_text` + `title` (80자 truncate) |
| `content_translated` (있으면) | `content.sections[1].content_text` |
| `categories[]` | tags (prefix `voc:`) |
| `country_code` | tags (prefix `country:`) + subject_keywords |
| `platform.code` (Naver/유튜브/X 등) | tags (prefix `channel:`) |
| `sentiment_label` | tags (prefix `sentiment:`) + severity 환산 |
| `sentiment_score` (수치) | summary 에 인용 |
| `product.code` | tags + subject_keywords + summary |
| `published_at` | `valid_from` (날짜만) + `year` |
| `likes/comments/shares_count`, `engagement_score` | record metadata (검색에서 정렬 보조) |

자동 부여:
- `agents = ["market-voc-analyst"]`
- `doc_type = "voc_report"`
- `team = "MX"`, `group = "VOC"`
- `data_type = "DOC"`, `language = "ko"`
- `classification = "internal"` (PII 마스킹 확인 후 — 미확인 시 `confidential` 강제)

### Sentiment → severity → quality_score 변환
```
very_negative → critical → 100
negative      → major    → 75
neutral       → info     → 25
positive      → info     → 25
```

→ AX Hub 검색에서 `quality_score>=75` 필터로 심각 VOC 만 뽑기 가능.

---

## 5. 운영 후 확인

### 적재 확인 (AX Hub)
```bash
curl "http://aidatahub:8001/api/search?q=Galaxy+S25+display&agent_type=market-voc-analyst"
curl "http://aidatahub:8001/api/records?team=MX&group=VOC&limit=10"
```

### sync_run 이력 (AX Hub)
```bash
curl "http://aidatahub:8001/api/sync/sources" | jq '.[] | select(.name=="signalforge")'
curl "http://aidatahub:8001/api/sync/sources/{id}/runs?limit=10"
```

### dead_letter 분석 (SignalForge 측 또는 AX Hub `sync_runs.dead_letter`)
적재 실패 row 의 원본 + 에러 사유 → 매핑 규칙 보강.

---

## 6. PII / 보안 정책

- SignalForge 는 SNS/카페/리뷰 등 외부 채널을 크롤링하므로 **본문에 사용자 닉네임/계정명/이메일 등 잔존 가능성**.
- SignalForge 측에서 `pii_masked=true` 보증 가능한 record 만 본 어댑터가 push.
- `pii_masked=false` 또는 unknown 은 `dead_letter` 로 분류 + 검토 후 사후 재처리.
- AX Hub 측은 `trust_pii_masked=false` (기본) 면 받은 record 의 `classification` 을 `confidential` 로 강제.

---

## 7. 트러블슈팅

| 증상 | 원인 | 대처 |
|---|---|---|
| AX Hub 가 401 | API 키 만료/잘못됨 | AX Hub `api_keys` 재발급 |
| 매핑 실패 다수 (dead_letter 다수) | mapping_rules vs 실제 응답 필드 불일치 | `AIDATAHUB_CLIENT_SPEC.md` §3 비교 후 룰 수정 |
| 동일 voc_id 가 record 2건 생성 | external_id_map 누락 | `external_source=signalforge` 파라미터 확인 |
| sync 가 점점 느려짐 | SignalForge rate limit | `config.yml` 의 `max_rps` 낮춤 |
| Galaxy S25 새 모델 검색 안 됨 | sentiment 분석 미완료 | `processed_at IS NOT NULL` 필터 적용 |

---

## 8. 관련 문서

- [AIDATAHUB_CLIENT_SPEC.md](./AIDATAHUB_CLIENT_SPEC.md) — AX Hub 측 명세
- AX Hub 사이트: `http://aidatahub:8001/api/docs/llm.txt` — LLM 친화 API 가이드
- AX Hub Ingest Kit: `GET /api/schema/ingest-kit.zip?agent_type=market-voc-analyst`
