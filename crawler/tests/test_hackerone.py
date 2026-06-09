"""HackerOne 크롤러 단위 테스트 — 네트워크 없이 파서·필터 검증.

실행: cd crawler && python -m pytest tests/test_hackerone.py -v
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.hackerone import (  # noqa: E402
    HackerOneCrawler,
    QUERIES,
    PAGE_SIZE,
    MAX_PAGES,
    HACKERONE_API,
    _parse_iso,
    _build_content,
)


def test_module_constants():
    assert HACKERONE_API.startswith("https://api.hackerone.com/")
    assert len(QUERIES) >= 3
    # Samsung / Galaxy / Android / Pixel 최소 셋
    assert any("samsung" in q for q in QUERIES)
    assert any("pixel" in q for q in QUERIES)
    assert PAGE_SIZE >= 10 and PAGE_SIZE <= 100
    assert MAX_PAGES >= 1
    # 보수 delay (rate limit 미공개 → 5초 1요청)
    assert HackerOneCrawler.MIN_DELAY >= 3.0


def test_parse_iso_z_suffix():
    dt = _parse_iso("2025-11-14T19:25:28.285Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2025 and dt.month == 11


def test_parse_iso_none_and_garbage():
    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso("not-a-date") is None


def test_build_content_includes_program_and_cve():
    body = _build_content(
        title="Galaxy hinge sensor bypass",
        program="Samsung Mobile",
        severity="High",
        summary="Detailed reproduction steps...",
        cve_ids=["CVE-2025-0001", "CVE-2025-0002"],
    )
    assert "Galaxy hinge sensor bypass" in body
    assert "Samsung Mobile" in body
    assert "High" in body
    assert "CVE-2025-0001" in body
    assert "Detailed reproduction" in body


def test_build_content_minimal():
    body = _build_content(
        title="Title only",
        program="",
        severity=None,
        summary="",
        cve_ids=[],
    )
    assert body.strip().startswith("Title only")


def test_to_rawvoc_mx_filter_pass():
    crawler = HackerOneCrawler.__new__(HackerOneCrawler)
    item = {
        "id": "12345",
        "attributes": {
            "title": "Samsung Galaxy S25 lockscreen bypass",
            "url": "https://hackerone.com/reports/12345",
            "disclosed_at": "2025-11-14T19:25:28.285Z",
            "severity_rating": "High",
            "cve_ids": ["CVE-2025-9999"],
            "vulnerability_information": "Tap-to-bypass on the One UI lockscreen.",
        },
        "relationships": {
            "program": {"data": {"attributes": {"name": "Samsung Mobile", "handle": "samsung"}}},
            "report_generated_content": {"data": {"attributes": {"hacktivity_summary": ""}}},
        },
    }
    voc = crawler._to_rawvoc(item)
    assert voc is not None
    assert "Galaxy" in voc.content
    assert voc.source_url == "https://hackerone.com/reports/12345"
    assert voc.meta["report_id"] == "12345"
    assert voc.meta["program"] == "Samsung Mobile"
    assert voc.meta["severity"] == "High"
    assert "CVE-2025-9999" in voc.meta["cve_ids"]
    assert voc.published_at is not None
    # external_id 안정성 — md5 16 자
    assert len(voc.external_id) == 16


def test_to_rawvoc_mx_filter_reject_unrelated():
    crawler = HackerOneCrawler.__new__(HackerOneCrawler)
    item = {
        "id": "67890",
        "attributes": {
            "title": "Random web app XSS on marketing site",
            "url": "https://hackerone.com/reports/67890",
            "disclosed_at": "2024-01-01T00:00:00Z",
            "severity_rating": "Low",
            "cve_ids": [],
            "vulnerability_information": "Reflected XSS in contact form.",
        },
        "relationships": {
            "program": {"data": {"attributes": {"name": "Acme SaaS"}}},
        },
    }
    # MX 키워드 (samsung/galaxy/android/iphone/pixel/oneui...) 없음 → 거절
    assert crawler._to_rawvoc(item) is None


def test_to_rawvoc_missing_required_fields_returns_none():
    crawler = HackerOneCrawler.__new__(HackerOneCrawler)
    # title 없음
    assert crawler._to_rawvoc({"id": "1", "attributes": {"url": "x"}}) is None
    # url 없음
    assert crawler._to_rawvoc({"id": "2", "attributes": {"title": "Galaxy issue"}}) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
