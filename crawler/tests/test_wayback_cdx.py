"""Wayback CDX backfill 단위 테스트 — mock CDX 응답.

외부 네트워크 호출 없이 _cdx_query / _parse_clien_snapshot 검증.

실행:
  cd crawler && python -m pytest tests/test_wayback_cdx.py -v
  cd crawler && python tests/test_wayback_cdx.py
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import wayback_kr_backfill as wb  # noqa: E402


# ---------- 1) CDX 응답 정상 파싱 ----------
def test_cdx_query_parses_rows():
    """CDX header + 2 row → (timestamp, original_url) 2개 반환."""
    fake_json = [
        ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["net,clien)/service/board/cm_andro/10849162",
         "20201027210523",
         "https://www.clien.net/service/board/cm_andro/10849162",
         "text/html", "200", "ABC", "47651"],
        ["net,clien)/service/board/cm_andro/11066808",
         "20200815014705",
         "https://www.clien.net/service/board/cm_andro/11066808",
         "text/html", "200", "DEF", "16885"],
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value=fake_json)
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    rows = asyncio.run(
        wb._cdx_query(mock_client, "clien.net/service/board/cm_andro/*", 2020, 100)
    )
    assert len(rows) == 2, f"기대 2, 실제 {len(rows)}"
    assert rows[0] == (
        "20201027210523",
        "https://www.clien.net/service/board/cm_andro/10849162",
    )
    assert rows[1][0] == "20200815014705"
    print("  [PASS] CDX 응답 → (ts, url) tuple 2개 파싱")


# ---------- 2) CDX 빈 응답 (header 만) ----------
def test_cdx_query_empty():
    """결과 0건 (header 만) → 빈 리스트."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value=[["urlkey", "timestamp", "original"]])
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    rows = asyncio.run(
        wb._cdx_query(mock_client, "nomatch.example.com", 2020, 100)
    )
    assert rows == [], f"빈 응답 기대, 실제 {rows}"
    print("  [PASS] CDX header-only 응답 → []")


# ---------- 3) clien snapshot HTML parser ----------
def test_parse_clien_snapshot_extracts_body_and_comments():
    """post_content + comment_row 2개 → body_voc + 2 comment voc."""
    html = """
    <html><body>
      <div class="post_subject">갤럭시 S20 사용기</div>
      <div class="post_author">2020-08-15 14:32:01 | 125.x.x.x</div>
      <div class="post_content">3개월 써본 후기입니다.
      카메라가 정말 좋네요. 추천합니다.</div>
      <div class="comment_row" data-comment-sn="c1">
        <div class="comment_view">저도 S20 사용중인데 만족합니다.</div>
      </div>
      <div class="comment_row" data-comment-sn="c2">
        <div class="comment_view">배터리는 어떤가요? 좀 더 알려주세요.</div>
      </div>
      <div class="comment_row blocked" data-comment-sn="c3">
        <div class="comment_view">차단된 댓글</div>
      </div>
    </body></html>
    """
    body_voc, comments = wb._parse_clien_snapshot(
        html, "https://www.clien.net/service/board/cm_andro/12345"
    )
    assert "갤럭시 S20" in body_voc.content
    assert "카메라가 정말 좋네요" in body_voc.content
    assert body_voc.country_code == "KR"
    assert body_voc.source_url.endswith("/12345")
    # 작성일 추출 (KST 2020-08-15 14:32 → UTC 05:32)
    assert body_voc.published_at is not None, "published_at 추출 실패"
    assert body_voc.published_at.year == 2020 and body_voc.published_at.month == 8
    assert body_voc.published_at.hour == 5, f"KST→UTC 변환 오류: hour={body_voc.published_at.hour}"
    # blocked 댓글은 제외
    assert len(comments) == 2, f"comment 2 기대, 실제 {len(comments)}"
    assert "S20 사용중" in comments[0].content
    assert "배터리는" in comments[1].content
    # external_id 결정적 (md5 prefix 16)
    assert len(body_voc.external_id) == 16
    print("  [PASS] clien snapshot HTML → body + 2 comment + 작성일 KST→UTC")


# ---------- 4) seed CSV loader (CDX fallback) ----------
def test_load_seed_urls(tmp_path=None):
    """seed CSV → (timestamp, url) tuple, 주석/잘못된 라인 skip."""
    import tempfile
    csv = (
        "# 주석 라인\n"
        "\n"
        "20200815014705,https://www.clien.net/service/board/cm_andro/11066808\n"
        "20201027210523,https://www.clien.net/service/board/cm_andro/10849162\n"
        "잘못된라인without_comma\n"
        "  ABC,not_digits  \n"   # timestamp 가 digit 아님 → skip
        " 20240119112646 , https://www.clien.net/service/board/cm_andro/10849162 \n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write(csv)
        path = f.name
    out = wb._load_seed_urls(path)
    assert len(out) == 3, f"기대 3, 실제 {len(out)}"
    assert out[0] == ("20200815014705", "https://www.clien.net/service/board/cm_andro/11066808")
    assert out[2][0] == "20240119112646"
    print("  [PASS] seed CSV → 3 valid (주석 + 잘못된 라인 skip)")


# ---------- 5) WB URL 구성 ----------
def test_wayback_url_builder():
    url = wb._build_wayback_url(
        "20201027210523",
        "https://www.clien.net/service/board/cm_andro/10849162",
    )
    assert url == (
        "http://web.archive.org/web/20201027210523/"
        "https://www.clien.net/service/board/cm_andro/10849162"
    )
    print("  [PASS] Wayback URL 조합")


if __name__ == "__main__":
    tests = [
        test_cdx_query_parses_rows,
        test_cdx_query_empty,
        test_parse_clien_snapshot_extracts_body_and_comments,
        test_load_seed_urls,
        test_wayback_url_builder,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n결과: {len(tests) - failed}/{len(tests)} 통과")
    sys.exit(0 if failed == 0 else 1)
