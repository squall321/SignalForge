# Topic 분류기 Multi-label Joint F1 — R24 (2026-06-05)

- 평가 모델: `qwen2.5:14b`
- 샘플: 100건 (multi=63 / single=37)
- 평가: auto_set = `voc.topics`, llm_set = LLM JSON 응답

## Overall Multi-label Metrics

| 지표 | 값 |
|---|---:|
| Exact match (set equality) | **0.150** |
| Partial match (∩ ≥ 1) | **0.800** |
| Jaccard 평균 | **0.429** |
| F1-micro 평균 (row 단위) | **0.535** |
| F1-macro (per-topic 평균) | **0.539** |

## Primary-label F1 vs R10 / R18 / R23

- R24 primary 정확도 (top1 only): **0.430**
- R10 0.678 / R18 v1 0.640 / R23 0.406 / **R24 0.430**

| topic | R24 primary F1 | R24 multi F1 | Δ(multi-primary) |
|---|---:|---:|---:|
| positive_general | 0.435 | 0.500 | +0.065 |
| negative_general | 0.483 | 0.545 | +0.062 |
| question | 0.385 | 0.655 | +0.270 |
| comparison | 0.476 | 0.436 | -0.040 |
| price_purchase | 0.417 | 0.421 | +0.004 |
| service_repair | 0.400 | 0.833 | +0.433 |
| experience | 0.125 | 0.278 | +0.153 |
| expectation | 0.471 | 0.564 | +0.093 |
| emotion_only | 0.750 | 0.615 | -0.135 |

## Per-topic Multi-label F1 (set 기반)

| topic | auto_support | llm_support | TP | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| positive_general | 24 | 12 | 9 | 0.375 | 0.750 | 0.500 |
| negative_general | 22 | 22 | 12 | 0.545 | 0.545 | 0.545 |
| question | 29 | 29 | 19 | 0.655 | 0.655 | 0.655 |
| comparison | 19 | 59 | 17 | 0.895 | 0.288 | 0.436 |
| price_purchase | 14 | 24 | 8 | 0.571 | 0.333 | 0.421 |
| service_repair | 13 | 11 | 10 | 0.769 | 0.909 | 0.833 |
| experience | 26 | 10 | 5 | 0.192 | 0.500 | 0.278 |
| expectation | 14 | 25 | 11 | 0.786 | 0.440 | 0.564 |
| emotion_only | 4 | 9 | 4 | 1.000 | 0.444 | 0.615 |

## Set 크기 분포

| 크기 | auto | llm |
|---|---:|---:|
| 1 | 37 | 16 |
| 2 | 61 | 67 |
| 3 | 2 | 17 |

## 잘못 분류 예시 (Jaccard < 0.5, 최대 12건)

- id=1114095 | auto=`['positive_general', 'price_purchase']` → llm=`['price_purchase', 'question']` | jacc=0.33 | "@Kimppaggujjing
Mr.
I also buy a carnival limousine and it's perfect for looking for a TV. tivo stream 4k
Which shopping mall did you purchase from?"
- id=6268 | auto=`['service_repair', 'experience']` → llm=`['positive_general', 'question', 'service_repair']` | jacc=0.25 | "I enjoyed reading your valuable review.
It was paid,,, how much was it?
I also bought a Fold 7, but I'm worried.
I use it at a construction site and after reading the article, I am scared.
How much do"
- id=1166023 | auto=`['experience', 'negative_general']` → llm=`['expectation', 'comparison']` | jacc=0.00 | "Galaxy S20
Even so, I was forced to use it because of rust, so I'm using my girlfriend's used S20 Plus for over 6 years. I don't have any major complaints, but I don't really like the camera because i"
- id=975290 | auto=`['price_purchase', 'positive_general']` → llm=`['comparison', 'negative_general']` | jacc=0.00 | "I currently own a 1000xm3. My colleague recently just bought an Airpods pro and was going on for ages about how awesome it is. I offered him to try out my XM3s. He tried them out for 15 mins. I never "
- id=143715 | auto=`['question', 'negative_general']` → llm=`['question', 'comparison']` | jacc=0.33 | "Which part do you think is a waste of money? Is there really no reason to go up to 1 tera? Should I just go to 512?"
- id=14937 | auto=`['negative_general']` → llm=`['comparison', 'experience']` | jacc=0.00 | "You can just search for Samsung Monitor M5.
It's FHD, so the specs aren't that great, but it has a smart TV function, so I mainly use it for 30 by Me purposes."
- id=7449 | auto=`['question', 'experience']` → llm=`['experience', 'comparison', 'expectation']` | jacc=0.25 | "@junapa
Mr.
Think about it carefully.
How to take pictures in a situation where all settings are messed up...
Is that just a drawback?
And take a good look...
I have been using Pro Mode on the S22U fo"
- id=17885 | auto=`['comparison', 'negative_general']` → llm=`['comparison', 'expectation']` | jacc=0.33 | "Galaxy's update is dropped so it stays in good condition, but I think it's because there are a lot of people who update iPhone as soon as it comes out. I can't use it since the 13 mini upgraded to ios"
- id=13549 | auto=`['negative_general', 'question']` → llm=`['question', 'comparison', 'expectation']` | jacc=0.25 | "What's the problem? Are you dissatisfied with the fact that you're still using the S20 Plus?
I'm planning to buy a new phone and use it for 6 years, but I'm thinking of buying a used S24 because it's "
- id=415582 | auto=`['service_repair', 'question']` → llm=`['question', 'comparison', 'price_purchase']` | jacc=0.25 | "What extended warranty do you buy for Z Fold 5?
best warranty, buy two s23 ultra"
- id=7811 | auto=`['experience']` → llm=`['positive_general', 'expectation']` | jacc=0.00 | "@
Since you are not fanless, of course there is fan noise :)
It varies from user to user, but in most cases, it seems difficult to hear fan noise when the power is off.
I plan to use this part more an"
- id=518113 | auto=`['question']` → llm=`['negative_general', 'comparison']` | jacc=0.00 | "To be honest, it wasn't top 10 power, was it?
Posted Category
Kiwoom
I know that the Beast is truly the worst of all time, but everyone expected him to not be 10th this year, so how can they push this"
