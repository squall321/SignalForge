"""언어 감지"""
from langdetect import detect, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException
import logging

DetectorFactory.seed = 42  # 재현 가능한 결과
logger = logging.getLogger(__name__)


# @lat: detect_language — [[nlp#Language Detection]] 참조.
def detect_language(text: str) -> str:
    """텍스트 언어를 감지하여 ISO 639-1 코드 반환 (실패 시 'en')"""
    if not text or len(text.strip()) < 10:
        return "en"
    try:
        return detect(text[:500])
    except LangDetectException as e:
        logger.debug(f"언어 감지 실패: {e}")
        return "en"
