# Topic 분류기 정확도 LLM Spot-Check (2026-06-04)

- 평가 모델: `qwen2.5:14b`
- 샘플: 90건 (topic 당 10건 균등)
- 전체 정확도: **0.456**

## Per-topic 정확도

| topic | support | llm_count | correct | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| positive_general | 10 | 1 | 1 | 0.100 | 1.000 | 0.182 |
| negative_general | 10 | 6 | 3 | 0.300 | 0.500 | 0.375 |
| question | 10 | 12 | 5 | 0.500 | 0.417 | 0.455 |
| comparison | 10 | 16 | 5 | 0.500 | 0.312 | 0.385 |
| price_purchase | 10 | 7 | 1 | 0.100 | 0.143 | 0.118 |
| service_repair | 10 | 8 | 6 | 0.600 | 0.750 | 0.667 |
| experience | 10 | 6 | 4 | 0.400 | 0.667 | 0.500 |
| expectation | 10 | 11 | 6 | 0.600 | 0.545 | 0.571 |
| emotion_only | 10 | 11 | 10 | 1.000 | 0.909 | 0.952 |

## Confusion Matrix (auto → llm)

| auto \ llm | positive_general | negative_general | question | comparison | price_purchase | service_repair | experience | expectation | emotion_only | other |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| positive_general | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 2 | 0 | 3 |
| negative_general | 0 | 3 | 1 | 3 | 0 | 0 | 0 | 1 | 0 | 2 |
| question | 0 | 0 | 5 | 2 | 2 | 0 | 1 | 0 | 0 | 0 |
| comparison | 0 | 0 | 0 | 5 | 2 | 0 | 0 | 0 | 0 | 3 |
| price_purchase | 0 | 1 | 5 | 0 | 1 | 0 | 0 | 2 | 0 | 1 |
| service_repair | 0 | 0 | 1 | 1 | 0 | 6 | 1 | 0 | 0 | 1 |
| experience | 0 | 0 | 0 | 3 | 1 | 1 | 4 | 0 | 0 | 1 |
| expectation | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 6 | 1 | 1 |
| emotion_only | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 |

## 잘못 분류 예시 (auto ≠ llm, 최대 10건)

- id=966547 | auto=`negative_general` → llm=`comparison` | "Stronger by Science covered this in a podcast episode maybe a year ago or so. There does seem to be reasonably good evidence that basal metabolic rates have dropped over the last century without any c"
- id=954413 | auto=`positive_general` → llm=`other` | "* Blog Post: https://blog.roboflow.com/roboflow-100/ * Paper: https://arxiv.org/abs/2211.13523 * Github: https://github.com/roboflow-ai/roboflow-100-benchmark At Roboflow, we've seen users fine-tune h"
- id=548763 | auto=`price_purchase` → llm=`question` | "Buds 4 Pro pre-order review Did you receive a Starbucks coupon?
Why don't you give it to me?"
- id=976657 | auto=`experience` → llm=`other` | "Show HN: Braand.co
Over the last 15 years I’ve been responsible for brand design across household names including Samsung, PayPal and LG Mobile. In addition to my design experience, I’ve developed a p"
- id=979405 | auto=`negative_general` → llm=`other` | "in response to the unfortunately flagged sibling comment, the planned vaguely-rio-like windowing system for yeso is called wercam, but there is only a prototype implementation of it in the repo so far"
- id=370131 | auto=`expectation` → llm=`negative_general` | "What’s disappointing about the wide fold
It seems like the established theory is to go for the cheaper version by reducing costs more than the regular fold, but in fact, there are a lot more users who"
- id=51370 | auto=`positive_general` → llm=`negative_general` | "와이드폴드 나와도 만족못할듯
작는걸론 만족 못하는 몸뚱이가 되어버림ㅠ
- dc official App"
- id=975306 | auto=`positive_general` → llm=`other` | "Vernor Vinge's A Fire Upon the Deep (1992) depicts a galaxy of countless highly advanced civilizations in the far future - and describes its "net" as "the net of a million lies". However, it also depi"
- id=967101 | auto=`negative_general` → llm=`other` | ">The universe will collapse into a single black hole. Basically no one believes the Big Crunch theory is likely to be accurate these days, which is what I imagine he is referring to here - but it's al"
- id=178150 | auto=`experience` → llm=`price_purchase` | "I sold my Galaxy 25 Ultra 256GB at Sello last week.
After inspecting, there was one small blemish, so 120,000 won was deducted...
Did you just sell it because there was a 100,000 won additional fee pr"

## 사전 보강 권고

- positive_general: precision=0.1 (support=10) — 키워드 사전 보강 권고
- price_purchase: precision=0.1 (support=10) — 키워드 사전 보강 권고
