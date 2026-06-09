# LLM External Tier 가이드 — OpenAI 호환 외부 서버

SignalForge LLM 라우팅은 **fast / high / external** 3 티어 + `auto` 자동 선택을 지원.

> **권장 (2026-06-06 Data Harvest 2 / Track E)**: Groq 무료 tier
> (`llama-3.3-70b-versatile`) 가 가장 빠르고 daily limit 도 일상 운영
> (보고서 1회 + alert 라우팅 수십회) 에는 충분. 키 입력 후 자동 검증
> 스크립트 `scripts/groq_health_check.py` 로 즉시 확인.

## 티어 표

| tier | 우선순위 | 소스 | 비용 | 환경변수 셋 |
|---|---|---|---|---|
| **external** | 1순위 (auto) | OpenAI 호환 외부 서버 (Groq/OpenRouter/Together/자체 호스트 등) | 사용자 책임 (free tier 종종 가능) | `EXTERNAL_API_KEY` + `EXTERNAL_BASE_URL` + `EXTERNAL_MODEL` (셋 다) |
| **high** | 2순위 | 1) Anthropic claude-sonnet → 2) OpenAI gpt-4o-mini → 3) 로컬 ollama 더 큰 모델 | claude 0.72/월, gpt 0.03/월, ollama 0 | `ANTHROPIC_API_KEY=sk-ant-...` 또는 `OPENAI_API_KEY=sk-...` 또는 `OPENAI_HIGH_MODEL_SHARED=qwen2.5:14b` |
| **fast** | 3순위 (폴백) | 로컬 ollama qwen2.5:7b | 0 | `OPENAI_API_KEY=ollama` + `OPENAI_BASE_URL=http://127.0.0.1:11434/v1` |

## 동작 원리

`get_provider(tier='auto')`:
1. external 키 셋(3개) 다 있으면 → 외부 서버
2. 위 없고 ANTHROPIC/OPENAI sk- 키 있으면 → 클라우드
3. 위 없으면 → 로컬 ollama (14b 또는 7b)

`get_provider(tier='external')`: 키 없으면 None 반환 (호출자가 high/fast 로 폴백 선택).

`get_provider(prefer='external')`: 강제 external (키 없으면 None).

## 외부 서버 가이드

### Groq (가장 빠름, 무료 daily limit)
```bash
EXTERNAL_API_KEY=gsk_...
EXTERNAL_BASE_URL=https://api.groq.com/openai/v1
EXTERNAL_MODEL=llama-3.3-70b-versatile
```
가입: https://console.groq.com/keys

### OpenRouter (다양한 모델, 일부 무료)
```bash
EXTERNAL_API_KEY=sk-or-v1-...
EXTERNAL_BASE_URL=https://openrouter.ai/api/v1
EXTERNAL_MODEL=meta-llama/llama-3.3-70b-instruct:free
```
가입: https://openrouter.ai/keys

### Together AI
```bash
EXTERNAL_API_KEY=...
EXTERNAL_BASE_URL=https://api.together.xyz/v1
EXTERNAL_MODEL=meta-llama/Llama-3.3-70B-Instruct-Turbo
```

### 자체 호스트 (vLLM/SGLang 등)
```bash
EXTERNAL_API_KEY=any-string  # 자체 서버 인증
EXTERNAL_BASE_URL=http://10.0.0.5:8000/v1
EXTERNAL_MODEL=meta-llama/Llama-3.3-70B-Instruct
```

## 적용 가이드

1. `.env` 에 3 슬롯 입력
2. backend 재시작: `scripts/down.sh && scripts/up.sh`
3. 확인: `curl http://127.0.0.1:8000/api/v1/_internal/llm-status | jq .external`
   - `configured: true`, `tier_label: "external:<model>"` 면 정상
4. 보고서 생성: `python -m insight.daily_insight YYYY-MM-DD`
   - footer 의 tier_label 에 external 표시
5. grounding history (`reports/insight_grounding_history.json`) 에서 추이 비교

## 자동 검증 (Track E)

키 입력 직후 graceful 검증:

```bash
cd crawler && /home/koopark/claude/SignalForge/.venv/bin/python \
    -m scripts.groq_health_check
```

- 키 미입력: `[skip]` 메시지 + exit 0 (graceful — 운영 흐름 안 막음)
- 키 입력 + 정상: `[ok] external reachable — tier_label=external:<model>`
- 키 입력 + 실패: `[fail]` + JSON 진단 + exit 1

스크립트 동작:
1. `.env` 의 `EXTERNAL_*` 3 슬롯 검사 (없으면 skip)
2. 직접 `<base_url>/chat/completions` POST 1회 (max_tokens=8) → reachable
3. backend `/api/v1/_internal/llm-status?ping=true` cross-check
4. `tier_label='external:<model>'` 정확 일치 + backend.external.reachable=true 확인

JSON 출력 (CI/CD 통합용):

```bash
python -m scripts.groq_health_check --json
```

## grounding 1주 추이 비교 (예정)

`reports/insight_grounding_history.json` 에 누적된 daily entry 의
`tier_label` 으로 ollama vs external 그룹 분리:

| 그룹 | tier_label 패턴 | 현 평균 grounding |
|---|---|---|
| ollama 14b | `high-shared:qwen2.5:14b` | 0.35~0.49 (실측 6일치) |
| external 70b (Groq) | `external:llama-3.3-70b-versatile` | 0.6+ 예상 — 키 입력 후 1주 측정 |

키 활성 후 7일 뒤 `python -m scripts.compare_insight` 로 자동 비교 가능.

## 비용 추정 (tier 별)

| 티어 | tier_label | 1회 비용 (3000 in + 1000 out) | 월 비용 (30회) |
|---|---|---|---|
| external (Groq llama 70b) | `external:llama-3.3-70b-versatile` | 0 (무료) | 0 |
| external (OpenRouter free) | `external:llama-3.3-70b-instruct:free` | 0 | 0 |
| external (Together 70b) | `external:Llama-3.3-70B-Instruct-Turbo` | ~$0.0009 | ~$0.027 |
| external (claude-sonnet 자체호스트) | depends | 외부 | 외부 |
| high (claude-sonnet) | `high-anthropic` | $0.024 | $0.72 |
| high (gpt-4o-mini) | `high-openai` | $0.001 | $0.03 |
| high (ollama 14b 로컬) | `high-shared:qwen2.5:14b` | 0 | 0 |
| fast (ollama 7b 로컬) | `fast-openai` | 0 | 0 |

## grounding 비교 예상

| 모델 | 예상 grounding | 실측 |
|---|---|---|
| qwen2.5:7b 로컬 | 0.30~0.35 | 0.3486 |
| qwen2.5:14b 로컬 | 0.45~0.55 | **0.4919** |
| llama-3.3-70b (Groq/OpenRouter) | 0.60~0.70 | 미측정 |
| claude-sonnet-4-5 | 0.75~0.85 | 미측정 |
| gpt-4o-mini | 0.55~0.65 | 미측정 |

external 70b 모델 (Groq free tier) 입력 시 grounding 0.6+ 예상 — 14b 대비 +0.15.

## 빠른 시작 (Groq 무료 권장)

1. https://console.groq.com 가입 (구글 로그인)
2. API Keys 메뉴 → "Create API Key"
3. `.env`:
   ```
   EXTERNAL_API_KEY=gsk_여기에붙여넣기
   EXTERNAL_BASE_URL=https://api.groq.com/openai/v1
   EXTERNAL_MODEL=llama-3.3-70b-versatile
   ```
4. `scripts/down.sh && scripts/up.sh`
5. 확인 + 첫 보고서 생성
