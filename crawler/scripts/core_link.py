# 코어와 잇기 대응표를 보고 고친다 — 사람 선 넣기 · 맞는 것 없음 · 거두기 · 다시 짓기(cae00 운영 사용자가 확인한 것을 넣는다)
"""코어와 잇기 — 대응표(crawler/data/plm/core-link/map.json) 손보기. 표는 공개 리포 밖이다(사용자 결정 2026-11-10).

  python3 -m scripts.core_link show                          # 개념마다 — rule · 사람 · 후보 · 사용 안 함 · 확인 필요
  python3 -m scripts.core_link set part hinge <코어 key>      # 사람 선(부품 → 코어 part, 증상 → 코어 failure_mode)
  python3 -m scripts.core_link none symptom lag "<까닭>"      # 코어에 맞는 것이 없다(사람 선 — key 없이 까닭)
  python3 -m scripts.core_link unset part hinge               # 사람 선 거두기(다음 짓기에서 rule · 후보로 돌아간다)
  python3 -m scripts.core_link rebuild                        # 사본으로 다시 짓기(받기 뒤에는 저절로 돈다)

set 은 사본에 그 key 가 살아 있는지 보고 넣는다. 고친 뒤 바로 다시 짓는다. SignalForge 어휘를 읽으려면 /shared 바인드가
있어야 한다 — apptainer exec --bind crawler:/crawler --bind shared:/shared apptainer/sif/crawler.sif sh -c "cd /crawler && …"
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base import core_link as C  # noqa: E402
from base import plm_mirror as P  # noqa: E402

USAGE = __doc__.split("\n\n")[1]


def _show() -> int:
    rows = {(r["kind"], r["sf_key"]): r for r in C.load_map()}
    try:
        review = json.loads((C.link_dir() / C.REVIEW).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        review = {"candidates": []}
    cands = {(c["kind"], c["sf_key"]): c for c in review.get("candidates", [])}
    for kind, labels in C.sf_vocab().items():
        print(f"== {kind}")
        for sf_key in labels:
            r, c = rows.get((kind, sf_key)), cands.get((kind, sf_key))
            if r:
                tail = r.get("reason") or r.get("matched_by") or ""
                extra = f" → 이긴 쪽 {r['merged_into']}" if r.get("merged_into") else ""
                print(f"  {sf_key:14} {r['origin']:5} {r['status']:6} {r.get('core_key') or '-':20} {tail}{extra}")
            elif c:
                opts = ", ".join(o["core_key"] for o in c["options"]) or "(없음)"
                print(f"  {sf_key:14} 후보  {c['why']:8} {opts}")
            else:
                print(f"  {sf_key:14} (아직 안 지었다)")
    return 0


def main(argv) -> int:
    if not argv or argv[0] not in ("show", "set", "none", "unset", "rebuild"):
        print(USAGE)
        return 1
    cmd = argv[0]
    if cmd == "show":
        return _show()
    if cmd == "rebuild":
        print(json.dumps(C.rebuild(), ensure_ascii=False))
        return 0
    if len(argv) < 3:
        print(USAGE)
        return 1
    kind, sf_key = argv[1], argv[2]
    if sf_key not in C.sf_vocab().get(kind, {}):
        print(f"[ERROR] SignalForge {kind} 에 '{sf_key}' 가 없다")
        return 1
    rows = C.load_map()
    if cmd == "set":
        core_key = argv[3] if len(argv) > 3 else ""
        core, _ = P._read_replica(P.default_root() / P.MIRROR, P.CORE_TYPES) if (P.default_root() / P.MIRROR).is_file() else ({}, {})
        alive = {r["key"] for r in core.get(dict(C.PAIRS)[kind], []) if not r.get("deleted")}
        if core_key not in alive:
            print(f"[ERROR] 사본의 코어 {dict(C.PAIRS)[kind]} 에 살아 있는 '{core_key}' 가 없다")
            return 1
        rows = C.set_human(rows, kind, sf_key, core_key)
    elif cmd == "none":
        rows = C.set_human(rows, kind, sf_key, None, " ".join(argv[3:]))
    else:
        rows = C.unset_human(rows, kind, sf_key)
    C.save_map(rows)
    print(json.dumps(C.rebuild(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
