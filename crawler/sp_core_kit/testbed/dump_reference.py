#!/usr/bin/env python3
"""참고 구현(sp_core_pull.py)이 받은 것을 compare.py 의 모양으로 내보낸다.

    python dump_reference.py --out ../out --to my_state.json

자기 플랫폼에 만든 받는 기능도 **이와 같은 모양**으로 자기 사본을 내보내면 된다 — 객체는
비활성까지 전부(active 로 가르고), 선은 지금 이어진 것만.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

SIDE = ("__relations", "__previous_keys", "__old")


def dump(out_dir: Path) -> dict[str, Any]:
    db = sqlite3.connect(out_dir / "sp_core.sqlite")
    try:
        tables = [name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        objects = []
        for table in tables:
            if table.endswith(SIDE):
                continue
            for key, label, status, deleted, props in db.execute(
                f'SELECT key, label, status, deleted, properties FROM "{table}"'
            ):
                objects.append(
                    {
                        "type": table,
                        "key": key,
                        "label": label,
                        "status": status or "active",
                        "active": not deleted,
                        "properties": json.loads(props or "{}"),
                    }
                )
        relations = []
        for table in tables:
            if not table.endswith("__relations"):
                continue
            src_type = table[: -len("__relations")]
            for src, relation, dst, dst_type in db.execute(
                f'SELECT src, relation, dst, dst_type FROM "{table}"'
            ):
                relations.append(
                    {"src_type": src_type, "src": src, "relation": relation, "dst": dst, "dst_type": dst_type}
                )
    finally:
        db.close()
    return {"objects": objects, "relations": relations}


def main() -> None:
    parser = argparse.ArgumentParser(description="참고 구현의 사본을 compare.py 모양으로")
    parser.add_argument("--out", default="out", help="sp_core_pull.py 의 out_dir")
    parser.add_argument("--to", default="my_state.json")
    args = parser.parse_args()
    Path(args.to).write_text(json.dumps(dump(Path(args.out)), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"내보냄: {args.to}")


if __name__ == "__main__":
    main()
