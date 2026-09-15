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

## 짧은 텍스트 언어 오탐 — 고치지 않기로 한 것

비영어로 잡혔는데 번역이 없는 행이 19,428건 있다(2026-09-15 실측, 500자 이하).
표본을 보면 상당수가 **실제로는 영어**인데 오탐된 것이다 —
`'Z Fold 8 Ultra'`→no, `'Very good'`→af, `'can do better'`→it.

두 가지 판별을 시도했고 **둘 다 실패**했다.

1. **길이 규칙** — 백필 스크립트가 쓰는 규칙(80자 미만 + 비라틴 문자 없음이면
   'en'으로 정정)을 실시간 경로에 넣어보면 표본 3,000건 중 17.7%가 뒤집힌다.
   그런데 그 안에 `'siapz rusak bergaris di lipetannya cuy'`(진짜 인도네시아어)
   같은 것이 섞인다. 인니·필리핀·베트남은 커버리지 시장이라 실제 손실이다.
   (백필은 `language_detected IS NULL` 행이 대상이라 "아무 라벨이라도 낫다"는
   다른 상황이었다 — 그래서 거기선 쓸 수 있다.)

2. **감지 신뢰도** — `detect_langs()` 의 확률로 가르려 했으나 langdetect 는
   짧은 텍스트에 과신한다. `'can do better'` → it **1.00**, `'1st comment'`
   → fr **1.00**. 미번역 행의 86%가 이미 0.9 이상이라 아예 분리되지 않는다.

**그래서 놔둔다.** 실질 피해가 제한적이기 때문이다 — 오탐된 영어는 번역을
시도했다가 원문이 그대로 남고, 감성 분석과 FTS 는 그 영어 원문 위에서 정상
동작한다. 낭비되는 것은 번역 API 호출과 `language_detected` 통계의 정확도뿐이다.
제대로 고치려면 짧은 텍스트용 언어 모델이 필요하다.

Source: [[crawler/nlp/detector.py#detect_language]]
