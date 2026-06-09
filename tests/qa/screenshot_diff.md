# 스크린샷 시각 회귀 (Visual Diff) 가이드

## 목적

대시보드 핵심 페이지의 UI 가 PR 단위로 시각적으로 회귀하지 않는지
픽셀 단위 diff 로 검증.

목표 게이트: **diff ratio < 2 %**

## 권장 스택

- **Playwright** (Python 또는 Node) — 헤드리스 Chromium
- **pixelmatch** (Node) 또는 **PIL + ImageChops** (Python) — 픽셀 diff

본 프로젝트는 backend 가 Python 이므로 Python Playwright 권장.

```bash
pip install playwright pillow
python -m playwright install --with-deps chromium
```

## 대상 페이지

1. `/dashboard` (Overview, 기본 필터)
2. `/dashboard?product=GS25U&period=30d` (Overview, 단일 제품)
3. `/dashboard?product=GS25U&period=30d&tab=top-issues` (Top Issues 탭)
4. `/keyword-track?q=overheating&period=30d` (Keyword Track)

뷰포트는 **1440×900** 고정 (CI 일관성).

## 디렉터리 구조

```
tests/qa/
├── screenshots/
│   ├── baseline/        ← main 브랜치 기준 (git LFS 권장)
│   │   ├── overview.png
│   │   ├── overview-gs25u.png
│   │   └── ...
│   ├── current/         ← PR 빌드 산출물
│   └── diff/            ← pixelmatch 결과
└── screenshot_runner.py
```

## screenshot_runner.py (참고 구현)

```python
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from PIL import Image, ImageChops

BASE_URL = "http://127.0.0.1:5173"
PAGES = {
    "overview": "/dashboard",
    "overview-gs25u": "/dashboard?product=GS25U&period=30d",
    "top-issues": "/dashboard?product=GS25U&period=30d&tab=top-issues",
    "keyword-track": "/keyword-track?q=overheating&period=30d",
}
VIEWPORT = {"width": 1440, "height": 900}
OUT = Path("tests/qa/screenshots/current")
BASELINE = Path("tests/qa/screenshots/baseline")
DIFF_DIR = Path("tests/qa/screenshots/diff")
THRESHOLD = 0.02  # 2 %


async def capture():
    OUT.mkdir(parents=True, exist_ok=True)
    DIFF_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport=VIEWPORT)
        page = await ctx.new_page()
        for name, path in PAGES.items():
            await page.goto(BASE_URL + path, wait_until="networkidle")
            # 차트 애니메이션 안정화
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        await browser.close()


def diff() -> int:
    failed = []
    for png in OUT.glob("*.png"):
        base = BASELINE / png.name
        if not base.exists():
            print(f"[NEW] {png.name} — baseline 없음, 등록 필요")
            continue
        a = Image.open(base).convert("RGB")
        b = Image.open(png).convert("RGB")
        if a.size != b.size:
            print(f"[FAIL] {png.name} — size mismatch")
            failed.append(png.name)
            continue
        diff = ImageChops.difference(a, b)
        bbox = diff.getbbox()
        if bbox is None:
            print(f"[OK]   {png.name} — identical")
            continue
        # 픽셀 차이 비율 계산
        hist = diff.convert("L").histogram()
        diff_pixels = sum(hist[1:])
        total = a.size[0] * a.size[1]
        ratio = diff_pixels / total
        status = "OK" if ratio < THRESHOLD else "FAIL"
        print(f"[{status}] {png.name} — diff {ratio*100:.2f} %")
        if ratio >= THRESHOLD:
            failed.append(png.name)
            diff.save(DIFF_DIR / png.name)
    return 1 if failed else 0


if __name__ == "__main__":
    asyncio.run(capture())
    exit(diff())
```

## CI 통합

`.github/workflows/qa.yml` 에서 다음 단계로 실행:

1. 백엔드 + 프론트 docker compose up
2. `python tests/qa/screenshot_runner.py`
3. exit code ≠ 0 시 `tests/qa/screenshots/diff/` 를 artifact 업로드

## Baseline 업데이트 프로세스

UI 변경 PR 의 경우:

1. PR 작성자가 로컬에서 `python tests/qa/screenshot_runner.py` 실행
2. 의도된 시각 변경이면 `current/` → `baseline/` 으로 복사 후 commit
3. 리뷰어가 PR diff 에서 baseline png 변경 사항 확인

## 알려진 한계

- **시간 의존 컴포넌트** (e.g. "오늘 갱신") → 마스크 영역 처리 필요
- **차트 애니메이션** → `wait_for_timeout(1500)` 이상 권장
- **폰트 렌더링 OS 차이** → CI 는 Ubuntu 22.04 + Noto Sans CJK KR 고정
