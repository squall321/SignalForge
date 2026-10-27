# MCP 응답의 개인정보 기본 가림(SG-11 · SG-18 (가))을 실측 표본 꼴로 고정한다
import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import pii as P  # noqa: E402


def test_imei_labeled_and_luhn_but_not_hashes_or_big_numbers():
    # 실측 꼴(2026-10-27 dev 본문) — 라벨 IMEI · Luhn 맞는 15자리는 가리고, 링크 해시·과장 숫자는 둔다
    assert P.mask_text("• IMEI 1: 353205682980931") == "• IMEI 1: [IMEI]"
    assert "[IMEI]" in P.mask_text("secondo 353884191588656 ok")          # 라벨 없음 · Luhn 맞음
    assert P.mask_text("SAMSUNG 100000000000000%%% NERFED").count("[IMEI]") == 0
    u = "https://preview.redd.it/x.png?auto=webp&s=af945e50f015214054393151c99e6d5b0c46dd1c"
    assert P.mask_text(u) == u
    assert P.mask_text("posts/190251048047090/myrocks") == "posts/190251048047090/myrocks"


def test_model_codes_firmware_prices_dates_survive():
    s = "SM-R530NZAATUR · R60XXS1BYH1 · S938BXXU1AYA4 · 1,299,000원 · 2026-10-27 · One UI 8.0 · 5000mAh"
    assert P.mask_text(s) == s


def test_serial_only_with_a_label():
    out = P.mask_text("Serial number: R58M12ABCDE, serial killer, serial port COM3, S/N: C02XK0XHJGH5")
    assert out == "Serial number: [일련번호], serial killer, serial port COM3, S/N: [일련번호]"
    assert P.mask_text("시리얼 번호 R5CT90ABCDE 이고 일련번호: 12345678") == "시리얼 번호 [일련번호] 이고 일련번호: [일련번호]"


def test_mail_phone_handles():
    out = P.mask_text("010-1234-5678 / 02-555-1234 / +82 10 1234 5678 / foo.bar@gmail.com")
    assert out == "[전화] / [전화] / [전화] / [메일]"
    out = P.mask_text("@john_doe 님 말대로 @SamsungMobile 공지, u/someuser /u/another")
    assert out == "@[계정] 님 말대로 @SamsungMobile 공지, u/[계정] /u/[계정]"


def test_urls_keep_working_where_possible():
    assert P.mask_url("https://twitter.com/foo/status/42?s=20") == "https://x.com/i/web/status/42?s=20"
    assert P.mask_url("https://mastodon.social/@bob/111") == "https://mastodon.social/@[계정]/111"
    assert P.mask_url("https://bsky.app/profile/alice.bsky.social/post/3k") == "https://bsky.app/profile/[계정]/post/3k"
    r = "https://www.reddit.com/r/samsung/comments/abc/xyz/"
    assert P.mask_url(r) == r
    assert "x.com/i/web/status/9" in P.mask_text("see https://x.com/someone/status/9 here")


def test_author_label_is_stable_and_not_the_name():
    a = P.author_label("Foo")
    assert a == P.author_label(" foo ") and a.startswith("작성자#") and "Foo" not in a
    assert P.author_label(None) is None and P.author_label("") == ""


def test_mask_walks_nested_results_by_key():
    out = P.mask({"results": [{"author_name": "ㅇㅇ", "content_original": "메일 a@b.co",
                               "source_url": "https://x.com/a/status/1", "matched": ["@x"]}], "total": 1})
    r = out["results"][0]
    assert r["author_name"].startswith("작성자#") and r["content_original"] == "메일 [메일]"
    assert r["source_url"] == "https://x.com/i/web/status/1" and r["matched"] == ["@x"] and out["total"] == 1


def test_registered_tools_mask_their_output(monkeypatch):
    """등록 길목에서 감싼다 — 도구가 원문을 돌려줘도 나가는 응답은 가려진다."""
    import server

    async def fake_detail(*a, **k):
        return {"total": 1, "truncated": False, "match_mode": "substring", "notes": [],
                "results": [{"id": 1, "author_name": "realname", "content_original": "제 번호 010-9876-5432",
                             "source_url": "https://x.com/realname/status/7"}]}

    monkeypatch.setattr(server, "search_voc_detail", fake_detail)
    out = asyncio.run(server.search_voc("배터리", semantic=False))
    row = out["results"][0]
    assert row["author_name"] != "realname" and "[전화]" in row["content_original"]
    assert "realname" not in str(out)
    tool = server.mcp._tool_manager.get_tool("search_voc")
    assert tool.fn is server.search_voc, "등록된 도구가 가림 래퍼다"
