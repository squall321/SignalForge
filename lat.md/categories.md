# Categories

VOC 카테고리 분류 코드 표준. 총 12개.
[[nlp#Category Classification]]에서 키워드 매칭으로 자동 분류.
[[data-model#Tables]]의 `categories TEXT[]` 컬럼에 저장.

Source: [[backend/app/seeds/seed_master.py#VOC_CATEGORIES]]
Source: [[crawler/nlp/categorizer.py#CATEGORY_KEYWORDS]]

## Category Codes

12개 표준 카테고리 코드. 각 코드는 `voc_records.categories`에 저장되고 API 필터 파라미터로 사용된다.

| code | 한국어 명 | 대표 키워드 |
| --- | --- | --- |
| `battery` | 배터리/충전 | battery life, drain, charging, 배터리 |
| `camera` | 카메라/촬영 | camera, zoom, night mode, 카메라 |
| `display` | 디스플레이 | screen, brightness, AMOLED, 화면 |
| `performance` | 성능/발열 | lag, heating, fps, 발열, 버벅 |
| `software` | 소프트웨어/UI | OneUI, update, bug, 업데이트 |
| `build_quality` | 내구성/품질 | crack, hinge, durability, 힌지 |
| `price` | 가격/가성비 | expensive, value, 가격, 비싸 |
| `design` | 디자인/형태 | design, color, thin, 디자인 |
| `connectivity` | 연결성 | wifi, bluetooth, 5G, 연결 |
| `ai_features` | AI 기능 | Galaxy AI, Circle to Search, 갤럭시 AI |
| `accessories` | 액세서리/호환 | S Pen, case, charger, 케이스 |
| `comparison` | 경쟁사 비교 | iPhone, Pixel, vs, 아이폰 |

## Scoring Rules

카테고리 자동 분류 시 적용되는 점수 계산 규칙.

- 하나의 VOC에 **복수 카테고리** 태깅 가능 (최대 5개).
- 키워드 매칭 점수 높은 순으로 정렬.
- 매칭 없으면 빈 배열 `[]` 반환.

> **비즈니스 규칙:** 신규 카테고리 추가 시 세 곳을 동시에 업데이트해야 한다.

1. `voc_categories` 테이블 (`seed_master.py`)
2. `CATEGORY_KEYWORDS` 딕셔너리 (`categorizer.py`)
3. `lat.md/categories.md` (이 파일의 위 표)
