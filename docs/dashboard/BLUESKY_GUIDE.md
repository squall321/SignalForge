# Bluesky 키 발급·등록 가이드

2026 년 X.com (Twitter) Basic API 가 월 200 USD 부터 시작하면서, SignalForge 는 무료 대안 1 순위로 **Bluesky** (AT Protocol) 를 채택했다. 이 문서는 가입부터 첫 수집 검증까지 약 5 분 안에 완료할 수 있도록 절차만 추렸다. 비교 분석과 nitter/Mastodon 등 후순위 옵션은 `docs/dashboard/TWITTER_ALTERNATIVES.md` 를 참고.

키가 비어 있는 동안에는 `crawler/platforms/bluesky.py:crawl()` 이 빈 리스트와 경고 로그만 남기고 안전하게 종료하므로, 다른 플랫폼 수집 파이프라인 전체가 죽지 않는다.

## 1. 계정 가입 (약 5 분)

1. https://bsky.app 접속 후 **Sign up** 클릭.
2. 이메일 + 비밀번호 입력 → 핸들 (handle) 선택. 추천 핸들: `signalforge.bsky.social` (사용 가능 여부에 따라 조정).
3. 이메일 인증 메일 (제목: "Bluesky email verification") 의 6 자리 코드 입력 → 가입 완료.
4. 첫 로그인 후 노출되는 추천 피드/모더레이션 안내는 모두 Skip 가능.

> Bluesky 는 별도 초대 코드 없이 즉시 가입 가능 (2024 후반부터 공개). Twitter 와 달리 핸들이 도메인 형태인 점만 유의.

## 2. 앱 패스워드 발급 (필수)

일반 계정 패스워드를 그대로 `.env` 에 넣지 **말 것**. 앱 패스워드는 DM/계정 설정 접근 권한이 없어 사고 시 피해 범위가 작다.

1. 좌측 메뉴 **Settings** → **Privacy and security** → **App Passwords** 클릭.
2. **Add app password** 클릭, 이름은 `signalforge-collector` 권장.
3. 발급된 19 자 패스워드 (`xxxx-xxxx-xxxx-xxxx` 형태) 를 즉시 복사. 모달을 닫으면 다시 볼 수 없다.

## 3. `.env` 등록

`/home/koopark/claude/SignalForge/.env` 의 다음 두 줄을 채운다 (슬롯은 이미 추가되어 있음).

```env
BLUESKY_HANDLE=signalforge.bsky.social
BLUESKY_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

- `BLUESKY_HANDLE` 은 도메인 포함 전체 핸들. 앞에 `@` 를 붙이지 말 것.
- `BLUESKY_PASSWORD` 는 반드시 앱 패스워드. 일반 패스워드는 동작은 하지만 보안 사고 시 전체 계정이 노출된다.
- 두 값에 공백·줄바꿈이 섞이지 않도록 주의 (붙여넣기 후 라인 끝 공백 제거 권장).

## 4. 활성화

`.env` 키 입력 후, DB 의 `platforms` 행에 `is_active=true` 로 마크해야 스케줄러가 호출한다.

```bash
# 한 번에 처리하는 자동 스크립트 (권장)
bash /home/koopark/claude/SignalForge/scripts/activate-channels.sh

# 또는 수동
PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge \
  -c "UPDATE platforms SET is_active = true WHERE code = 'bluesky';"
```

`activate-channels.sh` 는 `.env` 의 Reddit / Bluesky 키 유무를 확인해 채워진 채널만 활성화하고, 첫 수집 1 회를 즉시 실행한다.

## 5. 검증

자동 스크립트가 끝까지 통과했다면 별도 검증은 필요 없다. 수동으로 한 번 더 확인하고 싶다면:

```bash
cd /home/koopark/claude/SignalForge/crawler
python -m scripts.test_bluesky
```

기대 출력:

- 키 미설정: `Bluesky 인증 키 미설정 — skip` 경고 + `collected=0`
- 키 정상: `collected=<N>` (N 은 6 개 쿼리 × 약 5~25 건 = 30~150 사이)

## 6. 단위 테스트

```bash
cd /home/koopark/claude/SignalForge/crawler
python -m pytest tests/test_bluesky.py -v
```

두 케이스 (키 무 → 빈 결과, 키 + mock 세션 → RawVOC 변환) 가 모두 통과해야 한다.

## 7. 트러블슈팅

- **`createSession` 401**: 핸들 또는 패스워드 오타. 핸들에 `@` 가 포함되면 안 되고, 패스워드는 앱 패스워드를 정확히 한 줄로 붙여넣었는지 확인.
- **`searchPosts` 401 반복**: 토큰 캐시가 만료된 후 재발급이 막힌 경우. `crawler/platforms/bluesky.py:_reset_token_cache()` 로 캐시를 비우고 재시도하거나 프로세스를 재시작한다.
- **`429 Too Many Requests`**: Bluesky 의 공식 한도는 5 분에 3,000 req 로 매우 관대하지만, 동시 다중 인스턴스 운영 시 한도를 초과할 수 있다. `crawler/platforms/bluesky.py` 의 `MIN_DELAY` / `MAX_DELAY` 를 늘리거나 `QUERY_TERMS` 를 줄인다.
- **수집량이 0 인데 키는 정상**: 검색어 (`QUERY_TERMS`) 가 Bluesky 사용자 모수와 맞지 않는 가능성. 영어 한 단어 검색어 (예: "Pixel") 로 임시 교체해 확인.

## 8. 스케줄

`crawler/celery_app.py` 의 `crawl-bluesky-2h` 가 자동으로 2 시간마다 실행된다. Reddit, HackerNews 등 글로벌 영문 소셜 플랫폼과 같은 주기로 묶어 트래픽 균형을 맞췄다.

## 9. 메트릭 (예상)

| 채널            | 일 수집 예상 | 비용         | 차단 위험             |
| --------------- | -----------: | ------------ | --------------------- |
| Twitter Basic   |    400 ~ 800 | 월 200 USD   | 낮음 (공식)           |
| Bluesky         |    200 ~ 500 | 0            | 낮음 (공식)           |
| nitter (미구현) |    100 ~ 300 | 0            | 매우 높음 (인스턴스)  |

Bluesky 단독으로 Twitter Basic 의 약 50 % 수준 커버리지. 비용 대비 ROI 최고.
