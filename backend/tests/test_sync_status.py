"""
/api/v1/_internal/sync-status endpoint 단위 테스트
  (Stage 4.5 auto_sync Y5 트랙 — 양방향 Drive 동기화 상태).

검증 (외부 의존 0 — tmpdir audit JSONL + LATEST.json 만 사용):

  1. audit/manifest 둘 다 없음 → audit_available=false, send/recv.available=false,
                                  latest_manifest.available=false (graceful).
  2. audit 에 sync-to-drive end + sync-from-drive end + dry-run 섞임
     → send/recv.available=true, last_event 마지막 줄, last_success=end,
       counters_24h.runs/ok 정확, dry_runs 카운트.
  3. LATEST.json 있을 때 manifest 의 키가 그대로 노출 + mtime 포함.
  4. 잘못된 JSON 라인 섞여도 graceful skip — 정상 라인만 카운트.

실행:
    cd backend && .venv/bin/python tests/test_sync_status.py
    cd backend && .venv/bin/pytest tests/test_sync_status.py -v
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_audit(path: str, events: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_sync_status_missing_files():
    """audit / manifest 둘 다 없을 때 graceful."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    with tempfile.TemporaryDirectory() as tmp:
        audit_path = os.path.join(tmp, "portal_deploy.jsonl")
        latest_path = os.path.join(tmp, "LATEST.json")
        env_patch = {
            "AUTO_SYNC_AUDIT_FILE": audit_path,
            "AUTO_SYNC_LATEST_FILE": latest_path,
        }
        with mock.patch.dict(os.environ, env_patch, clear=False):
            r = client.get("/api/v1/_internal/sync-status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["audit_available"] is False, body
        assert body["send"]["available"] is False, body
        assert body["recv"]["available"] is False, body
        assert body["latest_manifest"]["available"] is False, body
        assert body["summary"]["send_ok_24h"] is False, body
        assert body["summary"]["recv_ok_24h"] is False, body
        assert body["summary"]["latest_present"] is False, body
        print("[ok] sync-status: missing files → all available=false graceful")


def test_sync_status_with_events_and_manifest():
    """두 측 events + LATEST.json 정상 시 요약 및 카운터 정확."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    with tempfile.TemporaryDirectory() as tmp:
        audit_path = os.path.join(tmp, "portal_deploy.jsonl")
        latest_path = os.path.join(tmp, "LATEST.json")
        now = datetime.now(timezone.utc)
        # 1h 전 송신 dry-run (run_id=A).
        send_dry_run_a = [
            {"ts": _iso(now - timedelta(hours=1, minutes=1)), "round": "auto_sync",
             "track": "Y3", "run_id": "A", "script": "sync-to-drive",
             "dry_run": 1, "event": "start"},
            {"ts": _iso(now - timedelta(hours=1)), "round": "auto_sync",
             "track": "Y3", "run_id": "A", "script": "sync-to-drive",
             "dry_run": 1, "event": "sif_dryrun"},
            {"ts": _iso(now - timedelta(hours=1)), "round": "auto_sync",
             "track": "Y3", "run_id": "A", "script": "sync-to-drive",
             "dry_run": 1, "event": "end"},
        ]
        # 30m 전 송신 실 1회 (run_id=B).
        send_real_b = [
            {"ts": _iso(now - timedelta(minutes=32)), "round": "auto_sync",
             "track": "Y3", "run_id": "B", "script": "sync-to-drive",
             "dry_run": 0, "event": "start"},
            {"ts": _iso(now - timedelta(minutes=31)), "round": "auto_sync",
             "track": "Y3", "run_id": "B", "script": "sync-to-drive",
             "dry_run": 0, "event": "sif_ok", "count": 5, "tag": "sif-20260607-231406Z"},
            {"ts": _iso(now - timedelta(minutes=30)), "round": "auto_sync",
             "track": "Y3", "run_id": "B", "script": "sync-to-drive",
             "dry_run": 0, "event": "end"},
        ]
        # 15m 전 수신 실 1회 (run_id=C).
        recv_real_c = [
            {"ts": _iso(now - timedelta(minutes=16)), "round": "auto_sync",
             "track": "Y4", "run_id": "C", "script": "sync-from-drive",
             "dry_run": 0, "event": "start"},
            {"ts": _iso(now - timedelta(minutes=15)), "round": "auto_sync",
             "track": "Y4", "run_id": "C", "script": "sync-from-drive",
             "dry_run": 0, "event": "db_ok", "latest": "sf-db-20260607-231546Z.sql.gz"},
            {"ts": _iso(now - timedelta(minutes=14)), "round": "auto_sync",
             "track": "Y4", "run_id": "C", "script": "sync-from-drive",
             "dry_run": 0, "event": "end"},
        ]
        events = send_dry_run_a + send_real_b + recv_real_c
        _write_audit(audit_path, events)

        manifest = {
            "ts": _iso(now - timedelta(minutes=31)),
            "db_sha256": "92bc4412e2a1beed5a210f59aca9033a9c60dea5b6024382805dc1df18d58f62",
            "db_size": 23837479,
            "env_sha256": "abc123def456",
            "sif_tag": "sif-20260607-231406Z",
            "sif_count": 5,
        }
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        env_patch = {
            "AUTO_SYNC_AUDIT_FILE": audit_path,
            "AUTO_SYNC_LATEST_FILE": latest_path,
        }
        with mock.patch.dict(os.environ, env_patch, clear=False):
            r = client.get("/api/v1/_internal/sync-status")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["audit_available"] is True, body
        # send 측
        send = body["send"]
        assert send["available"] is True, send
        assert send["last_event"]["event"] == "end", send
        assert send["last_event"]["run_id"] == "B", send
        assert send["last_success"]["event"] == "end", send
        assert send["last_run"]["run_id"] == "B", send
        assert send["last_run"]["ok"] is True, send
        # 24h 윈도우: run A (dry) + run B (real) = 2 runs, 둘 다 ok, 1 dry.
        assert send["counters_24h"]["runs"] == 2, send["counters_24h"]
        assert send["counters_24h"]["ok"] == 2, send["counters_24h"]
        assert send["counters_24h"]["dry_runs"] == 1, send["counters_24h"]
        assert send["counters_24h"]["fail"] == 0, send["counters_24h"]
        # recv 측
        recv = body["recv"]
        assert recv["available"] is True, recv
        assert recv["last_event"]["event"] == "end", recv
        assert recv["last_event"]["run_id"] == "C", recv
        assert recv["counters_24h"]["runs"] == 1, recv["counters_24h"]
        assert recv["counters_24h"]["ok"] == 1, recv["counters_24h"]
        # latest_manifest
        lm = body["latest_manifest"]
        assert lm["available"] is True, lm
        assert lm["sif_tag"] == "sif-20260607-231406Z", lm
        assert lm["db_sha256"].startswith("92bc4412"), lm
        assert "mtime" in lm, lm
        # summary
        s = body["summary"]
        assert s["send_ok_24h"] is True, s
        assert s["recv_ok_24h"] is True, s
        assert s["any_fail_24h"] is False, s
        assert s["latest_present"] is True, s
        print("[ok] sync-status: send/recv counters + manifest 정확")


def test_sync_status_graceful_skip_bad_lines():
    """손상된 JSONL 라인은 graceful skip — 정상 라인만 카운트."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    with tempfile.TemporaryDirectory() as tmp:
        audit_path = os.path.join(tmp, "portal_deploy.jsonl")
        latest_path = os.path.join(tmp, "LATEST.json")
        now = datetime.now(timezone.utc)
        good = {"ts": _iso(now - timedelta(minutes=10)), "round": "auto_sync",
                "track": "Y3", "run_id": "X", "script": "sync-to-drive",
                "dry_run": 0, "event": "end"}
        with open(audit_path, "w", encoding="utf-8") as f:
            f.write("{not json\n")
            f.write(json.dumps(good) + "\n")
            f.write("\n")  # 공백 라인
            f.write("[\"array not object\"]\n")
        env_patch = {
            "AUTO_SYNC_AUDIT_FILE": audit_path,
            "AUTO_SYNC_LATEST_FILE": latest_path,
        }
        with mock.patch.dict(os.environ, env_patch, clear=False):
            r = client.get("/api/v1/_internal/sync-status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["send"]["available"] is True, body
        assert body["send"]["last_event"]["run_id"] == "X", body
        # end event 만 있고 start 없는 run — counters 에는 한 줄로 잡힘.
        assert body["send"]["counters_24h"]["runs"] >= 1, body["send"]["counters_24h"]
        print("[ok] sync-status: 손상 라인 graceful skip")


if __name__ == "__main__":
    test_sync_status_missing_files()
    test_sync_status_with_events_and_manifest()
    test_sync_status_graceful_skip_bad_lines()
    print("\nsync-status endpoint 테스트 모두 통과.")
