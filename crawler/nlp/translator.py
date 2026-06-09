"""번역 모듈 — deep-translator(Google 무료) 기반.

무료 Google 한도(초당 5건)를 넘기면 'too many requests' 로 실패하므로
  - 전역 동시성 제한(Semaphore)
  - 최소 호출 간격(rate limit)
  - 레이트리밋/일시오류 시 지수 백오프 재시도
를 적용해 대량 수집/재처리에서도 번역 성공률을 확보한다.
"""
from deep_translator import GoogleTranslator
import asyncio
import logging
import random
import threading
import time

logger = logging.getLogger(__name__)

# 지원하지 않는 언어 (번역 건너뜀)
SKIP_LANGS = {"en", "und"}

# deep-translator 언어 코드 매핑 (langdetect → GoogleTranslator)
LANG_MAP = {
    "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
    "jw": "jv",     # 자바어
}

# Google 무료 한도(초당 5건) 안전 마진: 호출 간 최소 0.25s = 초당 4건
# ※ asyncio.Lock / Semaphore 는 첫 import 시점 event loop 에 바인딩되어
#    Celery worker 가 매 task 마다 새 loop 를 만들면 "bound to a different
#    event loop" 로 실패. → threading.Lock 으로 교체 (loop-agnostic).
_MIN_INTERVAL = 0.25
_MAX_RETRIES = 4

_throttle_lock = threading.Lock()
_last_call = 0.0


def _is_rate_limit(err: Exception) -> bool:
    m = str(err).lower()
    return "too many requests" in m or "server error" in m or "connection" in m


async def _throttle():
    """전역 최소 호출 간격 유지 (loop-agnostic).

    threading.Lock 으로 _last_call 갱신만 보호하고(await 미포함),
    그 후 asyncio.sleep 으로 양보. 어느 event loop 에서 호출되든 안전.
    """
    global _last_call
    with _throttle_lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _last_call)
        _last_call = now + (wait if wait > 0 else 0)
    if wait > 0:
        await asyncio.sleep(wait)


# @lat: translate_to_english — [[nlp#Translation]] 참조.
async def translate_to_english(text: str, source_lang: str = "auto") -> str:
    """텍스트를 영어로 번역. 실패 시 원문 반환(데이터 보존)."""
    if source_lang in SKIP_LANGS:
        return text

    # 텍스트 길이 제한 (Google Translator 무료 한도)
    text = text[:4999]
    src = LANG_MAP.get(source_lang, source_lang)
    loop = asyncio.get_event_loop()

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            await _throttle()
            result = await loop.run_in_executor(
                None,
                lambda: GoogleTranslator(source=src, target="en").translate(text),
            )
            return result or text
        except Exception as e:
            if attempt < _MAX_RETRIES and _is_rate_limit(e):
                # 지수 백오프 + 지터
                backoff = min(2 ** attempt + random.uniform(0, 1), 30)
                logger.debug(
                    f"번역 재시도 {attempt}/{_MAX_RETRIES} ({source_lang}) {backoff:.1f}s 후"
                )
                await asyncio.sleep(backoff)
                continue
            logger.warning(f"번역 실패 ({source_lang}, {attempt}회): {e}")
            return text
    return text
