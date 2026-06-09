# Topic 분류기 Multi-label Joint F1 — R27 (2026-06-06)

- 프롬프트 버전: `v3.1` (axis=`both`)
- 평가 모델: `qwen2.5:14b`
- 샘플: 100건 (multi=63 / single=37)
- 평가: auto_set = `voc.topics`, llm_set = LLM JSON 응답
- **R27 듀얼 축**: primary 는 단일-라벨 prompt (temp 0.0, max_tok 30, max_retries 1), multi 는 v3 prompt (temp 0.2, max_tok 100) 별도 호출

## Overall Multi-label Metrics

| 지표 | 값 |
|---|---:|
| Exact match (set equality) | **0.290** |
| Partial match (∩ ≥ 1) | **0.850** |
| Jaccard 평균 | **0.540** |
| F1-micro 평균 (row 단위) | **0.631** |
| F1-macro (per-topic 평균) | **0.648** |

## Primary-label F1 vs R10 / R18 / R23 / R24

- R27 primary 정확도 (top1 only): **0.480**
- R10 0.678 / R18 v1 0.640 / R23 0.406 / R24 0.430 / R25 0.500 / R26 0.450 / **R27 0.480**

| topic | R27 primary F1 | R27 multi F1 | Δ(multi-primary) |
|---|---:|---:|---:|
| positive_general | 0.522 | 0.632 | +0.110 |
| negative_general | 0.429 | 0.654 | +0.225 |
| question | 0.429 | 0.635 | +0.206 |
| comparison | 0.500 | 0.596 | +0.096 |
| price_purchase | 0.400 | 0.516 | +0.116 |
| service_repair | 0.588 | 0.880 | +0.292 |
| experience | 0.357 | 0.400 | +0.043 |
| expectation | 0.600 | 0.720 | +0.120 |
| emotion_only | 0.857 | 0.800 | -0.057 |

## Per-topic Multi-label F1 (set 기반)

| topic | auto_support | llm_support | TP | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| positive_general | 24 | 14 | 12 | 0.500 | 0.857 | 0.632 |
| negative_general | 22 | 30 | 17 | 0.773 | 0.567 | 0.654 |
| question | 29 | 34 | 20 | 0.690 | 0.588 | 0.635 |
| comparison | 19 | 38 | 17 | 0.895 | 0.447 | 0.596 |
| price_purchase | 14 | 17 | 8 | 0.571 | 0.471 | 0.516 |
| service_repair | 13 | 12 | 11 | 0.846 | 0.917 | 0.880 |
| experience | 26 | 14 | 8 | 0.308 | 0.571 | 0.400 |
| expectation | 14 | 11 | 9 | 0.643 | 0.818 | 0.720 |
| emotion_only | 4 | 6 | 4 | 1.000 | 0.667 | 0.800 |

## Set 크기 분포

| 크기 | auto | llm |
|---|---:|---:|
| 1 | 37 | 34 |
| 2 | 61 | 56 |
| 3 | 2 | 10 |

## 잘못 분류 예시 (Jaccard < 0.5, 최대 12건)

- id=1114095 | auto=`['positive_general', 'price_purchase']` → llm=`['price_purchase', 'question']` | jacc=0.33 | "@Kimppaggujjing
Mr.
I also buy a carnival limousine and it's perfect for looking for a TV. tivo stream 4k
Which shopping mall did you purchase from?"
- id=6268 | auto=`['service_repair', 'experience']` → llm=`['question', 'price_purchase', 'service_repair']` | jacc=0.25 | "I enjoyed reading your valuable review.
It was paid,,, how much was it?
I also bought a Fold 7, but I'm worried.
I use it at a construction site and after reading the article, I am scared.
How much do"
- id=1166023 | auto=`['experience', 'negative_general']` → llm=`['experience', 'comparison']` | jacc=0.33 | "Galaxy S20
Even so, I was forced to use it because of rust, so I'm using my girlfriend's used S20 Plus for over 6 years. I don't have any major complaints, but I don't really like the camera because i"
- id=975290 | auto=`['price_purchase', 'positive_general']` → llm=`['comparison', 'positive_general']` | jacc=0.33 | "I currently own a 1000xm3. My colleague recently just bought an Airpods pro and was going on for ages about how awesome it is. I offered him to try out my XM3s. He tried them out for 15 mins. I never "
- id=14937 | auto=`['negative_general']` → llm=`['experience']` | jacc=0.00 | "You can just search for Samsung Monitor M5.
It's FHD, so the specs aren't that great, but it has a smart TV function, so I mainly use it for 30 by Me purposes."
- id=7449 | auto=`['question', 'experience']` → llm=`['experience', 'negative_general']` | jacc=0.33 | "@junapa
Mr.
Think about it carefully.
How to take pictures in a situation where all settings are messed up...
Is that just a drawback?
And take a good look...
I have been using Pro Mode on the S22U fo"
- id=1165853 | auto=`['question']` → llm=`['emotion_only']` | jacc=0.00 | "Even if I turn, where should I turn? ㅜㅜ"
- id=518113 | auto=`['question']` → llm=`['negative_general', 'expectation']` | jacc=0.00 | "To be honest, it wasn't top 10 power, was it?
Posted Category
Kiwoom
I know that the Beast is truly the worst of all time, but everyone expected him to not be 10th this year, so how can they push this"
- id=23245 | auto=`['question', 'experience']` → llm=`['comparison', 'question']` | jacc=0.33 | "I'm worried about changing my phone after using my Flip 5 for 2 and a half years, but I'm waiting for 26 Wool vs. Flip 8.
I don't play any phone games and have no intention of playing any more.
​
I re"
- id=13024 | auto=`['question', 'positive_general']` → llm=`['price_purchase', 'question']` | jacc=0.33 | "Shinsegae S26 Ultra delivery related
I received a KakaoTalk message saying it was a sales contract.
Is it going to be Besson soon?"
- id=10247 | auto=`['expectation', 'experience']` → llm=`['comparison', 'negative_general']` | jacc=0.00 | "I don't see much room for battery improvement on Galaxy phones.
I use the iPhone 15 and the Galaxy A34 at the same time, and although the A34 has a larger battery capacity, the battery leak rate is fa"
- id=13276 | auto=`['experience']` → llm=`['negative_general', 'comparison', 'question']` | jacc=0.00 | "I’m so stressed because of the S26 Ultra..lol.
I switched from iPhone 17 during this crisis and I really like it.
Ah, but I've been using it for a few days now, and the pain in my hands and wrists doe"
