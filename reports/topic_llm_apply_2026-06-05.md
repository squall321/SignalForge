# Topic LLM Apply — Track A (2026-06-05)

- 모델: `qwen2.5:14b` / prompt: `v2`
- 샘플: 500건 (대상 ['positive_general', 'comparison', 'experience', 'negative_general'], topic 당 125건)
- DB 라벨 == LLM 라벨: 203건 (40.6%)
- 확장 후보 (db≠llm, llm≠other): 282건
- LLM 'other' 응답: 15건
- DRY_RUN: 아니오 (실제 적용)
- DB 확장: 282건 (백업 → `voc_topics_backup_r23_llm_main`)

## db_primary 별 LLM 라벨 분포

### positive_general (n=125)

- positive_general: 50 ✅
- experience: 18
- negative_general: 16
- price_purchase: 11
- expectation: 7
- comparison: 7
- emotion_only: 6
- question: 6
- other: 3
- service_repair: 1

### negative_general (n=125)

- negative_general: 59 ✅
- question: 27
- price_purchase: 8
- comparison: 7
- emotion_only: 7
- experience: 6
- other: 5
- expectation: 4
- service_repair: 2

### comparison (n=125)

- comparison: 30 ✅
- question: 26
- experience: 20
- negative_general: 11
- positive_general: 10
- price_purchase: 9
- expectation: 6
- other: 5
- emotion_only: 4
- service_repair: 4

### experience (n=125)

- experience: 64 ✅
- question: 15
- price_purchase: 13
- negative_general: 12
- service_repair: 9
- comparison: 3
- positive_general: 3
- expectation: 2
- emotion_only: 2
- other: 2

## 확장 후보 사례 (최대 30건, db≠llm, llm≠other)

- id=1010554 | db=['negative_general'] + llm=`question` → ['negative_general', 'question'] | "Fold Ultra << Isn’t the naming funny?
Not much has changed, but I'm trying to get the price up."
- id=9278 | db=['comparison'] + llm=`positive_general` → ['comparison', 'positive_general'] | "@Trees and Forests 3
Your 11th Street SKT public homepage menu is not bad. If you are a SKT user, I think the conditions are better than Coupang."
- id=1165855 | db=['comparison'] + llm=`positive_general` → ['comparison', 'positive_general'] | "Oh.. I like this place better because of the points. Thank you."
- id=75763 | db=['positive_general'] + llm=`negative_general` → ['positive_general', 'negative_general'] | "Helmo is not popular because it is a Shinsegae gift certificate.
Going offline is a no-brainer"
- id=159409 | db=['comparison'] + llm=`experience` → ['comparison', 'experience'] | "I switched to the Fold 7 after using the iPhone for over 10 years, and Samsung Pay is so convenient???"
- id=15166 | db=['experience'] + llm=`price_purchase` → ['experience', 'price_purchase'] | "Anyway, if the cost for one year is 280,000, it is better to resubscribe to S27 after one year than to use S26 for one more year."
- id=147793 | db=['experience'] + llm=`price_purchase` → ['experience', 'price_purchase'] | "할아버지 폰 바꿔드리려는데 S26은 너무 오버스펙이냐?
통신사 요금제 6개월 유지 조건으로 공짜폰이던데 고민되네...
대리점 갔더니 A시리즈 35만원에 팔아먹길래 내가 직접 찾는 중
- dc official App"
- id=384887 | db=['experience'] + llm=`price_purchase` → ['experience', 'price_purchase'] | "I'm planning to buy iPhone 17 Pro 512GB.
If you change your cell phone once, you can use it for a long time... (It's annoying)
I have been using the iPhone 12 Pro for over 5 years now.
The battery is "
- id=15438 | db=['positive_general'] + llm=`price_purchase` → ['positive_general', 'price_purchase'] | "It has a 60W charging speed and is fully charged in 45 minutes, 20 minutes faster than the Plus. The 26 Regular Plus uses Samsung's Exynos chipset, and the Ultra uses a Snapdragon chipset, so the APs "
- id=979944 | db=['positive_general'] + llm=`negative_general` → ['positive_general', 'negative_general'] | "I am less than impressed with Tuya.  They want to be an ecosystem, just like HomeKit or SmartThings, so you either have to go all in on them or use some bridging solution.  And they charge (a lot) for"
- id=1146001 | db=['experience'] + llm=`expectation` → ['experience', 'expectation'] | "Just use it for another year and then change it when 27u is released."
- id=1046960 | db=['negative_general'] + llm=`question` → ['negative_general', 'question'] | "It seems like I have a bad personality, but since when did I start liking celebrities based on their personalities?"
- id=853757 | db=['experience'] + llm=`question` → ['experience', 'question'] | "> Also, what could one do in advance to know if they're about to purchase such an SSD? You mentioned one affected model. Typically QLC is significantly worse at this than TLC, since the "real" write s"
- id=41636 | db=['negative_general'] + llm=`experience` → ['negative_general', 'experience'] | "3-4일이면 적응해서 다른게 구려보임  - dc App"
- id=122081 | db=['negative_general'] + llm=`question` → ['negative_general', 'question'] | "8.5 삼성 노트 기존처럼
월별로 카테고리 나눠지는게 아니라 그냥 수정날짜 내림차순으로 꽉차있게 못만듬?
월별로 나누니까 스크롤 미치겠네"
- id=279483 | db=['experience'] + llm=`service_repair` → ['experience', 'service_repair'] | "Oh... you missed it, but the delivery of the case keeps getting cancelled. And when I tried it myself, I found one defective pixel, but I'm just putting up with it and using it. I don't want to exchan"
- id=3616 | db=['comparison'] + llm=`experience` → ['comparison', 'experience'] | "In a typical case, it may not be easy to use both phones, but in my case, while staying abroad, I sometimes use local SIM cards and Korean SIM cards separately, and sometimes I use several for busines"
- id=16861 | db=['comparison'] + llm=`question` → ['comparison', 'question'] | "Is it possible to transfer all phone information to the Galaxy Switch?
The Bluetooth connection didn't work at some point, so I was upset and was told that it was a problem with the motherboard and th"
- id=4333 | db=['positive_general'] + llm=`expectation` → ['positive_general', 'expectation'] | "It's coming out next year in the biggest way possible."
- id=147757 | db=['negative_general'] + llm=`expectation` → ['negative_general', 'expectation'] | "It seems like there's only bad news about Galaxy these days...
Flip discontinuation, update related, etc.
Especially in S27, there is only talk about nerfs.
If we nerf and cut costs now, it would be t"
- id=5316 | db=['experience'] + llm=`negative_general` → ['experience', 'negative_general'] | "I have a Purpleplexity Pro subscription, but I haven't used it for a few months.
As for our own model, Sonar has been out for over a year... and its performance is really poor.
So I almost never use i"
- id=513302 | db=['positive_general'] + llm=`expectation` → ['positive_general', 'expectation'] | "michelthemaster wrote: The first to release a 36-39" 16:9 display, click to enlarge... It will probably never happen, I would be there with 39" too. I'm very happy with my 42" but it could be a bit sm"
- id=285042 | db=['experience'] + llm=`emotion_only` → ['experience', 'emotion_only'] | "I'm currently writing 23 Ullari, but I guess it's just my mood."
- id=853866 | db=['comparison'] + llm=`experience` → ['comparison', 'experience'] | "I use the pdf view of Literate Programming projects uploaded to my Kindle Scribe in a similar fashion --- at need I&#x27;ve augmented this by switching to my MacBook for coding and using my Samsung Ga"
- id=768533 | db=['negative_general'] + llm=`comparison` → ['negative_general', 'comparison'] | "Colors and check patterns were slightly different for each brand."
- id=10019 | db=['comparison'] + llm=`experience` → ['comparison', 'experience'] | "Galaxy s24U display quality
After pre-ordering and using it, I discovered an amazing phenomenon... ㅠㅠ
As shown in the photo, in a low-light environment, there is a brightness difference like a diagona"
- id=968953 | db=['positive_general'] + llm=`experience` → ['positive_general', 'experience'] | "They can run Android on them, which is open (I'm typing this comment on a Samsung Galaxy Tab 10.1 w/ keyboard running a CM9 nightly build). Or they can run real Linux distros ... Fedora, Debian, Ubunt"
- id=391842 | db=['comparison'] + llm=`price_purchase` → ['comparison', 'price_purchase'] | "If I'm going to buy it, I'll buy the Watch Ultra. I'm currently using the Watch 5 Pro Bluetooth, but it stutters, so I'm thinking of moving on - dc App"
- id=10512 | db=['comparison'] + llm=`experience` → ['comparison', 'experience'] | "@Jahan
You are correct. I also really prefer new phones, but phones these days are big and slippery.
I'm a bit anxious...
There is a big difference between wearing one of these and not wearing one. St"
- id=10737 | db=['comparison'] + llm=`experience` → ['comparison', 'experience'] | "Well, from what I remember of turning on and touching the iPhone 6s that I sold to Mintit a few days ago before selling it, I couldn't feel any difference in basic performance compared to the 13 Mini."
