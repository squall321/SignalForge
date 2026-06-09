# Bluesky 키 입력·활성 가이드 (Stage 5C / 2026-06-08)

본 문서는 운영자가 5 ~ 10 분 안에 Bluesky 채널을 키 입력 → 활성 → 1 회 검증 → 자동 스케줄 진입까지 완료할 수 있도록 정리한 **체크리스트형** 가이드다. 깊은 배경 설명과 nitter / Mastodon 비교는 `docs/dashboard/BLUESKY_GUIDE.md` 와 `docs/dashboard/TWITTER_ALTERNATIVES.md` 를 참조한다.

> Stage 5C 현재 상태: `platforms.is_active = true`, `voc_records(bluesky) = 0`, `.env` 의 `BLUESKY_HANDLE` / `BLUESKY_PASSWORD` 가 **빈 문자열**. 이 상태에서 크롤러는 `_has_bluesky_keys()` 분기로 빈 리스트만 반환하고 경고 로그를 남긴 후 종료한다 (graceful skip). 즉, 키가 비어 있는 동안은 **수집량 0 / 다른 채널 정상** 이 보장된 상태다.

## 0. 사전 확인 (30 초)

다음 명령으로 현재 환경이 본 가이드의 가정과 일치하는지 확인한다.

```bash
# 환경변수 슬롯 존재 확인 (값은 비어 있어야 정상 — 이번 가이드의 출발점)
grep -E '^BLUESKY_' /home/koopark/claude/SignalForge/.env

# 플랫폼 활성 여부 (true 가 정상)
PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge -At \
  -c "SELECT code, is_active FROM platforms WHERE code='bluesky';"

# 현재 수집량 (0 이어야 정상)
PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge -At \
  -c "SELECT COUNT(*) FROM voc_records WHERE platform_id=(SELECT id FROM platforms WHERE code='bluesky');"
```

기대 출력: `BLUESKY_HANDLE=`, `BLUESKY_PASSWORD=`, `bluesky|t`, `0`.

## 1. 계정 가입 (약 3 분)

1. https://bsky.app 접속 → **Sign up**.
2. 이메일 + 비밀번호 입력 → 핸들 선택 (권장: `signalforge.bsky.social`, 사용 중이면 임의 변형).
3. 이메일 인증 6 자리 코드 입력 → 가입 완료.
4. 첫 로그인 후 추천 피드 / 모더레이션 화면은 모두 Skip 가능.

## 2. 앱 패스워드 발급 (필수)

운영 패스워드를 그대로 `.env` 에 넣지 **말 것**. 앱 패스워드는 DM·계정 설정 권한이 없어 사고 범위가 좁다.

1. 좌측 메뉴 **Settings** → **Privacy and security** → **App Passwords**.
2. **Add app password** 클릭, 이름은 `signalforge-collector` 권장.
3. 발급된 19 자 패스워드 (`xxxx-xxxx-xxxx-xxxx`) 를 **즉시 복사**. 모달을 닫으면 재확인 불가.

## 3. `.env` 두 줄만 수정 (1 분)

`/home/koopark/claude/SignalForge/.env` 의 **기존 빈 슬롯 2 줄**을 채운다 (라인 추가 X — 이미 존재).

```env
BLUESKY_HANDLE=signalforge.bsky.social
BLUESKY_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

**주의 사항** (실측 다발 오류 3 종)

- `BLUESKY_HANDLE` 앞에 `@` 를 붙이지 말 것 → 401.
- 패스워드 양옆에 공백 / 따옴표가 섞이지 않도록 라인 끝에서 `Enter` 만 한 번.
- VS Code · vim · nano 어느 편집기든 저장 후 `cat -A .env | grep BLUESKY` 로 줄 끝의 `$` 만 확인 (공백 `\ $` 가 보이면 다시 저장).

## 4. up.sh 재가동 (선택 — 기존 worker 가 .env 새 키를 읽도록)

키만 추가한 경우 worker / beat 는 다음 cycle 진입 시 자동으로 새 환경변수를 읽지 못한다. 운영 무중단 방식 2 가지 중 택 1.

방식 A — 빠른 검증 (스크립트만 실행)
```bash
cd /home/koopark/claude/SignalForge/crawler
python -m scripts.test_bluesky
```
이 명령은 `.env` 를 직접 로드하므로 worker 재시작 없이 키 정상 여부를 즉시 확인한다.

방식 B — 자동 스케줄 즉시 반영 (full restart)
```bash
bash /home/koopark/claude/SignalForge/scripts/up.sh
```
- backend / nginx / postgres 는 무중단.
- worker / beat 만 재기동되어 새 `.env` 가 메모리에 로드된다.
- 메모리 모니터: 재기동 직후 `free -m` 으로 `swap_free` 가 200 MB 이하로 떨어지지 않는지 확인 (OOM 안전 정책).

> 우선 방식 A 로 키 정상부터 확인하고, 다음 정규 `up.sh` 사이클에 합쳐 B 로 옮기는 것을 권장한다.

## 5. 활성 검증 (운영자 1 회 호출)

```bash
cd /home/koopark/claude/SignalForge/crawler
python -m scripts.test_bluesky
```

### 5.1 출력 해석표

| 출력 첫 줄 | 다음 줄 패턴 | 의미 | 조치 |
| --- | --- | --- | --- |
| `keys_present=False` | `[guide] .env 의 BLUESKY_HANDLE / BLUESKY_PASSWORD 가 비어 있습니다.` | `.env` 빈 슬롯 | 3 단계 재실행 |
| `keys_present=True` | `collected=N` (`30 ~ 150`) | 정상 수집 | 다음 단계 |
| `keys_present=True` | `[error] Bluesky 호출 실패: HTTPStatusError 401` | 핸들/패스워드 오타 | `.env` 재확인 |
| `keys_present=True` | `[error] Bluesky 호출 실패: HTTPStatusError 429` | 동시 인스턴스 한도 초과 | 5 분 대기 후 재시도 |

### 5.2 DB 반영 (수동 검증)

`test_bluesky.py` 는 RawVOC 객체만 출력하고 **DB 에 적재하지 않는다**. 실제 적재는 worker 가 다음 cycle (`crawl-bluesky-2h`) 에 수행한다. cycle 직후 다음 SQL 로 확인:

```bash
PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge -At \
  -c "SELECT COUNT(*), MAX(published_at) FROM voc_records
      WHERE platform_id=(SELECT id FROM platforms WHERE code='bluesky');"
```

기대: `count > 0` 으로 전이.

## 6. graceful skip 안전성 (이번 라운드 실측)

키 미입력 상태에서 본 가이드 작성 시점에 다음 실측이 있었다 (감사 로그 round=stage5c_sequential / track=T4).

| 항목 | 값 |
| --- | --- |
| `BLUESKY_HANDLE` | 빈 문자열 |
| `BLUESKY_PASSWORD` | 빈 문자열 |
| `platforms.is_active(bluesky)` | `true` |
| `voc_records(bluesky)` 호출 전 | `0` |
| `python -m scripts.test_bluesky` exit code | `0` |
| 첫 줄 출력 | `keys_present=False` |
| 안내 출력 | `[guide] .env 의 BLUESKY_HANDLE / BLUESKY_PASSWORD 가 비어 있습니다.` |
| `voc_records(bluesky)` 호출 후 | `0` (델타 0) |
| 메모리 swap_used | `7797 MB` → `7797 MB` (변동 없음) |

즉 키가 비어 있어도 `is_active=true` 상태 그대로 두는 게 **안전**하다. 사용자가 키를 입력하는 즉시 다음 2 시간 사이클부터 자동으로 수집 시작.

## 7. 스케줄 / 자동화 흐름 요약

| 단계 | 트리거 | 주체 | 결과 |
| --- | --- | --- | --- |
| 1 | 사용자 `.env` 수정 | 사용자 | 빈 슬롯 → 실값 |
| 2 | `python -m scripts.test_bluesky` | 운영자 (1 회) | 키 정상 확인 (`collected=N`) |
| 3 | `up.sh` 다음 사이클 | celery beat | worker 가 새 환경변수 로드 |
| 4 | `crawl-bluesky-2h` 자동 cycle | celery worker | `voc_records` 적재 시작 |
| 5 | 대시보드 `/api/platforms/health` | backend | bluesky `degraded` → `ok` |

## 8. 트러블슈팅 (자주 발생 3 종)

- **`401 createSession`** — 핸들 앞 `@` / 패스워드 공백. `.env` 의 라인 끝을 `cat -A` 로 확인.
- **`401 searchPosts` 반복** — 토큰 캐시 만료 후 재발급 실패. `crawler/platforms/bluesky.py` 의 `_reset_token_cache()` 호출 또는 worker 재시작.
- **수집량 0 (키 정상)** — 검색어가 Bluesky 사용자 모수와 맞지 않는 경우. `QUERY_TERMS` 에 한 단어 영어 검색어 (예: `"Pixel"`) 를 임시 추가.

## 9. 종료 (운영자 체크리스트)

- [ ] `.env` 의 `BLUESKY_HANDLE` / `BLUESKY_PASSWORD` 채워짐
- [ ] `python -m scripts.test_bluesky` → `collected > 0`
- [ ] `voc_records(bluesky)` 다음 cycle 후 `count > 0`
- [ ] 메모리 `swap_free > 200 MB` 유지
- [ ] (선택) `audit/stage5c_sequential.jsonl` 에 track=T4 결과 기록

본 가이드는 `BLUESKY_GUIDE.md` (정적 참조) 와 달리 Stage 5C 운영 시점 기준의 **활성 검증 절차**에 초점이 있다. 가이드 버전 갱신이 필요할 때는 동일 디렉토리에 `BLUESKY_SETUP_<날짜>.md` 형식으로 추가하고, 본 문서는 변경하지 않는다 (감사 추적성 보존).
