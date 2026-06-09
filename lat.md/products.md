# Products

Galaxy MobileExperience 대상 제품군 및 코드 체계.

Source: [[backend/app/seeds/seed_master.py#PRODUCTS]]

## Series Codes

6개 시리즈로 분류. `series_code`가 제품 목록 API 필터로 사용된다.

| series_code | 제품군 | 제품 코드 예시 |
| --- | --- | --- |
| `GS` | Galaxy S | `GS25`, `GS25P`, `GS25U` |
| `GZ` | Galaxy Z Fold/Flip | `GZF7`, `GZFL7` |
| `GA` | Galaxy A / FE | `GA56`, `GFE25` |
| `GW` | Galaxy Watch | `GW8`, `GWU` |
| `GB` | Galaxy Buds | `GB3`, `GB3P` |
| `GR` | Galaxy Ring | `GR2` |

## Product Code Convention

`{series_prefix}{model_number}{variant_suffix}`

예시:

- `GS25U` = Galaxy S (`GS`) + 25 + Ultra (`U`)
- `GZF7` = Galaxy Z Fold (`GZF`) + 7
- `GZFL7` = Galaxy Z Flip (`GZFL`) + 7

## Active Products (2026)

현재 `is_active=true`인 제품 12개:
`GS25`, `GS25P`, `GS25U`, `GZF7`, `GZFL7`, `GA56`, `GFE25`, `GW8`, `GWU`, `GB3`, `GB3P`, `GR2`

> **비즈니스 규칙:** 신모델 출시 시 시딩 스크립트에 추가하고, 단종 제품은 `is_active=false`로 변경한다. DB에서 삭제하지 않는다.
