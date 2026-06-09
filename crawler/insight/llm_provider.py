"""
LLM Provider — Anthropic Claude 또는 OpenAI GPT 통합 추상화.

설계 원칙:
- 어느 한쪽 키만 있어도 동작.  ANTHROPIC_API_KEY 가 OPENAI_API_KEY 보다 우선.
- 호출 실패 시 None 반환 (호출자가 fallback 결정).  예외를 위로 전파하지 않음.
- 단순 동기 인터페이스: summarize(prompt) -> Optional[str].

환경변수:
    ANTHROPIC_API_KEY  (claude-sonnet-4-5)
    OPENAI_API_KEY     (gpt-4o-mini)
    LLM_TIMEOUT_SEC    (기본 60)
    LLM_MAX_TOKENS     (기본 4096)

사용 예시:
    from insight.llm_provider import get_provider
    prov = get_provider()
    if prov is None:
        print('LLM key 없음 — skip')
    else:
        print(prov.name, '→', prov.summarize('hello'))
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 기본값 (env 로 override 가능)
DEFAULT_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SEC", "60"))
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
DEFAULT_RETRY = int(os.getenv("LLM_RETRY", "1"))

# 프롬프트 버전 — 변경 시 캐시 자동 무효화.
# v4 (R12 Track E3, 2026-06-04): daily_insight 에서 *필수 인용 5종*
# (총 수집, 감성 평균, TOP 카테고리, TOP 제품, TOP 사이트) 를 강제하는 프롬프트
# 헤더가 안정화되었으므로 v3 → v4 로 승격. 캐시 자동 무효화 + grounding 기준선
# 별도 추적 가능 (insight_*.md footer prompt_version 으로 회귀 분리).
PROMPT_VERSION = "v4-must-cite-5"

# 시스템 프롬프트 v2 (legacy 호환용 — 운영 비교용으로만 유지).
SYSTEM_PROMPT_KO_V2 = (
    "당신은 삼성/Galaxy 제품군의 글로벌 VOC(Voice of Customer) 데이터를 "
    "분석하여 의사결정자에게 보고하는 시니어 애널리스트입니다. "
    "주어진 표/JSON 데이터에서 의미 있는 신호만 선별해 한국어로 보고서를 "
    "작성합니다. 각 문단은 구체적 수치(절대 수, 비율, 증감)와 인과적 "
    "해석을 동반해야 합니다.\n\n"
    "[grounding 규칙 — 절대]\n"
    "- 주어진 표(또는 JSON)의 수치를 단 하나라도 변경하면 무효이다.\n"
    "- 표에 없는 사실은 추측하지 않는다. 알 수 없는 경우 '데이터 없음' 으로 명시한다.\n"
    "- 모든 수치 주장은 표의 어떤 행/컬럼에서 왔는지 자연어로 가리킬 수 있어야 한다.\n"
    "- 합계·평균·비율을 새로 계산해 인용할 경우 표의 원본 수치만 사용한다.\n\n"
    "[언어 규칙 — 절대]\n"
    "- 출력은 100% 한국어로만 작성한다.\n"
    "- 중국어 한자(简体/繁體), 일본어 가나(ひらがな/カタカナ), 약어 외 영어 토큰을 절대 사용하지 않는다.\n"
    "- 제품명(Galaxy S25, Z Fold 8 등)과 사이트명(Reddit, DCInside 등)은 그대로 영문 표기 허용.\n"
    "- 그 외 모든 명사·동사·형용사는 한국어 단어로 표현한다.\n"
    "- '今天'/'分析'/'数据'/'用户' 같은 한자어 직접 출력은 절대 금지. "
    "한국어 '오늘/분석/데이터/사용자' 로 옮긴다.\n"
    "- 위반 시 보고서는 무효."
)

# v3 강화 — few-shot + 표 수치 그대로 인용 + bold 강제.
SYSTEM_PROMPT_KO_V3 = (
    "당신은 삼성/Galaxy 제품군의 글로벌 VOC(Voice of Customer) 데이터를 "
    "분석하여 의사결정자에게 보고하는 시니어 애널리스트입니다. "
    "주어진 표/JSON 데이터에서 의미 있는 신호만 선별해 한국어로 보고서를 "
    "작성합니다. 각 문단은 구체적 수치(절대 수, 비율, 증감)와 인과적 "
    "해석을 동반해야 합니다.\n\n"
    "[grounding 규칙 — 절대]\n"
    "- 주어진 표의 수치를 단 하나라도 변경하거나 반올림하면 보고서는 무효이다.\n"
    "- 표의 모든 수치는 콤마 포함 형식(예: 13,487) 그대로 인용한다.\n"
    "- 출력에 수치를 쓸 때는 반드시 **굵게(bold)** 표기한다. 예: **13,487건**, **9,259건**.\n"
    "- 표에 없는 사실은 추측하지 않는다. 알 수 없는 경우 '데이터 없음' 으로 명시한다.\n"
    "- 모든 수치 주장은 표의 어떤 행/컬럼에서 왔는지 자연어로 가리킬 수 있어야 한다.\n"
    "- 합계·평균·비율을 새로 계산해 인용할 경우 표의 원본 수치만 사용한다.\n"
    "- TOP 카테고리/제품/플랫폼 언급 시 표 행의 코드(price, GS26U, dcinside 등)와 한국어 이름을 모두 적는다.\n\n"
    "[출력 형식 — 절대]\n"
    "- 표/숫자/카테고리명은 입력 표에 적힌 그대로 인용한다.\n"
    "- 컬럼명을 거론할 때는 한국어 표기를 우선한다 (건수/비율(%)/변화율(%pp)).\n\n"
    "[언어 규칙 — 절대]\n"
    "- 출력은 100% 한국어로만 작성한다.\n"
    "- 중국어 한자(简体/繁體), 일본어 가나(ひらがな/カタカナ), 약어 외 영어 토큰을 절대 사용하지 않는다.\n"
    "- 제품명(Galaxy S25, Z Fold 8 등)과 사이트명(Reddit, DCInside 등)은 그대로 영문 표기 허용.\n"
    "- 그 외 모든 명사·동사·형용사는 한국어 단어로 표현한다.\n"
    "- '今天'/'分析'/'数据'/'用户' 같은 한자어 직접 출력은 절대 금지. "
    "한국어 '오늘/분석/데이터/사용자' 로 옮긴다.\n"
    "- 위반 시 보고서는 무효.\n\n"
    "[few-shot 예시 — 표 수치 그대로 인용 + bold]\n"
    "예시 A) 입력 표에 '총량 13,487, 부정 1,167, 가격 1,530건' 이 있을 때, 올바른 출력은:\n"
    "  '어제 수집 총량은 **13,487건**이며, 그 중 부정 의견은 **1,167건**(8.65%)이었습니다. "
    "부정 카테고리 1위는 가격/가성비(price)로 **1,530건**이었습니다.'\n"
    "예시 B) 입력 표에 '제품 GS26U 67건, 디스플레이 1,227건' 이 있을 때, 올바른 출력은:\n"
    "  '갤럭시 S26 울트라(GS26U) 부정 언급은 **67건**으로 제품 TOP 1 위치이며, "
    "디스플레이(display) 카테고리는 전체에서 **1,227건**으로 3 위입니다.'\n"
    "잘못된 예) '많은 사용자가 부정적인 의견을 보였습니다' — 수치가 없으므로 무효."
)

# 호환용 alias — 기존 모듈이 SYSTEM_PROMPT_KO 를 import 하므로 유지.
SYSTEM_PROMPT_KO = SYSTEM_PROMPT_KO_V3


def _has_hanzi(text: str) -> bool:
    """LLM 출력에 한자(중국·일본) 포함 여부 — 자동 재요청 트리거."""
    if not text:
        return False
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:  # CJK Unified Ideographs
            return True
    return False


class LLMProvider(ABC):
    """LLM 제공자 추상 인터페이스."""

    name: str = "base"

    @abstractmethod
    def summarize(self, prompt: str) -> Optional[str]:
        """프롬프트 → 한국어 인사이트 텍스트.  실패 시 None."""
        raise NotImplementedError

    def summarize_json(
        self,
        payload: Dict[str, Any],
        schema_desc: str,
        instructions: str,
        *,
        retry_on_hanzi: bool = True,
    ) -> Optional[str]:
        """JSON payload 를 markdown 표로 변환한 뒤 LLM 에 전달하는 grounded 요약.

        흐름:
          1. metrics_to_markdown(payload) → 표 문자열.
          2. 표 + schema_desc + instructions 를 user prompt 로 호출.
          3. 응답에 한자가 섞이면 (retry_on_hanzi=True) 1회 재요청 — 더 강한 경고를 덧붙임.

        payload 가 dict 가 아니면 그대로 summarize() 로 폴백.
        """
        # 지연 import — grounding 모듈은 동일 패키지.
        try:
            from .grounding import metrics_to_markdown  # type: ignore
        except Exception:
            try:
                from insight.grounding import metrics_to_markdown  # type: ignore
            except Exception as e:
                logger.warning("grounding import 실패 → 단순 summarize 폴백: %s", e)
                return self.summarize(
                    f"{schema_desc}\n\n{payload}\n\n{instructions}"
                )

        if not isinstance(payload, dict):
            return self.summarize(f"{schema_desc}\n\n{payload}\n\n{instructions}")

        table_md = metrics_to_markdown(payload, schema_desc=schema_desc)
        # v3: 표 위에 few-shot 예시 2개를 payload shape 에 맞춰 자동 생성.
        fewshot_block = ""
        try:
            from .grounding import build_fewshot_examples  # type: ignore
        except Exception:
            try:
                from insight.grounding import build_fewshot_examples  # type: ignore
            except Exception:
                build_fewshot_examples = None  # type: ignore
        if build_fewshot_examples is not None:
            try:
                fewshot_block = build_fewshot_examples(payload)
            except Exception as e:  # pragma: no cover
                logger.warning("build_fewshot_examples 실패: %s", e)
                fewshot_block = ""
        prompt_parts = [table_md]
        if fewshot_block:
            prompt_parts.append("\n---\n\n[few-shot 예시 — payload 기반 자동 생성]\n" + fewshot_block)
        prompt_parts.append(
            "\n---\n\n[작성 지시]\n" + instructions
            + "\n\n위 표의 수치를 인용할 때는 표에 적힌 그대로 옮기고 **bold** 로 강조하세요. "
            "표에 없는 수치는 추정하지 말고 '데이터 없음' 으로 표기하세요."
        )
        prompt = "".join(prompt_parts)
        out = self.summarize(prompt)
        if out and retry_on_hanzi and _has_hanzi(out):
            logger.warning("LLM 출력에 한자 감지 — 1회 재요청")
            stronger = (
                prompt
                + "\n\n[CRITICAL] 직전 출력에서 한자가 검출되었습니다. "
                "이번에는 한자(중국어/일본어 한자)를 절대 사용하지 말고 "
                "100% 한글만 사용해 다시 작성하세요."
            )
            out2 = self.summarize(stronger)
            if out2 and not _has_hanzi(out2):
                return out2
            # 재요청도 한자 → 그래도 더 나은 쪽 선택 (없으면 원본)
            return out2 or out
        return out


# ────────────────────────────────────────────────────────────────────────────
# Anthropic Claude
# ────────────────────────────────────────────────────────────────────────────
class AnthropicProvider(LLMProvider):
    """Anthropic Claude (claude-sonnet-4-5) 백엔드.

    SDK ≥0.30 (Messages API)을 사용.
    """

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5",
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        tier_label: Optional[str] = None,
    ):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("anthropic SDK 미설치") from e

        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        # tier_label: 'high-anthropic' 등 — _internal/llm-status 모니터링용.
        self.tier_label = tier_label
        # Anthropic 은 base_url 을 노출하지 않지만 모니터링 호환성 위해 None 으로 명시.
        self.base_url = None
        # 지연 import 로 모듈 import 시점 부담 최소화
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key, timeout=timeout)

    def summarize(self, prompt: str) -> Optional[str]:
        last_err: Optional[Exception] = None
        for attempt in range(DEFAULT_RETRY + 1):
            try:
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=SYSTEM_PROMPT_KO,
                    messages=[{"role": "user", "content": prompt}],
                )
                # content 는 [TextBlock, ...] — 텍스트만 합침
                parts = []
                for block in resp.content:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(text)
                return "".join(parts).strip() or None
            except Exception as e:  # SDK 종류 다양 → 광범위 처리
                last_err = e
                logger.warning(
                    "AnthropicProvider attempt %d failed: %s", attempt + 1, e
                )
        logger.error("AnthropicProvider 최종 실패: %s", last_err)
        return None


# ────────────────────────────────────────────────────────────────────────────
# OpenAI GPT
# ────────────────────────────────────────────────────────────────────────────
class OpenAIProvider(LLMProvider):
    """OpenAI Chat Completions 백엔드 (gpt-4o-mini 기본).

    P4.1: high tier 가 fast 와 동일 ollama 서버를 공유할 때 force_json/temperature/
    max_tokens 를 명시적으로 통제할 수 있도록 인자 추가.
    """

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: Optional[str] = None,
        temperature: Optional[float] = None,
        force_json: bool = False,
        tier_label: Optional[str] = None,
    ):
        try:
            import openai  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("openai SDK 미설치") from e

        self._api_key = api_key
        # OPENAI_MODEL: 오픈소스 LLM 서버 사용 시 모델명 지정 (예: meta-llama/Llama-3.3-70B-Instruct).
        # 미지정 시 OpenAI 공식 gpt-4o-mini.
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.timeout = timeout
        self.max_tokens = max_tokens
        # OPENAI_BASE_URL: OpenAI-호환 서버(vLLM/Ollama/LM Studio/사내 LLM)로 라우팅.
        # 미지정 시 OpenAI 공식 endpoint 그대로.
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or None
        self.temperature = temperature
        self.force_json = force_json
        self.tier_label = tier_label  # 'high' | 'fast' | None — 모니터링/디버깅용
        from openai import OpenAI

        client_kwargs = {"api_key": api_key, "timeout": timeout}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        self._client = OpenAI(**client_kwargs)

    def summarize(self, prompt: str) -> Optional[str]:
        last_err: Optional[Exception] = None
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_KO},
                {"role": "user", "content": prompt},
            ],
        }
        # getattr 폴백: 테스트에서 __new__() 로 만든 인스턴스 호환.
        temperature = getattr(self, "temperature", None)
        force_json = getattr(self, "force_json", False)
        if temperature is not None:
            kwargs["temperature"] = temperature
        # force_json 은 OpenAI 호환 서버가 지원할 때만 의미가 있다. Ollama 는
        # 정식 JSON 모드를 지원하지 않으므로 plain markdown 출력을 그대로 받는다.
        if force_json:
            # OpenAI 공식 API 호환: 일부 모델만 지원하므로 실패해도 무시.
            kwargs["response_format"] = {"type": "json_object"}  # pragma: no cover
        for attempt in range(DEFAULT_RETRY + 1):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                choice = resp.choices[0] if resp.choices else None
                if choice is None or choice.message is None:
                    return None
                return (choice.message.content or "").strip() or None
            except Exception as e:
                last_err = e
                logger.warning(
                    "OpenAIProvider attempt %d failed: %s", attempt + 1, e
                )
                # response_format 으로 실패 → 1회 더 시도 시 제거 후 재호출.
                if "response_format" in kwargs:
                    kwargs.pop("response_format", None)
        logger.error("OpenAIProvider 최종 실패: %s", last_err)
        return None


# ────────────────────────────────────────────────────────────────────────────
# Ollama (fast tier fallback)
# ────────────────────────────────────────────────────────────────────────────
class OllamaProvider(LLMProvider):
    """로컬 Ollama 서버 (qwen2.5:7b 기본) — fast tier 의 최종 폴백.

    OpenAI-호환 endpoint 를 사용하므로 openai SDK 위에 얹는다.
    환경변수:
        OLLAMA_BASE_URL  (기본 http://127.0.0.1:11434/v1)
        OLLAMA_MODEL     (기본 qwen2.5:7b)
    """

    name = "ollama"

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        tier_label: Optional[str] = None,
    ):
        try:
            import openai  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("openai SDK 미설치 (Ollama OpenAI-호환 호출에 필요)") from e

        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        self.base_url = base_url or os.getenv(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"
        )
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.tier_label = tier_label
        from openai import OpenAI

        self._client = OpenAI(api_key="ollama", base_url=self.base_url, timeout=timeout)

    def summarize(self, prompt: str) -> Optional[str]:
        last_err: Optional[Exception] = None
        for attempt in range(DEFAULT_RETRY + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT_KO},
                        {"role": "user", "content": prompt},
                    ],
                )
                choice = resp.choices[0] if resp.choices else None
                if choice is None or choice.message is None:
                    return None
                return (choice.message.content or "").strip() or None
            except Exception as e:
                last_err = e
                logger.warning("OllamaProvider attempt %d failed: %s", attempt + 1, e)
        logger.error("OllamaProvider 최종 실패: %s", last_err)
        return None


# ────────────────────────────────────────────────────────────────────────────
# Factory
# ────────────────────────────────────────────────────────────────────────────
def _tier() -> str:
    """현재 LLM 품질 tier 반환 (env LLM_QUALITY_TIER, 기본 'auto').

    유효값: 'fast' | 'high' | 'auto'.  타 값은 'auto' 로 정규화.
    """
    raw = (os.getenv("LLM_QUALITY_TIER") or "auto").strip().lower()
    return raw if raw in ("fast", "high", "auto") else "auto"


def _is_real_openai_key(key: str) -> bool:
    """fast tier 의 ollama dummy key("ollama") 와 진짜 OpenAI key 를 구별.

    high tier 는 'sk-' 로 시작하는 실 키만 신뢰한다. 단, Anthropic 의
    'sk-ant-' prefix 는 OpenAI 키가 아니므로 명시적으로 제외한다.
    """
    if not key:
        return False
    if key.startswith("sk-ant-"):
        return False
    return key.startswith("sk-")


def _is_real_anthropic_key(key: str) -> bool:
    """'sk-ant-' prefix 를 가진 진짜 Anthropic API 키만 인정."""
    return bool(key) and key.startswith("sk-ant-")


def get_provider(
    prefer: Optional[str] = None,
    tier: Optional[str] = None,
) -> Optional[LLMProvider]:
    """tier 별 LLM provider 반환. 가용 provider 없으면 None.

    tier 동작 (LLM_QUALITY_TIER env 가 default; 미지정시 'auto'):
        - 'external': EXTERNAL_API_KEY + EXTERNAL_BASE_URL + EXTERNAL_MODEL 셋 다
                      설정되면 외부 OpenAI 호환 서버로 라우팅 (Groq/OpenRouter/
                      Together/Fireworks/자체 호스트 등). 미설정 → None
                      (호출자가 high 또는 fast 로 폴백).
                      tier_label='external:<model>'.
        - 'high': ANTHROPIC_API_KEY → Anthropic.
                  없으면 sk- 로 시작하는 OPENAI_API_KEY → OpenAI(gpt-4o-mini).
                  둘 다 없으면 로컬 ollama OPENAI_HIGH_MODEL_SHARED (예: qwen2.5:14b)
                  로 폴백. 'high-shared:<model>'.
        - 'fast': 기존 호환 — Anthropic 키 있으면 사용, 다음 OPENAI(+ base_url 라우팅 OK,
                  ollama dummy 도 허용).  마지막 폴백으로 Ollama 직접.
        - 'auto': 'external' → 'high' → 'fast' 순으로 시도.

    prefer = 'openai'/'anthropic'/'ollama'/'external' → 해당 vendor 만 시도, 폴백 없음.
    """
    anth_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    oai_key = os.getenv("OPENAI_API_KEY", "").strip()
    ext_key = os.getenv("EXTERNAL_API_KEY", "").strip()
    ext_base = os.getenv("EXTERNAL_BASE_URL", "").strip()
    ext_model = os.getenv("EXTERNAL_MODEL", "").strip()

    # 1) prefer 명시 → 단일 vendor 시도.
    if prefer == "anthropic":
        if not anth_key:
            return None
        try:
            return AnthropicProvider(anth_key, tier_label="prefer-anthropic")
        except Exception as e:  # pragma: no cover
            logger.warning("AnthropicProvider 초기화 실패: %s", e)
            return None
    if prefer == "openai":
        if not oai_key:
            return None
        try:
            return OpenAIProvider(oai_key, tier_label="prefer-openai")
        except Exception as e:  # pragma: no cover
            logger.warning("OpenAIProvider 초기화 실패: %s", e)
            return None
    if prefer == "ollama":
        try:
            return OllamaProvider(tier_label="prefer-ollama")
        except Exception as e:  # pragma: no cover
            logger.warning("OllamaProvider 초기화 실패: %s", e)
            return None
    if prefer == "external":
        if not (ext_key and ext_base and ext_model):
            return None
        try:
            return OpenAIProvider(
                api_key=ext_key,
                model=ext_model,
                base_url=ext_base,
                max_tokens=int(os.getenv("LLM_HIGH_MAX_TOKENS", "8192")),
                temperature=float(os.getenv("LLM_HIGH_TEMPERATURE", "0.0")),
                tier_label=f"external:{ext_model}",
            )
        except Exception as e:  # pragma: no cover
            logger.warning("external OpenAIProvider 초기화 실패: %s", e)
            return None

    chain_tier = (tier or _tier()).lower()

    # 1.5) external tier — OpenAI 호환 외부 서버 (Groq/OpenRouter/Together/자체호스트 등).
    # EXTERNAL_API_KEY + EXTERNAL_BASE_URL + EXTERNAL_MODEL 셋 다 있어야 활성.
    if chain_tier == "external":
        if ext_key and ext_base and ext_model:
            try:
                return OpenAIProvider(
                    api_key=ext_key,
                    model=ext_model,
                    base_url=ext_base,
                    max_tokens=int(os.getenv("LLM_HIGH_MAX_TOKENS", "8192")),
                    temperature=float(os.getenv("LLM_HIGH_TEMPERATURE", "0.0")),
                    tier_label=f"external:{ext_model}",
                )
            except Exception as e:  # pragma: no cover
                logger.warning("external OpenAIProvider 초기화 실패: %s", e)
        return None  # 키 없거나 실패 → 호출자가 high/fast 로 폴백

    # 2) high tier — 실 클라우드 키 우선, 없으면 fast 와 공유.
    if chain_tier == "high":
        # 2a) ANTHROPIC_API_KEY=sk-ant-... → Anthropic Sonnet, label='high-anthropic'.
        if _is_real_anthropic_key(anth_key):
            try:
                return AnthropicProvider(anth_key, tier_label="high-anthropic")
            except Exception as e:  # pragma: no cover
                logger.warning("AnthropicProvider 초기화 실패: %s", e)
        # 2b) OPENAI_API_KEY=sk-... (sk-ant- 제외) → OpenAI gpt-4o-mini, label='high-openai'.
        #     base_url=None 으로 공식 endpoint 강제 (OPENAI_BASE_URL env 무시).
        if _is_real_openai_key(oai_key):
            try:
                return OpenAIProvider(
                    oai_key,
                    model=os.getenv("OPENAI_HIGH_MODEL", "gpt-4o-mini"),
                    base_url=None,
                    temperature=float(os.getenv("LLM_HIGH_TEMPERATURE", "0.0")),
                    force_json=False,
                    tier_label="high-openai",
                )
            except Exception as e:  # pragma: no cover
                logger.warning("OpenAIProvider 초기화 실패: %s", e)
        # 2c) P4.1 폴백: 클라우드 키 없을 때 fast 와 동일 ollama 서버 공유.
        # OPENAI_HIGH_MODEL_SHARED 가 있으면 high tier 만 *더 큰 모델* 로 라우팅 (같은 ollama 서버).
        # 예: fast=qwen2.5:7b / high=qwen2.5:14b — 같은 base_url, 다른 모델.
        shared_base = os.getenv("OPENAI_BASE_URL", "").strip() or "http://127.0.0.1:11434/v1"
        shared_model = (
            os.getenv("OPENAI_HIGH_MODEL_SHARED", "").strip()
            or os.getenv("OPENAI_MODEL", "").strip()
            or "qwen2.5:7b"
        )
        try:
            return OpenAIProvider(
                api_key=oai_key or "ollama",
                model=shared_model,
                base_url=shared_base,
                max_tokens=int(os.getenv("LLM_HIGH_MAX_TOKENS", "8192")),
                temperature=float(os.getenv("LLM_HIGH_TEMPERATURE", "0.0")),
                tier_label=f"high-shared:{shared_model}",
            )
        except Exception as e:  # pragma: no cover
            logger.warning("shared high tier OpenAIProvider 초기화 실패: %s", e)
        return None  # high 키 없음 → 호출자가 fast 로 폴백

    # 3) auto tier — external → high → fast 순으로 시도.
    if chain_tier == "auto":
        prov = get_provider(tier="external")
        if prov is not None:
            return prov
        prov = get_provider(tier="high")
        if prov is not None:
            return prov
        return get_provider(tier="fast")

    # 4) fast tier — 기존 호환 (ollama dummy 도 OPENAI_API_KEY 로 받아들임).
    if anth_key:
        try:
            return AnthropicProvider(anth_key, tier_label="fast-anthropic")
        except Exception as e:  # pragma: no cover
            logger.warning("AnthropicProvider 초기화 실패: %s", e)
    if oai_key:
        try:
            return OpenAIProvider(oai_key, tier_label="fast-openai")
        except Exception as e:  # pragma: no cover
            logger.warning("OpenAIProvider 초기화 실패: %s", e)
    # 어떤 OPENAI_API_KEY 도 없으면 Ollama 직접 폴백.
    try:
        return OllamaProvider(tier_label="fast-ollama")
    except Exception as e:  # pragma: no cover
        logger.warning("OllamaProvider 초기화 실패: %s", e)
    return None


__all__ = [
    "LLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "OllamaProvider",
    "get_provider",
    "SYSTEM_PROMPT_KO",
    "SYSTEM_PROMPT_KO_V2",
    "SYSTEM_PROMPT_KO_V3",
    "PROMPT_VERSION",
]
