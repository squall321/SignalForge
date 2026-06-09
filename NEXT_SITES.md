# SignalForge — 신규 사이트 후보 풀 (미래 계획)

> 1~11차로 충분히 확장 (목표 60+ 활성 / 24+ 지역).
> 추가 수집보다 **자동 리포팅**이 다음 우선순위.
> 이 문서는 향후 라운드가 필요할 때 즉시 fan-out 할 수 있는 후보 풀.

## A. 실패/차단 재시도 (재진단 후 우회 전략)

| 사이트 | 지역 | 실패 라운드 | 우회 전략 |
|---|---|---|---|
| kaskus | 🇮🇩 ID | 8차 | IP 차단 자연해제(24-48h) + MIN_DELAY≥2.5s + UA 회전, 실패 시 ScrapingBee/Residential 프록시 |
| 91mobiles | 🇮🇳 IN | 4차 | Akamai TLS 정밀 분석 + curl-impersonate / cloudscraper |
| computerbase | 🇩🇪 DE | 5차 | 빈 응답 정밀 진단 (Cloudflare Turnstile 가능), Playwright stealth 검토 |
| 4pda | 🇷🇺 RU | 5차 | RU 지역 차단 가능, VPN/프록시 필요 |
| AnandTech | 🌏 GLOBAL | 3차 | 사이트 운영 종료 의심, archive.org 백업 검토 |
| areamobile | 🇩🇪 DE | 10차 | **폐쇄(301 PCGH)**, 대체로 inside-handy 진행 중 |

## B. 미진입 시장 (Samsung 점유율·인구 가중)

### 동남아 (Samsung 핵심 시장)
- 🇮🇩 ID: **detik.com/inet**, jagatreview.com (kaskus 차단 회피 1순위)
- 🇵🇭 PH: **pinoyexchange.com** (포럼 활성), gizmodo.com.ph
- 🇲🇾 MY: soyacincau (lowyat 보강)
- 🇻🇳 VN: **genk.vn** (tinhte 보강, 더 큰 미디어)
- 🇹🇭 TH: droidsans.com (sanook 보강)

### 인도 (Galaxy M/F/A 핵심)
- **beebom.com** (대형, WordPress)
- **smartprix.com** (가격·리뷰)
- fonearena.com (모바일 전문)

### 중남미
- 🇨🇱 CL: **fayerwayer.com** (스페인어 큰 미디어)
- 🇦🇷 AR: infobae.com/tecno
- 🇨🇴 CO: enter.co
- 🇧🇷 BR 보강: olhardigital.com.br

### 중동/북아프리카
- 🇸🇦 SA: aitnews.com (arageek 보강)
- 🇦🇪 UAE: tech-wd.com
- 🇮🇷 IR: digiato.com (이란 IT 매체)
- 🇹🇷 TR 보강: turkiyebilisim, mobil.shiftdelete 외 사이트

### 북유럽
- 🇩🇰 DK: mobilsiden.dk
- 🇳🇴 NO: tek.no, dinside.no
- 🇫🇮 FI: io-tech.fi, muropaketti.com

### 서유럽 보강
- 🇫🇷 FR 보강: numerama.com (frandroid 외)
- 🇮🇹 IT 보강: tomshw.it (hwupgrade 외)
- 🇪🇸 ES 보강: andro4all.com
- 🇧🇪 BE: tweakers.be (tweakers.net 보완)
- 🇮🇱 IL: geektime.co.il

### 아프리카
- 🇿🇦 ZA 보강: techcentral.co.za
- 🇰🇪 KE 보강: techweez
- 🇳🇬 NG 보강: gadgets-africa.com

### 동아시아 보강
- 🇨🇳 CN 보강: 36kr.com, ifanr.com (Weibo는 정치적 민감)
- 🇯🇵 JP 보강: k-tai-watch.impress.co.jp, juggly.cn
- 🇰🇷 KR 보강: 82cook (다른 demographic), MLBPark IT 게시판

### 오세아니아
- 🇳🇿 NZ: nzherald.co.nz/lifestyle
- 🇦🇺 AU 보강: whirlpool.net.au (포럼 활성)

## C. 글로벌 메이저 영문 매체
- techcrunch.com (기사 + 댓글)
- mashable.com
- digitaltrends.com
- cnet.com
- gizmodo.com (글로벌)

## D. 자격증명 필요 (확보 시 즉시 가능)
- **Reddit** 서브레딧: r/samsung, r/galaxyS25, r/galaxyfold, r/galaxywatch, r/galaxybuds, r/AndroidQuestions → CLIENT_ID/SECRET 필요
- **Twitter/X**: @SamsungMobile, hashtags → 로그인 필요
- **Naver Cafe**: 삼성모바일 공식 카페 (clubid=28543326) → 네이버 로그인
- **Amazon Reviews**: 봇 차단, 공식 API 또는 서드파티 데이터

## E. 전문 카테고리 보강
- 카메라: fstoppers.com (DPReview 보완)
- 게이밍: gamebyte, theshelfnetwork
- 헬스 (Watch/Ring): wareable.com

## 후속 라운드 전략

각 라운드 5개씩 배치, 라운드별 주제 통일:
- 12차: 실패 재시도 (kaskus/91mobiles/computerbase/4pda 등)
- 13차: 동남아 보강 (detik/pinoyexchange/jagatreview/genk/soyacincau)
- 14차: 인도 (beebom/smartprix/fonearena)
- 15차: 중남미 (fayerwayer/infobae/enter)
- 16차: 북유럽 (mobilsiden/tek.no/io-tech)
- 17차: 자격증명 채널 확보 후 Reddit/Twitter 본격 가동
