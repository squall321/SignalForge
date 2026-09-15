"""
NLP 처리 파이프라인
언어감지 → 번역 → 감성분석 → 카테고리분류 → 참여도계산
"""
from typing import List
import asyncio
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from nlp.detector import detect_language
from nlp.translator import translate_to_english
from nlp.sentiment import analyze_sentiment, analyze
from nlp.categorizer import classify_categories

logger = logging.getLogger(__name__)


# @lat: process_voc_list — [[voc-pipeline#Flow]] 참조.
async def process_voc_list(vocs, translate_deadline=None) -> list:
    """StandardVOC 리스트에 NLP 처리 적용.

    translate_deadline: time.monotonic 기준 번역 마감시각. 넘기면 번역을
    건너뛰고 원문을 남긴다(저장은 되므로 backfill 로 메울 수 있다).
    번역 비용은 건당 추정이 불가능해서 건수가 아니라 시각으로 끊는다.
    """
    if translate_deadline is not None:
        from nlp.translator import set_deadline
        set_deadline(translate_deadline)
    tasks = [process_single(voc) for voc in vocs]
    return await asyncio.gather(*tasks, return_exceptions=False)


async def process_single(voc) -> object:
    """단일 VOC NLP 처리"""
    try:
        # 1. 언어 감지
        lang = detect_language(voc.content_original)
        voc.language_detected = lang

        # 2. 번역 (비영어)
        if lang and lang != "en":
            translated = await translate_to_english(voc.content_original, source_lang=lang)
            voc.content_translated = translated
        else:
            voc.content_translated = voc.content_original

        # 3. 감성 분석 — 한국어는 원문에서 직접(번역 의존 X), 그 외는 번역본/VADER
        if lang == "ko":
            score, label = analyze(voc.content_original, lang="ko")
        else:
            score, label = analyze_sentiment(voc.content_translated or voc.content_original)
        voc.sentiment_score = score
        voc.sentiment_label = label

        # 4. 카테고리 분류 (번역본 우선, 없으면 원문)
        voc.categories = classify_categories(voc.content_translated or voc.content_original)

        # 5. 참여도 점수 계산
        voc.engagement_score = _calc_engagement(
            voc.likes_count, voc.comments_count, voc.shares_count
        )

    except Exception as e:
        logger.warning(f"NLP 처리 실패 ({voc.external_id}): {e}")

    return voc


# @lat: _calc_engagement — [[voc-pipeline#Engagement Score 계산]] 참조.
def _calc_engagement(likes: int, comments: int, shares: int) -> float:
    """참여도 점수 정규화 (0 ~ 100)"""
    raw = likes * 1.0 + comments * 2.0 + shares * 3.0
    # log 스케일 정규화 (최대 10000 기준)
    import math
    return round(min(math.log1p(raw) / math.log1p(10000) * 100, 100), 2)
