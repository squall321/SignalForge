# Twitter / X.com 무료 대안 비교 + Bluesky 운영 가이드

2026 년 들어 X.com (구 Twitter) API 는 Basic tier 도 월 200 USD 부터 시작하며 검색 호출 단가가 비대화 SignalForge 의 VOC 수집 ROI 와 맞지 않게 되었다. 본 문서는 무료 또는 매우 저렴한 5 개의 대안을 비교하고, 1 순위로 채택한 **Bluesky** 의 키 발급·등록 절차를 정리한다.

## 5 가지 옵션 비교

| 옵션 | API/접근 방식 | 비용 | 안정성 | Galaxy/Pixel 토픽량 | 인증 난이도 | SignalForge 권장 |
|------|---------------|------|--------|---------------------|-------------|------------------|
| A. nitter 미러 | 비공식 RSS / HTML 스크레이핑 | 무료 | 낮음 (인스턴스 자주 다운, 차단) | 높음 (Twitter 미러) | 없음 (익명) | 백업 (2 순위) |
| B. Threads (Meta) | 공식 Graph API, OAuth | 무료 (제한적) | 보통 (정책 자주 변경) | 낮음 (Galaxy 토픽 적음) | 보통 (앱 심사) | 미채택 |
| C. Mastodon | ActivityPub federation 검색 | 무료 | 보통 (instance 마다 검색 범위 다름) | 낮음~보통 | 쉬움 (toot.io 토큰) | 후순위 |
| D. Bluesky | AT Protocol XRPC, JWT | 무료 (계정 1 개) | 높음 (공식 standard, rate limit 관대) | 보통 (2026 증가세) | 쉬움 (앱 패스워드) | **1 순위 (채택)** |
| E. 한국 X 트렌드 / Naver 데이터랩 | 키워드 트렌드 그래프만 (raw post 없음) | 무료 | 높음 (집계 데이터) | 보통 | 없음/쉬움 | 보완 (트렌드 시계열 전용) |

### 옵션별 한 줄 요약
- **A. nitter** — 무인증·무료지만 인스턴스 차단/다운이 잦아 SLA 없는 백업으로만 유용.
- **B. Threads** — Meta 정책 의존도 높고 Galaxy 토픽 자체가 부족.
- **C. Mastodon** — federation 검색이 instance 별로 결과가 달라 노이즈 큼.
- **D. Bluesky** — 무료·공식·안정. AT Protocol 표준으로 응답 스키마가 long-term stable.
- **E. Naver 데이터랩** — 텍스트가 아닌 trend index 만 제공하므로 VOC 분석엔 부족.

## Bluesky 운영 가이드 (1 순위)

### 1. 앱 패스워드 발급
1. https://bsky.app 에 가입 (이메일 인증 + 핸들 선택, 예: `signalforge.bsky.social`).
2. Settings → Privacy and security → **App Passwords** 클릭.
3. **Add app password** 클릭, 이름은 `signalforge-collector` 권장.
4. 발급된 19 자 짧은 패스워드를 즉시 복사 (재확인 불가).

> 일반 계정 패스워드 대신 반드시 앱 패스워드를 사용하세요. 앱 패스워드는 DM 접근 권한이 없어 사고 시 피해 범위가 작습니다.

### 2. `.env` 등록

`/home/koopark/claude/SignalForge/.env` 에 다음 두 줄을 채웁니다.

```env
BLUESKY_HANDLE=signalforge.bsky.social
BLUESKY_PASSWORD=<19자 앱 패스워드>
```

키가 비어 있으면 `crawler/platforms/bluesky.py:crawl()` 은 빈 리스트와 경고 로그만 남기고 안전하게 종료하므로 파이프라인 전체가 죽지 않습니다.

### 3. 마이그레이션 + 활성화

```bash
# 1) 마이그레이션 적용 (platforms 신규 row 'bluesky' INSERT)
cd /home/koopark/claude/SignalForge/backend
alembic upgrade head

# 2) 활성화 (.env 키 채운 후)
PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge \
  -c "UPDATE platforms SET is_active = true WHERE code = 'bluesky';"

# 3) celery 재시작 (worker + beat)
# 이미 실행 중인 프로세스 재시작 (환경에 맞게 조정)
pkill -HUP -f 'celery.*beat' || true
pkill -HUP -f 'celery.*worker' || true
```

### 4. 검증

```bash
cd /home/koopark/claude/SignalForge/crawler
python -c "
import asyncio
from platforms.bluesky import BlueskyCrawler
print(len(asyncio.run(BlueskyCrawler().crawl())))
"
```

기대값: 키 입력·활성화 후 첫 실행은 6 개 쿼리 × 약 5~25 건 = **30~150 건** 사이.

### 5. 스케줄

`crawler/celery_app.py` 의 `crawl-bluesky-2h` 가 자동으로 2 시간마다 실행됩니다. 다른 글로벌 영문 소셜 플랫폼(Reddit, HN)과 같은 주기로 묶어 트래픽 균형을 잡았습니다.

## nitter 백업 (구현 보류)

nitter 인스턴스 가용성이 안정화되면 보조 수집 채널로 추가합니다. 현재는 다음 인스턴스가 가동 중 (2026-06-03 기준 — 자주 바뀜):
- https://nitter.privacydev.net
- https://nitter.poast.org

RSS 엔드포인트 패턴: `https://<host>/search/rss?f=tweets&q=Galaxy+S25`

미구현 사유: 인스턴스 평균 가용 기간이 3 개월 이내라 운영 비용 대비 효익이 낮음. 추후 Bluesky 만으로 글로벌 SNS VOC 부족이 발견되면 1 day 작업으로 추가.

## 메트릭 (예상)

| 채널 | 일 수집 예상 | 비용 | 차단 위험 |
|------|---------------|------|-----------|
| Twitter Basic API | 400 ~ 800 건 | 월 200 USD | 낮음 (공식) |
| Bluesky | 200 ~ 500 건 | 0 | 낮음 (공식) |
| nitter | 100 ~ 300 건 | 0 | 매우 높음 (인스턴스 차단) |

Bluesky 단독으로 Twitter Basic 의 약 50 % 수준 커버. 비용 대비 ROI 최고.
