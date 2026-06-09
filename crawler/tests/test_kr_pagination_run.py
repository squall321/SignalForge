"""KR 사이트 페이지네이션 본런 — 사이트별 안전 임계 적용 단위 테스트 (Harvest 2 트랙 B).

Discovery 가 정해준 사이트별 안전 임계
  clien=25 / dcinside=20 / ppomppu=30 / fmkorea=25 / dogdrip=25
가 env 로 주입됐을 때, 각 platform 모듈의 LIST_PAGES 가 동일 값으로 평가되는지만 검증.

외부 네트워크 / DB 의존 0. 1 케이스.

실행:
  cd crawler && /home/koopark/claude/SignalForge/.venv/bin/python \\
      -m pytest tests/test_kr_pagination_run.py -v
"""
from __future__ import annotations

import importlib
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)


# Harvest 2 (트랙 B) Discovery 권고 임계
SAFE_PAGES = {
    "clien":    25,
    "dcinside": 20,
    "ppomppu":  30,
    "fmkorea":  25,
    "dogdrip":  25,
}


def test_per_site_safe_pages_applied(monkeypatch):
    """각 사이트별 BACKFILL_PAGES env → LIST_PAGES 가 그대로 반영되는지."""
    env_map = {
        "CLIEN_BACKFILL_PAGES":    str(SAFE_PAGES["clien"]),
        "DCINSIDE_BACKFILL_PAGES": str(SAFE_PAGES["dcinside"]),
        "PPOMPPU_BACKFILL_PAGES":  str(SAFE_PAGES["ppomppu"]),
        "FMKOREA_BACKFILL_PAGES":  str(SAFE_PAGES["fmkorea"]),
        "DOGDRIP_BACKFILL_PAGES":  str(SAFE_PAGES["dogdrip"]),
    }
    for k, v in env_map.items():
        monkeypatch.setenv(k, v)

    # module-level 상수라 reload 필요
    site_modules = {
        "clien":    "platforms.clien",
        "dcinside": "platforms.dcinside",
        "ppomppu":  "platforms.ppomppu",
        "fmkorea":  "platforms.fmkorea",
        "dogdrip":  "platforms.dogdrip",
    }
    for site, modname in site_modules.items():
        if modname in sys.modules:
            mod = importlib.reload(sys.modules[modname])
        else:
            mod = importlib.import_module(modname)
        assert mod.LIST_PAGES == SAFE_PAGES[site], \
            f"{site} LIST_PAGES={mod.LIST_PAGES}, 기대={SAFE_PAGES[site]}"
