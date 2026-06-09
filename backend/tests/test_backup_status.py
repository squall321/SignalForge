"""
/api/v1/_internal/backup-status endpoint 단위 테스트 (Track E — backup verify).

검증 (1 케이스, 외부 의존 0):
  - 상태 파일 없음 → {"available": false, "ok": null}
  - 상태 파일 있음 → verify-backup.sh 페이로드가 그대로 노출 + available=true
  - 잘못된 JSON → {"available": false, "ok": null, "error": ...} (graceful)

실행:
    cd backend && .venv/bin/python tests/test_backup_status.py
    cd backend && .venv/bin/pytest tests/test_backup_status.py -v
"""
import json
import os
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_backup_status_states():
    """3 상태 (missing / valid / corrupt) 가 의도대로 graceful."""
    client = TestClient(app, client=("127.0.0.1", 50000))

    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "last_verified.json")

        # ── 1) 상태 파일 없음 ─────────────────────────────────────────
        with mock.patch.dict(os.environ, {"BACKUP_STATE_FILE": state_path}, clear=False):
            r = client.get("/api/v1/_internal/backup-status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is False, body
        assert body["ok"] is None, body
        assert body.get("path") == state_path, body

        # ── 2) 상태 파일 있음 (정상 페이로드) ─────────────────────────
        sample = {
            "ok": True,
            "verified_at": "2026-06-03T15:44:55Z",
            "reason": "ok",
            "drive_path": "ApptainerImages:SignalForge/db-dumps",
            "file": "sf-db-20260602-043001Z.sql.gz",
            "size_bytes": 23837479,
            "mtime": "2026-06-02T04:30:26.771Z",
            "age_hours": 35,
            "max_age_hours": 48,
            "min_size_bytes": 1048576,
            "sha256": "92bc4412e2a1beed5a210f59aca9033a9c60dea5b6024382805dc1df18d58f62",
        }
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(sample, f)
        with mock.patch.dict(os.environ, {"BACKUP_STATE_FILE": state_path}, clear=False):
            r = client.get("/api/v1/_internal/backup-status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True, body
        assert body["ok"] is True, body
        assert body["reason"] == "ok", body
        assert body["file"] == sample["file"], body
        assert body["size_bytes"] == sample["size_bytes"], body
        assert body["sha256"] == sample["sha256"], body

        # ── 3) 잘못된 JSON → graceful ─────────────────────────────────
        with open(state_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        with mock.patch.dict(os.environ, {"BACKUP_STATE_FILE": state_path}, clear=False):
            r = client.get("/api/v1/_internal/backup-status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is False, body
        assert body["ok"] is None, body
        assert "error" in body, body

        print("[ok] backup-status: missing/valid/corrupt 3 상태 모두 graceful")


if __name__ == "__main__":
    test_backup_status_states()
    print("\nbackup-status endpoint 테스트 통과.")
