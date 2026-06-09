# LLM 라우팅 가이드 (P3.6 → P4.2)

`crawler/insight/llm_provider.get_provider(tier=...)` 는 사용 가능한 키와
`LLM_QUALITY_TIER` 환경변수를 보고 적합한 백엔드를 선택한다. 모든 provider
인스턴스는 `tier_label` attribute 로 자기 정체성을 노출한다 — 모니터링,
보고서 footer, `_internal/llm-status` 에서 공통 사용.

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_QUALITY_TIER` | `auto` | `auto`/`fast`/`high` 중 하나 |
| `ANTHROPIC_API_KEY` | - | **`sk-ant-` prefix** 인 진짜 키만 high-anthropic 으로 인정 |
| `OPENAI_API_KEY` | - | **`sk-` prefix** 인 진짜 키 (단 `sk-ant-` 는 제외) — high-openai 로 인정 |
| `OPENAI_HIGH_MODEL` | `gpt-4o-mini` | high-openai 사용 시 모델 override |
| `OPENAI_MODEL` | `gpt-4o-mini` | 공유 OpenAI 호환 endpoint 사용 시 모델 |
| `OPENAI_BASE_URL` | - | vLLM/사내 LLM/Ollama 등 OpenAI-호환 endpoint |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434/v1` | 로컬 Ollama OpenAI-호환 endpoint |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Ollama 모델명 |
| `LLM_HIGH_MAX_TOKENS` | `8192` | shared/openai-high 시 출력 토큰 한도 |
| `LLM_HIGH_TEMPERATURE` | `0.0` | high tier 결정성 강화 |
| `COMPARE_TIERS` | - | `true` 면 보고서 1편당 fast 도 호출 → 동일 .md 에 2 섹션 |

## tier_label 매트릭스

`get_provider(tier='high')` 가 init 시점에 결정하는 라벨:

| ANTHROPIC | OPENAI(`sk-`) | tier_label | provider | base_url |
|---|---|---|---|---|
| 있음 | 무관 | `high-anthropic` | AnthropicProvider | (해당없음) |
| 없음 | 있음 | `high-openai` | OpenAIProvider | **None — 공식 endpoint 강제** |
| 없음 | 없음 | `high-shared` | OpenAIProvider | `OPENAI_BASE_URL` 또는 `http://127.0.0.1:11434/v1` |

fast tier 라벨 (참고용):

| ANTHROPIC | OPENAI | tier_label | provider |
|---|---|---|---|
| 있음 | 무관 | `fast-anthropic` | AnthropicProvider |
| 없음 | 있음 (`ollama` dummy 포함) | `fast-openai` | OpenAIProvider |
| 없음 | 없음 | `fast-ollama` | OllamaProvider |

`auto` = `high` 시도 → None 이면 `fast` 폴백.

## 즉시 분기 보장

`.env` 에 `ANTHROPIC_API_KEY=sk-ant-...` 를 입력하고 backend 를 재시작하면
즉시 high tier 가 `high-anthropic` 으로 분리된다 — 별도 빌드/배포 없음.
키 없는 환경에서는 현행 `high-shared` 동작 (fast 와 동일 ollama 서버 공유)
이 그대로 유지된다.

## 모델별 grounding 점수 예상치

P3.5 평가 (114k VOC 샘플, JSON payload → markdown 표 grounding 프롬프트).

| 모델 | tier_label | grounding 점수 (0-1) | 비고 |
|---|---|---|---|
| qwen2.5:7b (Ollama 공유) | `high-shared` / `fast-openai` | 0.22 ~ 0.35 | 표 인용 변형 빈번 |
| gpt-4o-mini (공식) | `high-openai` | 0.45 ~ 0.55 | 표 그대로 인용 비교적 안정 |
| claude-sonnet-4-5 | `high-anthropic` | 0.60 이상 (예상) | 한국어/grounding 양쪽 최강 |

`tier='high'` 에서 grounding 점수가 0.5 이상이면 성공으로 간주한다.
실측: 최근 7일 평균 `0.3456` (qwen2.5:7b 공유, 2026-06-02 기준 — `reports/insight_grounding_history.json`).

## 비용 비교 (월 30회 인사이트 1편 기준)

인사이트 1편 ≈ input 3k token / output 1k token. 단위 USD/월.

| tier_label | input/output ($/1M tok) | 1편 비용 | 월(30회) 비용 |
|---|---|---|---|
| `high-shared` (ollama 로컬) | 0 / 0 | 0 | **0.00** |
| `high-openai` (gpt-4o-mini) | 0.15 / 0.60 | 0.00105 | **0.03** |
| `high-anthropic` (sonnet 4.5) | 3.00 / 15.00 | 0.024 | **0.72** |
| `fast-openai` (ollama 공유) | 0 / 0 | 0 | **0.00** |

`_internal/llm-status` 의 `fast.cost_estimate` / `high.cost_estimate` 필드와 정확히 동일한 수치를 사용한다.

## API key 입력 가이드 한 단락

`/.env` 에 한 줄 추가하고 backend 만 재시작하면 즉시 high tier 가 분리된다.
Anthropic Claude Sonnet 4.5 를 쓰려면 `ANTHROPIC_API_KEY=sk-ant-api03-...`,
OpenAI gpt-4o-mini 를 쓰려면 `OPENAI_API_KEY=sk-proj-...` 를 추가한다 (Anthropic
의 `sk-ant-` prefix 는 OpenAI 호출에 사용되지 않도록 자동 배제된다). 키 없는
환경에서도 dry-run/fallback 으로 `high-shared` 가 동작하므로 dev/staging 에서
키 미입력 상태로도 보고서 생성이 가능하다. 분기 확인은
`curl -sS http://127.0.0.1:8001/api/v1/_internal/llm-status | jq '.high.tier_label, .high.cloud_ready'`.

## 호출 예시

```python
from insight.llm_provider import get_provider

# env 의 LLM_QUALITY_TIER 자동 적용 (auto)
prov = get_provider()
print(prov.tier_label)  # 'high-anthropic' / 'high-openai' / 'high-shared' / 'fast-*'

# tier 명시 (env override)
prov = get_provider(tier="high")

# vendor 강제 (폴백 없음)
prov = get_provider(prefer="anthropic")
```

`daily_insight.run()` 은 내부의 `_select_provider_with_tier()` 가 high 시도
→ 실패 시 fast 폴백을 수행한다. `COMPARE_TIERS=true` 면 used_tier=high 일 때
fast 도 동시 호출해 보고서 .md 에 `## 비교: fast tier 출력 (<label>)` 섹션을
추가한다.

## 검증 명령

```bash
# 단위 테스트 (P4.2 신규)
cd crawler
python tests/test_llm_tier_selection.py
# 결과: 5/5 통과

# 기존 회귀 테스트
python tests/test_llm_routing.py     # 3/3
python tests/test_llm_routing_v2.py  # 4/4

# 키 없는 환경 → high.tier_label == 'high-shared'
curl -sS http://127.0.0.1:8001/api/v1/_internal/llm-status | jq '.high.tier_label, .high.cloud_ready, .high.cost_estimate'

# .env 에 ANTHROPIC_API_KEY=sk-ant-test 추가 + backend 재시작
curl -sS http://127.0.0.1:8001/api/v1/_internal/llm-status | jq '.high.tier_label'
# 출력: "high-anthropic"
```
