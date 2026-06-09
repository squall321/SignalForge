"""MX 카테고리 통합 키워드 필터 — Samsung + 경쟁사 + 일반 폰/웨어러블."""
from __future__ import annotations
import re
from typing import Optional

# Samsung + Apple + Google Pixel + 중국 OEM + 일반 폰/웨어러블 + 한국어/영어
_MX_PATTERN = re.compile(
    r"(samsung|galaxy|갤럭시|삼성|폴드|플립|oneui|one ui|exynos|tab s|"
    r"galaxy buds|buds[ 0-9]|"
    r"apple|iphone|아이폰|airpods|에어팟|애플|ipad|아이패드|"
    r"pixel|픽셀|"
    r"xiaomi|샤오미|huawei|화웨이|oppo|오포|vivo|비보|"
    r"oneplus|원플러스|honor|아너|redmi|레드미|poco|포코|"
    r"smartphone|스마트폰|핸드폰|휴대폰|foldable|폴더블|"
    r"smartwatch|스마트워치|wearable|웨어러블)",
    re.IGNORECASE,
)


def is_mx_relevant(content: Optional[str]) -> bool:
    """본문에 MX 통합 키워드 1개 이상 포함되면 True."""
    if not content:
        return False
    return bool(_MX_PATTERN.search(content))
