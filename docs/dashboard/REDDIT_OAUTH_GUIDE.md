# Reddit OAuth 키 발급·등록 가이드

2026 년 봇 차단 강화 이후 SignalForge 의 Reddit 수집은 공식 OAuth 토큰 없이 동작하지 않습니다. `old.reddit.com` 의 `.json` 엔드포인트는 더 이상 익명 접근을 허용하지 않으므로 (`403 Blocked`), 이 문서를 따라 5 분 안에 키를 발급받아 `.env` 에 등록하면 수집이 즉시 재개됩니다. 키가 없으면 `crawler/platforms/reddit.py:crawl()` 은 빈 리스트와 경고 로그만 남기고 안전하게 종료되므로 파이프라인 전체가 죽지 않습니다.

## 1. 앱 생성

1. https://www.reddit.com/prefs/apps 에 로그인 후 접속.
2. 페이지 하단의 **create app...** 또는 **create another app...** 클릭.
3. 다음과 같이 입력합니다.
   - **name**: `SignalForge`
   - **app type**: `script` (개인 사용 용도, app-only client_credentials 가능)
   - **description**: `SignalForge VOC monitoring`
   - **about url**: 비워 둠
   - **redirect uri**: `http://localhost` (script 타입은 실제로 사용하지 않지만 필수)
4. **create app** 클릭.

## 1.5 Developer Platform 등록 폼 (2024 이후 필수)

2024 년 봇/AI 대량 수집 차단 정책 강화 이후, Reddit 는 신규 앱 생성 직후 또는 첫 토큰 요청 시 **Developer Platform** 등록 폼 통과를 요구합니다. 폼을 통과하지 않으면 `client_id` / `client_secret` 은 발급되지만 토큰 요청 시 401/403 으로 막힙니다.

폼 진입 경로 (2026-06 기준):
- 앱 카드 우측 상단의 **Developer Platform** 또는 **About this app** 배너 클릭, 혹은
- https://www.reddit.com/r/redditdev 가이드 페이지 하단의 "Submit your app" 링크.

폼 항목과 SignalForge 표준 답안:

| 항목 | 추천 답변 |
|------|-----------|
| App name | `SignalForge` |
| App description | `Internal VOC (voice of customer) monitoring for Galaxy/iPhone/Pixel discussion threads. Read-only listing fetch, no posting, no DMs.` |
| Will your app train AI models? | **No** (반드시 No. Yes 답할 경우 별도 commercial license 협의 단계로 넘어가 통과까지 평균 2 주 소요) |
| Will your app post or comment on behalf of users? | **No** |
| Will your app collect/store user data? | **Only public post metadata (id, score, body) for trend analysis. No PII, no DMs, no private subreddits.** |
| Expected QPS | `< 1` (분당 10~30 req 수준이라고 명시) |
| Contact email | 본인 이메일 |
| OAuth scopes requested | `read` 만 체크 (write / identity / history 모두 미체크) |

> **AI training: No** 가 가장 중요합니다. 폼 통과 후에도 약관 위반 (model training, scraping at scale, redistribution) 시 즉시 키가 회수됩니다.

폼 제출 후:
- 즉시 통과 케이스: 1 분 내 "Approved" 상태 표시 + 키 활성.
- 검토 케이스: 24 ~ 72 시간 내 이메일 통보. 추가 질문 (예: "어떤 subreddit 을 모니터링하는가?") 이 오면 `r/samsung`, `r/Android`, `r/apple`, `r/GalaxyS25` 등 구체 리스트로 답변.

폼 통과 확인은 https://www.reddit.com/prefs/apps 의 앱 카드 라벨이 **active** 인지 (또는 회색 **pending** 인지) 로 판단합니다.

## 2. 키 확인

생성 후 앱 카드에 두 값이 노출됩니다.

- `client_id`: 앱 이름 바로 아래, "personal use script" 라벨 옆의 14 자 짧은 문자열.
- `client_secret`: `secret` 라벨 옆의 27 자 문자열. **edit** 을 눌러야 전체가 보일 수 있습니다.

## 3. `.env` 등록

`/home/koopark/claude/SignalForge/.env` 의 다음 슬롯을 채웁니다.

```env
REDDIT_CLIENT_ID=<14자 client_id>
REDDIT_CLIENT_SECRET=<27자 client_secret>
REDDIT_USER_AGENT=SignalForge/1.0 by /u/<your-reddit-username>
# 아래 둘은 읽기 전용 수집이면 비워둡니다.
REDDIT_USERNAME=
REDDIT_PASSWORD=
```

`REDDIT_USER_AGENT` 는 Reddit 가이드에 따라 반드시 `<프로젝트>/<버전> by /u/<계정>` 형식이어야 하며, 다른 유저의 UA 를 복사하면 차단 대상이 됩니다.

## 4. 활성화 + 재시작

```bash
# crawler 및 celery 재시작 (PID 는 환경에 맞게 조정)
sudo systemctl restart signalforge-celery-worker signalforge-celery-beat

# DB 에서 platforms.is_active 를 true 로
psql postgresql://signalforge:signalforge_pass@127.0.0.1:5434/signalforge \
  -c "UPDATE platforms SET is_active = true WHERE code = 'reddit';"
```

## 5. 검증

다음 명령으로 토큰이 발급되고 첫 listing 이 수집되는지 즉시 확인할 수 있습니다.

```bash
cd /home/koopark/claude/SignalForge/crawler
python -c "
import asyncio
from platforms.reddit import RedditCrawler
print(len(asyncio.run(RedditCrawler().crawl())))
"
```

키가 올바르면 100~ 200 건 수준의 RawVOC 가 수집되고, 키가 비어 있으면 `Reddit OAuth 키 미설정 — skip` 경고 후 `0` 이 출력됩니다.

## 6. 단위 테스트

```bash
cd /home/koopark/claude/SignalForge/crawler
pytest tests/test_reddit_oauth.py -v
```

세 케이스 (키 무, 키 + mock 토큰, 401 후 자동 갱신) 가 모두 통과해야 합니다.

## 7. 트러블슈팅

- `401 Unauthorized` 가 매 요청마다 반복되면 `client_id` / `client_secret` 입력을 다시 확인하세요. secret 은 공백·줄바꿈이 섞이지 않도록 한 줄로 붙여넣습니다.
- `429 Too Many Requests` 가 뜨면 `crawler/platforms/reddit.py` 의 `MIN_DELAY` / `MAX_DELAY` 를 늘리거나 `SUBREDDITS` 목록을 축소하세요. app-only 토큰의 공식 한도는 분당 60 요청입니다.
- 토큰은 1 시간 유효하며 본 구현은 60 초 마진을 두고 자동 재발급합니다 (`_TOKEN_SAFETY_MARGIN`).
