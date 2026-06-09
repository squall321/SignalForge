# Topic LLM Refine — Track A2 (2026-06-05)

- 모델: `qwen2.5:14b`
- 샘플: 45건 (대상 ['positive_general', 'comparison', 'experience'], topic 당 15건)
- DB 라벨 == LLM 라벨: 23건 (51.1%)
- APPLY 모드: 아니오 (dry-run)

## db_primary 별 LLM 라벨 분포

### comparison (n=15)

- comparison: 12 ✅
- question: 2
- negative_general: 1

### experience (n=15)

- comparison: 6
- experience: 4 ✅
- emotion_only: 2
- positive_general: 1
- expectation: 1
- negative_general: 1

### positive_general (n=15)

- positive_general: 7 ✅
- comparison: 4
- expectation: 1
- other: 1
- price_purchase: 1
- question: 1

## 잠재 변경 후보 — db → llm 빈도 TOP 20

| db_primary | llm_label | n |
|---|---|---:|
| experience | comparison | 6 |
| positive_general | comparison | 4 |
| experience | emotion_only | 2 |
| comparison | question | 2 |
| experience | positive_general | 1 |
| comparison | negative_general | 1 |
| positive_general | expectation | 1 |
| experience | expectation | 1 |
| positive_general | other | 1 |
| experience | negative_general | 1 |
| positive_general | price_purchase | 1 |
| positive_general | question | 1 |

## 사례 (최대 30건, db≠llm)

- id=951471 | db=`experience` → llm=`comparison` | "I think the ignorance point was more about ignoring the entire Android userbase who wouldn't benefit from these donated AirTags, which is a valid point for a government program. (I also currently own "
- id=3645 | db=`experience` → llm=`comparison` | "I'm still using the iPhone SE 2nd generation model and I'm jealous."
- id=7811 | db=`experience` → llm=`positive_general` | "@
Since you are not fanless, of course there is fan noise :)
It varies from user to user, but in most cases, it seems difficult to hear fan noise when the power is off.
I plan to use this part more an"
- id=572148 | db=`comparison` → llm=`negative_general` | "Is the Buds Pro 4 no-can really this trash?
I was using AirPod Pro 2, but when I switched to a Galaxy phone, I moved on to Buds Pro 4, but the no-can is too bad.
first time
I wondered if there was a p"
- id=11994 | db=`positive_general` → llm=`comparison` | "Even if it’s s20 for A… Because it is a better model than A.
A flagship is a flagship.
Otherwise, I recommend the s10 series for its excellent cost-effectiveness."
- id=5504 | db=`positive_general` → llm=`expectation` | "삼성전자, 갤럭시의 혁신 기술로 2026 밀라노 코르티나 동계올림픽 감동을 전 세계에 
삼성전자, 갤럭시의 혁신 기술로 2026 밀라노 코르티나 동계올림픽 감동을 전 세계에 전달
국제올림픽위원회(IOC)의 공식 파트너(Worldwide Partner)인 삼성전자가 2026 밀라노 코르티나 동계올림픽에서 모바일 혁신 기술을 통해 선수, 팬, 커뮤니티를 더욱 "
- id=280027 | db=`experience` → llm=`expectation` | "The 9600 will be released in a month. Will it be equipped with the 9500 or the 9600?"
- id=89271 | db=`experience` → llm=`comparison` | "From the perspective of someone who doesn't play games, there doesn't seem to be any benefit to changing to 26 wool unless you want to use the privacy feature.
I think it would be better to use 24 woo"
- id=7109 | db=`positive_general` → llm=`comparison` | "1. As you know, AI depends on your taste... The reason is that chatgpt is the most general purpose, and the rest are specialized, so I recommend using additional ones depending on your own use. People"
- id=163842 | db=`positive_general` → llm=`comparison` | "If you go from the entry-level Galaxy A to the S25 Ultra,
Is it a new world???
recommended
0
share"
- id=13276 | db=`experience` → llm=`emotion_only` | "I’m so stressed because of the S26 Ultra..lol.
I switched from iPhone 17 during this crisis and I really like it.
Ah, but I've been using it for a few days now, and the pain in my hands and wrists doe"
- id=962436 | db=`positive_general` → llm=`other` | "It is written in the annals of the galaxy that the Great Prophet Zarquon on his return will bring with him the first publicly useable version of GNU/Hurd which will signify a change for the universe s"
- id=1011417 | db=`comparison` → llm=`question` | "> I'd enable that setting and I imagine lots of others would I absolutely would - I run a few sites under Cloudflare. Instead of slowing down the traffic coming from the FCC (which is likely to be sma"
- id=172119 | db=`positive_general` → llm=`comparison` | "To be honest, as a writer, I don't know if I'm reading a PDF, but I'm enlarging my handwriting, so I don't think the ratio is a problem.
The writing feel of the iPad is so great that the Galaxy Tab ha"
- id=159008 | db=`experience` → llm=`comparison` | "I'm using the Fold 5, but I want the Fold 7 because it's thin. Haha
The Fold's large screen is also useful when watching Netflix or YouTube while lying on the sofa.
When I drive, I turn on T Map on my"
- id=412350 | db=`comparison` → llm=`question` | "If you replace the back of the Galaxy S23 Plus, does the battery also change??
I asked because there was no English under Samsung on the back panel.
I heard it broke and I changed it once.
From what I"
- id=7585 | db=`experience` → llm=`comparison` | "@Yongi-hyung
Third parties (Thunderbird, Apple Calendar) can synchronize all schedules, but Google products only synchronize for one year...
It's really absurd. I think the iPhone, not Android, can be"
- id=391852 | db=`experience` → llm=`comparison` | "Why do Samsung keep changing the UI in less than a year?
I think everyone will like it if I change it unconditionally.
I also use an iPad, and after using it for several years, there hasn't been any m"
- id=283813 | db=`experience` → llm=`negative_general` | "These bastards are really fucking idiots... It's not like they'll be in business for a year and then quit.
The S25 series is also supported from the beginning! If I had said that, it would have been l"
- id=13672 | db=`positive_general` → llm=`price_purchase` | "Galcams S26 Ultra 512gb for recording
I had to change my phone a few days ago, so I went to the store and kept looking.
I can't put it off any longer, so I just compromise and purchase it through Galc"
- id=1114095 | db=`positive_general` → llm=`question` | "@Kimppaggujjing
Mr.
I also buy a carnival limousine and it's perfect for looking for a TV. tivo stream 4k
Which shopping mall did you purchase from?"
- id=285042 | db=`experience` → llm=`emotion_only` | "I'm currently writing 23 Ullari, but I guess it's just my mood."
