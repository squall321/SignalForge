# VOC Pipeline

수집(Raw) → 언어감지 → 번역 → 감성분석 → 카테고리분류 → DB저장

## Flow

각 단계는 순차 실행되며 실패 시 해당 VOC만 건너뛴다. 전체 파이프라인은 `BaseCrawler.run()`이 실행한다.

```text
crawl()          → List[RawVOC]
normalize()      → List[StandardVOC]  (플랫폼 특화 필드 → 공통 포맷)
process_voc_list()  ──┬── detect_language()    # langdetect
                      ├── translate_to_english() # deep-translator (비영어만)
                      ├── analyze_sentiment()    # VADER
                      └── classify_categories()  # 키워드 매칭
save()           → DB INSERT (ON CONFLICT DO NOTHING)
```

## RawVOC vs StandardVOC

- `RawVOC` — 플랫폼에서 수집한 원시 데이터. `content`, `external_id`, `source_url` 필드만 필수.
- `StandardVOC` — 정규화된 공통 포맷. NLP 처리 결과(`sentiment_score`, `categories` 등)가 채워진다.

Source: [[crawler/base/crawler.py#RawVOC]]
Source: [[crawler/base/crawler.py#StandardVOC]]
Source: [[crawler/nlp/pipeline.py#process_voc_list]]

## Engagement Score 계산

`engagement_score = min(log1p(likes×1 + comments×2 + shares×3) / log1p(10000) × 100, 100)`

최대 기준값 10000으로 log 스케일 정규화하여 0~100 범위로 출력.

Source: [[crawler/nlp/pipeline.py#_calc_engagement]]

## 중복 방지

`save()` 시 `ON CONFLICT (platform_id, external_id) DO NOTHING`으로 멱등성을 보장한다.
같은 VOC를 재수집해도 DB에 영향 없음.

Source: [[crawler/base/crawler.py#BaseCrawler]]
