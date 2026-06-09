# Topic 분류기 정확도 LLM Spot-Check (2026-06-04)

- 평가 모델: `qwen2.5:14b`
- 샘플: 90건 (topic 당 10건 균등)
- 전체 정확도: **0.678**

## Per-topic 정확도

| topic | support | llm_count | correct | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| positive_general | 10 | 5 | 4 | 0.400 | 0.800 | 0.533 |
| negative_general | 10 | 9 | 6 | 0.600 | 0.667 | 0.632 |
| question | 10 | 11 | 8 | 0.800 | 0.727 | 0.762 |
| comparison | 10 | 15 | 6 | 0.600 | 0.400 | 0.480 |
| price_purchase | 10 | 11 | 5 | 0.500 | 0.455 | 0.476 |
| service_repair | 10 | 8 | 8 | 0.800 | 1.000 | 0.889 |
| experience | 10 | 10 | 6 | 0.600 | 0.600 | 0.600 |
| expectation | 10 | 9 | 8 | 0.800 | 0.889 | 0.842 |
| emotion_only | 10 | 11 | 10 | 1.000 | 0.909 | 0.952 |

## Confusion Matrix (auto → llm)

| auto \ llm | positive_general | negative_general | question | comparison | price_purchase | service_repair | experience | expectation | emotion_only | other |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| positive_general | 4 | 1 | 0 | 2 | 1 | 0 | 1 | 0 | 0 | 1 |
| negative_general | 0 | 6 | 0 | 2 | 1 | 0 | 1 | 0 | 0 | 0 |
| question | 0 | 0 | 8 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| comparison | 0 | 1 | 1 | 6 | 1 | 0 | 1 | 0 | 0 | 0 |
| price_purchase | 1 | 1 | 2 | 0 | 5 | 0 | 0 | 1 | 0 | 0 |
| service_repair | 0 | 0 | 0 | 0 | 1 | 8 | 1 | 0 | 0 | 0 |
| experience | 0 | 0 | 0 | 2 | 2 | 0 | 6 | 0 | 0 | 0 |
| expectation | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 8 | 1 | 0 |
| emotion_only | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 |

## 잘못 분류 예시 (auto ≠ llm, 최대 10건)

- id=972006 | auto=`positive_general` → llm=`other` | "The login page example was actually perfect for illustrating this. Meshing polygons? Centering a div? Go ahead and turn the LLM loose. If you miss any bugs you can just fix them when they get reported"
- id=224851 | auto=`price_purchase` → llm=`negative_general` | "Samsung is eroding trust. If you pre-order, you are now a fool."
- id=235364 | auto=`comparison` → llm=`question` | "Doesn't the iPhone Air work?
‌Galaxy S26
These days, there are so many hot deals coming out.
There were widespread rumors that sales had collapsed.
I know that the iPhone Air is also a failure.
I hope"
- id=687955 | auto=`service_repair` → llm=`price_purchase` | "s25+ mint fucking bite
I want to switch to Silver Shadow
Pay an additional 100,000 won to Samsung Service Center
When replacing the back panel + border
I would love it if there was an option to change"
- id=139637 | auto=`negative_general` → llm=`comparison` | "오늘 핸드폰 사는데 겜용으로 s26+ 별로에요?
퀸텀 2라는 희대의 똥폰 4년 사용하고
오늘 엄마폰 + 내폰 , 26+ 사용 하려는데
울트라사면 좋겠지만 지갑사정이 부담이라
엑시노스가 커뮤에서 악명 높은건 알고있어서
겜 + 서핑용으로 4년정도 사용하는건 지장 없겠죠?"
- id=270579 | auto=`positive_general` → llm=`comparison` | "The difference between Fold 6 and 7 is big, so if you can spend money, I recommend 7."
- id=749735 | auto=`negative_general` → llm=`price_purchase` | "Is it a waste of money to buy AirPods Pro 3 instead of Buds 4 Pro?
I was thinking of buying Buds 3 Pro.
There was so much talk that I gave up.
I'm planning to buy Buds 4 Pro this time.
They say there "
- id=185021 | auto=`price_purchase` → llm=`question` | "s26u 샀는데 전작보다
배터리 사이클 줄었다는데 1000몇회 이렇게 하니까 이해가 안돼는데 대충 몇년쓸거 몇년으로 줄었다 설명 좀 해주라"
- id=976877 | auto=`positive_general` → llm=`comparison` | "Is the alternative really better overall. We upgraded to a samsung fridge last year from two consecutive cheapo-chinese-local walmart-brands and it was worth every penny. It will pay itself in energy "
- id=191026 | auto=`comparison` → llm=`experience` | "iPhone 17 Pro Max 딥 블루 1TB 자급제 약 3달 사용기
원 글은 티스토리에 올렸던 건데
티스토리 원 글에는 사진이 30개가 넘어가고 내용이 길어서
여기 올릴 때는 사진을 추리고
내용도 약 3달간 사용해본 경험 위주로 요약해서 올립니다.
내용을 요약하다보니
다소 문체나 어투가 정제 되지 않은 점 양해 바라며,
원본 사용기는 제 티스토리에서 확"

## 사전 보강 권고

- (없음 — 모든 topic 의 precision ≥ 0.30)
