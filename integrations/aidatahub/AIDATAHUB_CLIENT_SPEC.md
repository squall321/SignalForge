# AX Hub Client Spec — for SignalForge Integration

> AX Hub (Mobile eXperience AI Data Hub) 가 SignalForge VOC 데이터를 받는 방식의
> 명세서. SignalForge 측 LLM/엔지니어가 이 문서를 시스템 프롬프트로 또는 참조
> 자료로 사용하여 통합 어댑터를 자동/수동 작성할 수 있다.

대상 AX Hub 버전: v0.8+
인증: `X-API-Key: <token>` (HTTP 헤더)
콘텐츠 타입: `application/json`

---

## 1. AX Hub Record 스키마 핵심

```json
{
  "id": "DOC-MX-VOC-2026-0000000001",         // 자동 채번 가능 — auto_seq=true
  "data_type": "DOC",                          // 7-enum 중
  "team": "MX",
  "group": "VOC",
  "year": 2026,
  "title": "갤럭시 S25 통화 중 터치 오작동",   // ~~250자 권장
  "summary": "",                               // 1~3줄, 검색 boost
  "content": {                                 // JSONB, 자유 구조
    "sections": [
      {"section_id":"1","level":1,"title":"본문","content_text":"..."}
    ]
  },
  "doc_type": "voc_report",                    // taxonomy code (필수 권장)
  "tags": ["voc","Galaxy-S25","sentiment:negative"],
  "agents": ["market-voc-analyst"],            // 이 record 를 사용할 agent
  "classification": "internal",                // public|internal|confidential|secret
  "language": "ko",
  "author": "signalforge",
  "department": "MX/VOC",
  "valid_from": "2026-03-15",                  // 발생일
  "subject_keywords": ["S25","display","touch"],
  "quality_score": 75                          // 0~100 (severity 환산 결과)
}
```

**필수 필드**: `title`, `content`  
**id 자동 부여 시 필요**: `data_type`, `team`, `group`, `year` (auto_seq=true 설정)

---

## 2. POST /api/records/import 사양

엔드포인트: `POST {AIDH_BASE_URL}/api/records/import`

### 쿼리 파라미터
| 이름 | 의미 | 기본 |
|---|---|---|
| `auto_seq=true` | id 없으면 (data_type,team,group,year)별 seq 자동 부여 | false |
| `dry_run=true` | 검증만, 저장 안 함 | false |
| `external_source=signalforge` | **반드시 지정** — UPSERT 매핑 활성 | null |

### Body — 3가지 모두 허용
```json
// 1) 단건
{"title":"...","content":{...},"_external_id":"123"}

// 2) 배열
[ {...}, {...}, {...} ]

// 3) wrapped
{"auto_seq":true,"external_source":"signalforge","records":[...]}
```

### 각 record 의 `_external_id` (UPSERT 키)
**이 키가 있으면** AX Hub 의 `external_id_map(source='signalforge', external_id=...)` 조회 →
- 매핑 있음: 그 record 를 UPDATE (audit_log 자동 기록)
- 매핑 없음: 새 record INSERT + 매핑 등록

**없으면**: 매번 새 record. **반드시 SignalForge 의 voc_records.id 를 `_external_id` 로 전달.**

### 응답
```json
{
  "count": 100,
  "ok": 98,
  "failed": 2,
  "warnings": 5,
  "auto_seq": true,
  "dry_run": false,
  "external_source": "signalforge",
  "results": [
    {"id":"DOC-MX-VOC-2026-0000000001","action":"inserted","external_id":"123","warnings":[]},
    {"id":"DOC-MX-VOC-2026-0000000002","action":"updated","external_id":"124","warnings":[]},
    {"error":"missing title","input_title":null},
    ...
  ]
}
```

`action` 값: `"inserted"` / `"updated"` / `"skipped"` / `"dry_run"`

### 한도
- 1회 호출당 최대 **1000 records**
- 더 많으면 분할 호출. AX Hub 가 throttle 안 함 — 호출자 책임.

### 멱등성
- 같은 `_external_id` 재호출은 안전 (UPSERT).
- 같은 호출의 재시도(timeout 등)도 안전 (idempotent — content_hash 변화 없으면 `action="skipped"`).

---

## 3. SignalForge VOC → AX Hub 매핑 규약

### Source: VocRecord (SignalForge 의 `voc_records` 테이블)
주요 필드 (SignalForge `app/models/voc_record.py` 참조):

```python
class VocRecord:
    id: int                          # → _external_id
    external_id: str                 # crawler 원본 ID (Naver/X 등)
    source_url: str
    content_original: str            # 원본 텍스트
    content_translated: str | None
    language_detected: str
    country_code: str
    sentiment_score: float           # -1.0 ~ 1.0
    sentiment_label: str             # very_negative/negative/neutral/positive
    categories: list[str]            # ARRAY(TEXT) — e.g. ["display","touch"]
    likes_count: int
    comments_count: int
    shares_count: int
    engagement_score: float
    published_at: datetime
    processed_at: datetime | None    # NLP 완료 시점 — null 이면 skip
    product: Product                 # FK — product.code / name_en / series_code
    platform: Platform               # FK — platform.code / name / region
```

### Target: AX Hub Record

```python
{
  "_external_id": str(voc.id),                    # required
  "data_type": "DOC",
  "team": "MX",
  "group": "VOC",
  "year": voc.published_at.year,
  "title": voc.content_original[:80] + ("..." if len(voc.content_original) > 80 else ""),
  "summary": (
    f"{voc.product.code} VOC — {voc.sentiment_label} "
    f"({voc.platform.code}, {voc.country_code}, score={voc.sentiment_score:.2f})"
  ),
  "doc_type": "voc_report",
  "tags": [
    *[f"voc:{c}" for c in (voc.categories or [])],
    f"country:{voc.country_code}",
    f"channel:{voc.platform.code}",
    f"sentiment:{voc.sentiment_label}",
    voc.product.code,
    voc.product.series_code,
  ],
  "agents": ["market-voc-analyst"],
  "classification": "internal",  # PII 마스킹 보증 안 되면 confidential
  "language": "ko",
  "author": "signalforge",
  "department": "MX/VOC",
  "valid_from": voc.published_at.date().isoformat(),
  "subject_keywords": [voc.product.code, voc.product.name_en, voc.product.name_ko],
  "quality_score": SEVERITY_QS[SENTIMENT_TO_SEVERITY[voc.sentiment_label]],
  "content": {
    "sections": [
      {
        "section_id": "1",
        "level": 1,
        "title": "본문 (원어)",
        "content_text": voc.content_original
      },
      *([
        {
          "section_id": "2",
          "level": 1,
          "title": "본문 (한글 번역)",
          "content_text": voc.content_translated
        }
      ] if voc.content_translated else []),
      {
        "section_id": "3",
        "level": 1,
        "title": "메타",
        "content_text": (
          f"source_url: {voc.source_url}\n"
          f"engagement: likes={voc.likes_count}, comments={voc.comments_count}, "
          f"shares={voc.shares_count}, score={voc.engagement_score:.2f}\n"
          f"sentiment_score: {voc.sentiment_score:.3f}\n"
          f"processed_at: {voc.processed_at}"
        )
      }
    ]
  }
}
```

### 변환 상수

```python
SENTIMENT_TO_SEVERITY = {
    "very_negative": "critical",
    "negative":      "major",
    "neutral":       "info",
    "positive":      "info",
}
SEVERITY_QS = {"critical": 100, "major": 75, "minor": 50, "info": 25}
```

### 필터
다음 조건이면 skip + dead_letter 기록:
- `voc.processed_at is None` (NLP 미완료)
- `voc.pii_masked` 가 False (있을 경우) — 또는 AX Hub 가 `classification=confidential` 강제

---

## 4. 인증 & 보안

### 인증
- 헤더: `X-API-Key: <token>`
- 토큰 발급: AX Hub `/api/auth/keys` (admin)
- 만료: 90일 권장 회전

### 키 보관
- 환경변수 `$AIDH_API_KEY` (config.yml 의 `${AIDH_API_KEY}` 치환)
- Celery worker 의 비밀: SignalForge `app/core/config.py` 의 SecretStr 사용

### TLS
- 운영 환경에서는 `https://aidatahub.internal` 권장
- 개발 환경 `http://aidatahub:8001` 허용

---

## 5. 에러 응답 표준

```json
{
  "detail": "team/group not registered: MX/VOC",
  "type": "OrgValidation"
}
```

| HTTP | 의미 | 권장 동작 |
|---|---|---|
| 200 | 부분 성공 가능 — `results[i].error` 확인 | dead_letter 에 실패 row 저장 |
| 400 | body 형식 오류 | 매핑 로직 수정 후 재호출 |
| 401 | API 키 잘못/만료 | 키 회전 |
| 403 | 권한 없음 | AX Hub 운영자 확인 |
| 413 | records 1000개 초과 | 분할 호출 |
| 429 | rate limit (api_keys.rate_limit_per_min) | backoff + 재시도 |
| 500 | AX Hub 내부 오류 | sync_run.dead_letter 에 저장, 재시도 backoff |

---

## 6. Push vs Pull — 어느 방식?

| 모드 | SignalForge 부담 | AX Hub 부담 | 권장 |
|---|---|---|---|
| **A. SF push (initial)** | Celery worker 가 호출 | 받기만 | **초기 backfill 시 권장** |
| **B. AIDH pull** | 0 (기존 API) | sync_source 등록 + cron | **정기 운영 권장** |
| **C. SF push (real-time)** | webhook on insert | 받기만 | 옵션 — 즉시성 필요 시 |

본 어댑터는 **A + B 동시 지원**. 운영자 선택.

---

## 7. 호출 예시 (SignalForge 측 코드)

```python
import httpx

async def push_voc_batch(voc_records: list, *, aidh_url: str, aidh_key: str):
    body = {
        "auto_seq": True,
        "external_source": "signalforge",
        "records": [voc_to_record(v) for v in voc_records],
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{aidh_url}/api/records/import",
            params={"auto_seq": "true", "external_source": "signalforge"},
            headers={"X-API-Key": aidh_key, "Content-Type": "application/json"},
            json=body,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()
```

전체 동작은 `aidatahub_sync.py` 참조.

---

## 8. AX Hub 측 사전 등록 (1회만)

AX Hub 운영자가 다음을 등록해야 함 (SignalForge 측은 unnecessary):

1. `org_group` (MX/VOC)
2. `doc_type` (voc_report — mode=llm_context)
3. `agent` (market-voc-analyst)
4. `sync_source` (signalforge — mapping_rules 포함, Pull 모드용)
5. `api_key` (SignalForge 가 쓸 X-API-Key 토큰 발급)

→ AX Hub repo 의 `examples/MX/voc-signalforge/setup.sh` 가 4번까지 자동.

---

## 9. 변경 이력

| 버전 | 날짜 | 변경 |
|---|---|---|
| 1.0 | 2026-05-28 | 초안 |
