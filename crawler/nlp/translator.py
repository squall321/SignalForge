"""번역 모듈 — deep-translator(Google 무료) 기반.

무료 Google 한도(초당 5건)를 넘기면 'too many requests' 로 실패하므로
  - 전역 동시성 제한(Semaphore)
  - 최소 호출 간격(rate limit)
  - 레이트리밋/일시오류 시 지수 백오프 재시도
를 적용해 대량 수집/재처리에서도 번역 성공률을 확보한다.
"""
from deep_translator import GoogleTranslator, MyMemoryTranslator
import asyncio
import logging
import random
import re
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

# MyMemory fallback 언어 코드(지역 포함 필요). Google 이 'No translation found' 로
# 간헐 실패할 때 무료·무키 대안으로 사용.
MYMEMORY_MAP = {
    "ko": "ko-KR", "ja": "ja-JP", "zh-cn": "zh-CN", "zh-tw": "zh-TW", "zh": "zh-CN",
    "es": "es-ES", "pt": "pt-PT", "de": "de-DE", "fr": "fr-FR", "ru": "ru-RU",
    "it": "it-IT", "tr": "tr-TR", "nl": "nl-NL", "id": "id-ID", "th": "th-TH",
    "vi": "vi-VN", "ar": "ar-SA", "pl": "pl-PL", "sv": "sv-SE",
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


# 번역기가 **에러 페이지 HTML 을 번역 결과로 반환**하는 경우가 있다. 실측으로
# "Error 500 (Server Error)!!1500.That's an error..." 가 content_translated 에
# 3,887행 저장돼 있었고, 원문은 정상 한국어 결함 제보였다. 이 값이 그대로 저장되면
# 감성·카테고리·결함 추출이 전부 에러 텍스트 기준으로 계산돼 오염된다.
_ERROR_PAGE_RE = re.compile(
    r"^\s*Error\s+\d{3}\b|That['’]s an error|Server Error\)!!|"
    r"^\s*<!DOCTYPE|^\s*<html",
    re.IGNORECASE,
)


def _looks_like_error_page(s: str) -> bool:
    return bool(s) and bool(_ERROR_PAGE_RE.search(s[:200]))


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


# Google 무료 엔드포인트는 ~3000자 초과 단일 요청에서 RequestError 로 실패한다
# (4456자 한국어 기사 실측). 그 예외 메시지에 'connection' 이 들어가 rate-limit 로
# 오인 재시도까지 유발 → 긴 기사가 backlog 최전방을 영구 봉쇄했다. 청크 분할로 해소.
_CHUNK = 2000          # 안정 처리 상한(2000·3000 성공, 4999 실패 실측)
_MAX_CHARS = 6000      # 분석(감성·토픽)엔 앞부분으로 충분 — 노이즈 기사 과다 호출 방지


def _split_chunks(text: str, size: int):
    """경계(개행·문장·공백) 근처에서 잘라 size 이하 청크 리스트로 분할."""
    chunks = []
    i, n = 0, len(text)
    while i < n:
        end = min(i + size, n)
        if end < n:
            b = max(text.rfind("\n", i, end), text.rfind(". ", i, end),
                    text.rfind(" ", i, end))
            if b > i:
                end = b + 1
        chunks.append(text[i:end])
        i = end
    return chunks


# @lat: translate_to_english — [[nlp#Translation]] 참조.
async def translate_to_english(text: str, source_lang: str = "auto") -> str:
    """텍스트를 영어로 번역. 실패 시 원문 반환(데이터 보존).

    긴 텍스트는 _CHUNK 단위로 나눠 각각 번역해 이어붙인다(Google 무료 한도 회피)."""
    if source_lang in SKIP_LANGS:
        return text

    text = text[:_MAX_CHARS]
    src = LANG_MAP.get(source_lang, source_lang)

    if len(text) <= _CHUNK:
        return await _translate_chunk(text, source_lang, src)

    parts = _split_chunks(text, _CHUNK)
    outs, any_ok = [], False
    for p in parts:
        r = await _translate_chunk(p, source_lang, src)
        if r and r != p:
            any_ok, r_out = True, r
        else:
            r_out = p  # 실패 청크는 원문 유지
        outs.append(r_out)
    joined = "\n".join(outs)
    return joined if any_ok else text


async def _translate_chunk(text: str, source_lang: str, src: str) -> str:
    """단일 청크(_CHUNK 이하)를 Google→auto→MyMemory 순으로 번역."""
    loop = asyncio.get_event_loop()

    # 1차: Google (rate-limit 시 지수 백오프 재시도)
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            await _throttle()
            result = await loop.run_in_executor(
                None,
                lambda: GoogleTranslator(source=src, target="en").translate(text),
            )
            if result and result != text and not _looks_like_error_page(result):
                return result
            # 원문 그대로 반환 = 언어 오탐으로 Google 이 번역 안 함(예: 한국어를 tr 로 감지).
            # 성공으로 치면 안 됨 → auto 재감지로 넘어감.
            break  # 빈/무변경 결과 → auto/MyMemory fallback
        except Exception as e:
            if attempt < _MAX_RETRIES and _is_rate_limit(e):
                backoff = min(2 ** attempt + random.uniform(0, 1), 30)
                await asyncio.sleep(backoff)
                continue
            logger.debug(f"Google 번역 실패 ({source_lang}): {e} → auto/MyMemory fallback")
            break

    # 1.5차: source='auto' 재시도 — 언어 오탐(한국어가 tr/pt 로 감지 등) 대응.
    if src != "auto":
        try:
            await _throttle()
            result = await loop.run_in_executor(
                None,
                lambda: GoogleTranslator(source="auto", target="en").translate(text),
            )
            if result and result != text and not _looks_like_error_page(result):
                return result
        except Exception:
            pass

    # 2차 fallback: MyMemory (무료·무키). Google 이 간헐 'No translation found' 낼 때 대응.
    mm = MYMEMORY_MAP.get(source_lang) or MYMEMORY_MAP.get(src)
    if mm:
        try:
            await _throttle()
            result = await loop.run_in_executor(
                None,
                lambda: MyMemoryTranslator(source=mm, target="en-US").translate(text),
            )
            if result and not _looks_like_error_page(result):
                return result
        except Exception as e:
            logger.warning(f"번역 실패 (MyMemory {source_lang}): {e}")
    return text  # 둘 다 실패 → 원문 보존
