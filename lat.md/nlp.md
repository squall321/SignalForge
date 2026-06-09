# NLP

VOC 텍스트 처리 파이프라인 구성 요소. 전체 흐름: [[voc-pipeline]]

## Language Detection

Source: [[crawler/nlp/detector.py#detect_language]]

- `langdetect` 라이브러리 사용. 재현성을 위해 `seed=42` 고정.
- 입력 텍스트 500자로 제한하여 성능 최적화.
- 10자 미만 텍스트는 `en`으로 기본 반환.

## Translation

Source: [[crawler/nlp/translator.py#translate_to_english]]

- `deep-translator.GoogleTranslator` 사용 (무료, 5000자 제한).
- 언어 코드가 `SKIP_LANGS = {"en", "und"}`이면 번역 건너뜀.
- `asyncio.run_in_executor`로 동기 라이브러리를 비동기 래핑.

> **확장 포인트:** DeepL API 키(`DEEPL_API_KEY`)가 있으면 `deep-translator.DeepL`로 교체 권장. 품질이 더 높음.

## Sentiment Analysis

Source: [[crawler/nlp/sentiment.py#analyze_sentiment]]

- VADER(`vaderSentiment`) 사용. 룰 기반으로 빠른 처리.
- `compound >= 0.05` → `positive`, `<= -0.05` → `negative`, 나머지 → `neutral`
- 입력 텍스트 1000자로 제한.

> **확장 포인트:** 고품질 분석이 필요한 경우 Claude API(`ANTHROPIC_API_KEY`)로 앙상블 적용.
> 우선순위: 높은 `engagement_score` VOC에만 Claude API 적용하여 비용 절감.

## Category Classification

Source: [[crawler/nlp/categorizer.py#classify_categories]]

- 키워드 매칭 방식. 각 카테고리별 한국어/영어 키워드 목록으로 점수 계산.
- 매칭 점수 내림차순으로 최대 5개 카테고리 반환.
- [[categories]]에 정의된 12개 카테고리 코드 사용.

> **비즈니스 규칙:** 카테고리가 하나도 매칭되지 않으면 빈 배열 `[]` 반환. NULL 아님.
