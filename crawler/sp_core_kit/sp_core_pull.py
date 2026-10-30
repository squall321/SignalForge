#!/usr/bin/env python3
"""Standard Platform 코어 수신 클라이언트 — 설정만 고치면 도는 한 파일.

    pip install requests
    python sp_core_pull.py --config config.ini

무엇을 하나
    1. 카탈로그(GET /core)로 공개 타입과 칸을 읽는다. 정의 판(revision)이 바뀌었으면 무엇이
       달라졌는지 적고, 전부 처음부터 다시 받는다.
    2. 타입마다 GET /core/<타입>?since=... 를 next 가 빌 때까지 이어 받는다.
    3. 객체를 다 받은 뒤, 관계가 열린 타입은 GET /core/<타입>/relations 도 같은 식으로 받는다.
    4. 받은 행을 CSV 와 SQLite 에 저장한다(save_rows 를 고치면 자기 DB 로 간다).
    5. 마지막에 온 as_of 를 state.json 에 적어 두고, 다음 실행에서 그대로 돌려준다.

지켜야 할 것 — 이 파일은 이미 지키고 있다
    · as_of 는 서버가 준 값을 그대로 돌려준다(자기 시계로 만들지 않는다)
    · next 가 있으면 as_of 를 저장하지 않는다(끝까지 받은 뒤에만)
    · key 를 저장한다(다음 수신이 수정이 되는 근거)
    · deleted 행은 비활성 처리한다(삭제하지 않는다) — hidden 이면 이름 · 칸은 그대로 둔다
    · 사라진 객체를 끝으로 하는 선은 끊는다(공개 측은 그 선을 「끊긴 선」 으로 보내지 않는다)
    · renamed_from · previous_keys 가 오면 가진 행과 선을 새 key 로 옮긴다
    · 모르는 칸은 무시한다(공개 측이 칸을 추가해도 깨지지 않는다)
    · 처음부터 받았으면(--full · reset · 정의 판 변경) 끝까지 받은 뒤 안 온 것을 정리한다
    · 404 는 두 가지다 — 공개 안 된 타입은 건너뛰고 알리고, 커서를 못 읽으면 다시 받는다

reset 은 무엇인가
    오래 안 받아 갔으면 서버가 「그 시점부터는 **끊긴 것을 알려 줄 수 없다**」 고 말한다
    (끊긴 선의 기록은 한동안만 들고 있다). 그때 빈 쪽을 「바뀐 것 없음」 으로 읽으면 이미
    끊긴 선을 영영 들고 있게 된다 — 그래서 저장한 시각을 버리고 처음부터 다시 받고,
    **마지막 쪽까지 받은 뒤** 이번에 안 온 것을 정리한다. 먼저 비우지 않는 것은 중간에
    실패하면 아직 안 받은 것까지 사라지기 때문이다.

끝나는 값
    0  정상
    1  멈춤(인증 · 주소 · 연결 · 서버 오류) — 화면의 마지막 줄이 까닭이다
    2  끝까지 돌았지만 사람이 볼 것이 있다(공개 안 된 타입 · 정리를 멈춘 타입) — 「확인 필요」 줄
"""
from __future__ import annotations

import argparse
import configparser
import csv
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import requests

#: 이 키트의 판 — 바뀐 것은 CHANGES.md 에 같은 이름으로 적는다.
KIT_VERSION = "2026-10-21"

ENVELOPE = (
    "key",
    "label",
    "status",
    "updated_at",
    "deleted",
    "hidden",
    "merged_into",
    "renamed_from",
    "previous_keys",
)

#: 처음부터 다시 받았는데 가진 것의 이 비율보다 많이(그리고 GONE_FLOOR 건보다 많이) 안 왔으면
#: 정리하지 않고 멈춘다 — 토큰의 부서 · 공개 범위가 바뀐 날 전부를 비활성으로 만들지 않게.
GONE_LIMIT = 0.5
GONE_FLOOR = 10


class NotPublished(Exception):
    """APP-CORE-0001 — 공개되지 않은 타입이다(공개를 거두었거나 이름이 틀렸다)."""


class CursorLost(Exception):
    """APP-CORE-0002 — 커서를 읽을 수 없다. 커서 없이 같은 시각부터 다시 받는다."""


def load_config(path: str) -> dict[str, Any]:
    parser = configparser.ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        sys.exit(f"설정 파일을 읽지 못했습니다: {path}")
    section = parser["sp"]
    token = os.environ.get("SP_TOKEN") or section.get("token", "")
    if not token:
        sys.exit("토큰이 없습니다. config.ini 의 token 또는 환경변수 SP_TOKEN 을 지정하세요.")
    base_url = section.get("base_url", "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")) or "<" in base_url:
        sys.exit(f"config.ini 의 base_url 에 공개 측이 알려 준 주소를 적으세요(끝이 /api): {base_url!r}")
    types = [one.strip() for one in section.get("types", "").split(",") if one.strip()]
    return {
        "base_url": base_url,
        "token": token,
        "types": types,
        "out_dir": Path(section.get("out_dir", "out")),
        "page_size": section.getint("page_size", 500),
        "timeout": section.getint("timeout_seconds", 60),
        "retries": section.getint("retries", 3),
    }


def request_json(cfg: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
    """GET 한 번 — 일시적 오류는 다시 시도하고, 권한 오류는 즉시 중단한다."""
    url = f"{cfg['base_url']}{path}"
    headers = {"Authorization": f"Bearer {cfg['token']}", "Accept": "application/json"}
    for attempt in range(1, cfg["retries"] + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=cfg["timeout"])
        except requests.RequestException as caught:
            if attempt == cfg["retries"]:
                sys.exit(f"연결하지 못했습니다: {caught}")
            time.sleep(2 * attempt)
            continue
        if response.status_code in (401, 403):
            sys.exit(f"인증이 거절되었습니다(HTTP {response.status_code}). 토큰과 범위를 확인하세요: {said(response)}")
        if response.status_code == 404:
            # **404 는 두 가지다.** 상태 코드만 보고 멈추면 커서 문제 하나로 수신이 통째로 선다.
            code = error_code(response)
            if code.endswith("CORE-0001"):
                raise NotPublished(said(response))
            if code.endswith("CORE-0002"):
                raise CursorLost(said(response))
            sys.exit(f"주소를 찾지 못했습니다(HTTP 404): {url} — config.ini 의 base_url 을 확인하세요")
        if response.status_code >= 500 or response.status_code == 429:
            if attempt == cfg["retries"]:
                sys.exit(f"서버 오류가 계속됩니다(HTTP {response.status_code}): {said(response)}")
            time.sleep(2 * attempt)
            continue
        if response.status_code >= 400:
            sys.exit(f"요청이 거절되었습니다(HTTP {response.status_code}): {said(response)}")
        return response.json()
    return None


def said(response: requests.Response) -> str:
    try:
        return str((response.json().get("error") or {}).get("message") or "")[:300]
    except Exception:
        return response.text[:300]


def error_code(response: requests.Response) -> str:
    try:
        return str((response.json().get("error") or {}).get("code") or "")
    except Exception:
        return ""


def state_path(cfg: dict[str, Any]) -> Path:
    return cfg["out_dir"] / "state.json"


def load_state(cfg: dict[str, Any]) -> dict[str, str]:
    path = state_path(cfg)
    if path.exists():
        return dict(json.loads(path.read_text(encoding="utf-8")))
    return {}


def save_state(cfg: dict[str, Any], state: dict[str, str]) -> None:
    state_path(cfg).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def remember_catalog(cfg: dict[str, Any], catalog: dict[str, Any]) -> list[str]:
    """카탈로그를 `out/catalog.json` 에 둔다 — 정의 판이 바뀌었으면 **무엇이 달라졌나**를 돌려준다.

    직전 것은 `catalog.prev.json` 으로 남긴다. 처음이면(직전 것이 없으면) 빈 목록이다.
    """
    path = cfg["out_dir"] / "catalog.json"
    lines: list[str] = []
    if path.exists():
        before = json.loads(path.read_text(encoding="utf-8"))
        if before.get("revision") != catalog.get("revision"):
            lines.append(
                f"정의 판이 바뀌었습니다: {before.get('revision')} → {catalog.get('revision')}"
            )
            lines.extend(diff_catalog(before, catalog))
            path.replace(cfg["out_dir"] / "catalog.prev.json")
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    return lines


def diff_catalog(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """두 카탈로그의 차이 — 타입 · 칸 · 관계. 표시 문구(이름 · 설명)는 보지 않는다."""
    old = {one["slug"]: one for one in before.get("types") or []}
    new = {one["slug"]: one for one in after.get("types") or []}
    out = [f"  + 타입 {slug}" for slug in sorted(set(new) - set(old))]
    out += [f"  - 타입 {slug} (더 이상 공개되지 않음)" for slug in sorted(set(old) - set(new))]
    for slug in sorted(set(new) & set(old)):
        a = {one["key"]: one for one in old[slug].get("properties") or []}
        b = {one["key"]: one for one in new[slug].get("properties") or []}
        out += [f"  + {slug}.{key} ({b[key].get('data_type')})" for key in sorted(set(b) - set(a))]
        out += [f"  - {slug}.{key}" for key in sorted(set(a) - set(b))]
        for key in sorted(set(a) & set(b)):
            changed = [
                name
                for name in ("data_type", "multi", "ref_type_slug", "enum_options", "unit")
                if a[key].get(name) != b[key].get(name)
            ]
            if changed:
                out.append(f"  ~ {slug}.{key}: {', '.join(changed)} 바뀜")
        was = set(old[slug].get("relations") or [])
        now = set(new[slug].get("relations") or [])
        out += [f"  + {slug} 관계 {one}" for one in sorted(now - was)]
        out += [f"  - {slug} 관계 {one}" for one in sorted(was - now)]
    return out


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    """봉투 + properties 를 한 줄로. 봉투 이름이 우선한다.

    다중 값은 `;` 로 잇는다 — 파이썬 표기(`['A', 'B']`)로 두면 엑셀에서 읽을 수 없다.
    """
    out: dict[str, Any] = {}
    for key, value in (row.get("properties") or {}).items():
        out[key] = ";".join(str(one) for one in value) if isinstance(value, list) else value
    for name in ENVELOPE:
        if name in row:
            value = row[name]
            out[name] = ";".join(str(one) for one in value) if isinstance(value, list) else value
    return out


def connect(cfg: dict[str, Any]) -> sqlite3.Connection:
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(cfg["out_dir"] / "sp_core.sqlite")


def chunks(items: list[Any], size: int = 400) -> Iterator[list[Any]]:
    """IN (...) 에 넣을 만큼씩 — 옛 SQLite 는 한 문장의 자리표가 999 개까지다."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def object_table(db: sqlite3.Connection, type_slug: str) -> None:
    db.execute(
        f'CREATE TABLE IF NOT EXISTS "{type_slug}" '
        "(key TEXT PRIMARY KEY, label TEXT, status TEXT, updated_at TEXT, "
        "deleted INTEGER, merged_into TEXT, properties TEXT)"
    )


def relation_tables(db: sqlite3.Connection) -> list[str]:
    """이미 만든 선 표 전부 — 옛 키트가 만든 것이면 키를 먼저 바꿔 둔다."""
    names = [
        one
        for (one,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%\\_\\_relations' ESCAPE '\\'"
        )
    ]
    for name in names:
        relations_table(db, name)
    return names


def old_keys(row: dict[str, Any]) -> list[str]:
    """이 행의 옛 식별자 — `previous_keys`(전부, 오래된 것부터)와 `renamed_from`(마지막 것)."""
    out: list[str] = []
    for one in [*(row.get("previous_keys") or []), row.get("renamed_from")]:
        if one and one != row["key"] and one not in out:
            out.append(str(one))
    return out


def rekey(db: sqlite3.Connection, type_slug: str, key: str, olds: list[str]) -> int:
    """식별자가 바뀐 것 — 가진 행과 선을 **새 key 로 옮긴다.** 옮긴 행 수를 돌려준다.

    옮기지 않으면 새 key 가 처음 보는 것이 되어 같은 것이 둘이 된다. 오래 안 받았으면 가진
    것이 마지막 옛 식별자가 아닐 수 있어(A→B→C 에서 A) 옛 식별자를 전부 본다. 옛 → 새 짝은
    `<타입>__previous_keys` 표에 남긴다 — 다른 행의 참조 칸에 남은 옛 값을 거기서 푼다.
    """
    db.execute(
        f'CREATE TABLE IF NOT EXISTS "{type_slug}__previous_keys" '
        "(old_key TEXT PRIMARY KEY, key TEXT)"
    )
    db.executemany(
        f'INSERT INTO "{type_slug}__previous_keys" (old_key, key) VALUES (?, ?) '
        "ON CONFLICT(old_key) DO UPDATE SET key=excluded.key",
        [(old, key) for old in olds],
    )
    moved = 0
    has_new = db.execute(f'SELECT 1 FROM "{type_slug}" WHERE key=?', (key,)).fetchone() is not None
    for old in olds:
        if has_new:
            # 새 key 로 이미 받았다 — 옛 행은 같은 것의 두 번째 줄이다.
            moved += db.execute(f'DELETE FROM "{type_slug}" WHERE key=?', (old,)).rowcount
        else:
            done = db.execute(f'UPDATE "{type_slug}" SET key=? WHERE key=?', (key, old)).rowcount
            moved += done
            has_new = has_new or done > 0
    mine = f"{type_slug}__relations"
    for table in relation_tables(db):
        if table == mine:
            db.executemany(
                f'UPDATE OR REPLACE "{table}" SET src=? WHERE src=?', [(key, old) for old in olds]
            )
        db.executemany(
            f'UPDATE OR REPLACE "{table}" SET dst=? WHERE dst=? AND dst_type IN (?, \'\')',
            [(key, old, type_slug) for old in olds],
        )
    return moved


def cut_touching(db: sqlite3.Connection, type_slug: str, keys: list[str]) -> int:
    """그 객체를 끝으로 하는 선을 끊는다 — 지워졌거나 더 이상 안 보이는 객체.

    공개 측은 객체를 지우면 그 선을 **내보내지 않을 뿐, 「끊긴 선」 으로 알려 주지 않는다**
    (합쳐진 쪽도 같다 — 선은 이긴 쪽으로 옮겨 새로 온다). 받는 쪽이 끊지 않으면 없는 객체에
    매달린 선이 남는다.
    """
    if not keys:
        return 0
    cut = 0
    mine = f"{type_slug}__relations"
    for table in relation_tables(db):
        for part in chunks(keys):
            marks = ",".join("?" * len(part))
            if table == mine:
                cut += db.execute(f'DELETE FROM "{table}" WHERE src IN ({marks})', part).rowcount
            cut += db.execute(
                f'DELETE FROM "{table}" WHERE dst IN ({marks}) AND dst_type IN (?, \'\')',
                [*part, type_slug],
            ).rowcount
    return cut


def save_rows(cfg: dict[str, Any], type_slug: str, rows: list[dict[str, Any]]) -> dict[str, int]:
    """저장 — **여기만 고치면 자기 DB 로 간다.** 옮긴 것 · 되살아난 것의 수를 돌려준다.

    기본 동작
        CSV      이번에 받은 행을 이어 적는다 — **수신 이력**이다(같은 key 가 여러 번 나온다).
        SQLite   `key` 기준 upsert — **현재 상태**다. 삭제 행은 deleted=1 로 남는다.
    """
    done = {"renamed": 0, "revived": 0}
    if not rows:
        return done
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    flat = [flatten(one) for one in rows]
    columns: list[str] = []
    for one in flat:
        for name in one:
            if name not in columns:
                columns.append(name)

    csv_path = cfg["out_dir"] / f"{type_slug}.csv"
    exists = csv_path.exists()
    with csv_path.open("a" if exists else "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(flat)

    db = connect(cfg)
    try:
        object_table(db, type_slug)
        # 식별자가 바뀐 것을 **먼저** 옮긴다 — 그래야 아래 upsert 가 「새로 만들기」 가 아니라
        # 「고침」 이 된다.
        for one in rows:
            olds = old_keys(one)
            if olds:
                done["renamed"] += rekey(db, type_slug, one["key"], olds)

        shown = [one for one in rows if not one.get("hidden")]
        # 되살아난 것 — 지웠던 key 가 deleted=false 로 다시 왔다. 그 선은 바뀐 것이 아니라서
        # 증분으로 다시 오지 않는다(main 이 선을 처음부터 다시 받는다).
        alive = [one["key"] for one in shown if not one.get("deleted")]
        for part in chunks(alive):
            marks = ",".join("?" * len(part))
            done["revived"] += db.execute(
                f'SELECT count(*) FROM "{type_slug}" WHERE deleted=1 AND key IN ({marks})', part
            ).fetchone()[0]

        def values(one: dict[str, Any]) -> tuple[Any, ...]:
            return (
                one["key"],
                one.get("label"),
                one.get("status"),
                one.get("updated_at"),
                1 if one.get("deleted") else 0,
                one.get("merged_into"),
                json.dumps(one.get("properties") or {}, ensure_ascii=False),
            )

        insert = (
            f'INSERT INTO "{type_slug}" (key,label,status,updated_at,deleted,merged_into,properties) '
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "label=excluded.label, status=excluded.status, updated_at=excluded.updated_at, "
            "deleted=excluded.deleted, merged_into=excluded.merged_into"
        )
        # 살아 있는 행은 칸을 **통째로** 바꾼다 — 합치면 SP 에서 비운 칸이 남는다.
        db.executemany(
            insert + ", properties=excluded.properties",
            [values(one) for one in shown if not one.get("deleted")],
        )
        # 지운 행은 칸이 빈 채로 온다 — 가진 칸은 두고(이미 쓴 기록이 그 값을 보인다) 비활성만.
        # 가진 적 없으면 비활성 행으로 만든다(merged_into 를 남기려고).
        db.executemany(insert, [values(one) for one in shown if one.get("deleted")])
        # hidden — 지운 것이 아니라 이 토큰으로 더 이상 안 보이는 것(소유 부서가 옮겨 감).
        # 이름 · 칸은 오지 않으므로 가진 것을 그대로 두고 비활성만 한다. 없던 것은 만들지 않는다.
        db.executemany(
            f'UPDATE "{type_slug}" SET deleted=1, updated_at=? WHERE key=?',
            [(one.get("updated_at"), one["key"]) for one in rows if one.get("hidden")],
        )
        cut_touching(db, type_slug, [one["key"] for one in rows if one.get("deleted")])
        db.commit()
    finally:
        db.close()
    return done


RELATION_TABLE = (
    "(src TEXT, relation TEXT, dst TEXT, dst_type TEXT NOT NULL DEFAULT '', "
    "evidence_note TEXT, updated_at TEXT, properties TEXT, "
    "PRIMARY KEY (src, relation, dst, dst_type))"
)


def relations_table(db: sqlite3.Connection, table: str) -> None:
    """선 표를 마련한다 — **도착 타입까지 키다.**

    끝이 인터페이스인 관계는 도착이 여러 타입이고 `key` 는 타입 안에서만 하나라, 같은 출발 ·
    관계 · 도착 `key` 가 두 타입에 걸칠 수 있다. 세 끝만 키로 두면 그 둘이 한 줄로 겹쳐 하나가
    사라진다. 옛 키트가 만든 표(세 끝이 키)면 내용을 그대로 옮겨 키를 바꾼다.
    """
    columns = list(db.execute(f'PRAGMA table_info("{table}")'))
    if not columns:
        db.execute(f'CREATE TABLE "{table}" {RELATION_TABLE}')
        return
    if any(one[1] == "dst_type" and one[5] for one in columns):
        return
    old = f"{table}__old"
    db.execute(f'ALTER TABLE "{table}" RENAME TO "{old}"')
    db.execute(f'CREATE TABLE "{table}" {RELATION_TABLE}')
    db.execute(
        f'INSERT OR REPLACE INTO "{table}" '
        "(src,relation,dst,dst_type,evidence_note,updated_at,properties) "
        "SELECT src, relation, dst, coalesce(dst_type, ''), evidence_note, updated_at, "
        f'properties FROM "{old}"'
    )
    db.execute(f'DROP TABLE "{old}"')


def gone_here(db: sqlite3.Connection, type_slug: str, key: Any) -> bool:
    """그 객체가 이쪽에서 비활성인가 — 그 타입을 받지 않으면(표가 없으면) 모른다(아니다)."""
    try:
        row = db.execute(f'SELECT deleted FROM "{type_slug}" WHERE key=?', (key,)).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row[0])


def save_relations(cfg: dict[str, Any], type_slug: str, rows: list[dict[str, Any]]) -> None:
    """선 저장 — CSV 는 수신 이력, SQLite 는 현재 상태(출발 · 관계 · 도착 · 도착 타입이 키다).

    `deleted` 인 줄은 **지운다** — 선은 상태가 아니라 있음/없음이다. 도착 객체가 이쪽에서
    비활성이면 넣지 않는다 — 공개 측은 출발이 지워진 선만 거르고 도착이 지워진 선은 보낸다.
    """
    if not rows:
        return
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    columns = ["src", "relation", "dst", "dst_type", "evidence_note", "updated_at", "deleted"]
    flat = [
        {
            **{name: one.get(name) for name in columns},
            **{
                key: ";".join(str(item) for item in value)
                if isinstance(value, list)
                else value
                for key, value in (one.get("properties") or {}).items()
            },
        }
        for one in rows
    ]
    names: list[str] = list(columns)
    for one in flat:
        for name in one:
            if name not in names:
                names.append(name)
    csv_path = cfg["out_dir"] / f"{type_slug}-relations.csv"
    exists = csv_path.exists()
    with csv_path.open("a" if exists else "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=names, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(flat)

    table = f"{type_slug}__relations"
    db = connect(cfg)
    try:
        relations_table(db, table)
        for one in rows:
            keys = (one.get("src"), one.get("relation"), one.get("dst"))
            dst_type = one.get("dst_type") or ""
            if one.get("deleted") or gone_here(db, dst_type, one.get("dst")):
                # 도착 타입이 빈 줄은 옛 키트가 받은 것이다 — 그것도 같은 선으로 본다.
                db.execute(
                    f'DELETE FROM "{table}" WHERE src=? AND relation=? AND dst=? '
                    "AND dst_type IN (?, '')",
                    (*keys, dst_type),
                )
                continue
            db.execute(
                f'INSERT INTO "{table}" '
                "(src,relation,dst,dst_type,evidence_note,updated_at,properties) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(src,relation,dst,dst_type) DO UPDATE SET "
                "evidence_note=excluded.evidence_note, "
                "updated_at=excluded.updated_at, properties=excluded.properties",
                (
                    *keys,
                    dst_type,
                    one.get("evidence_note"),
                    one.get("updated_at"),
                    json.dumps(one.get("properties") or {}, ensure_ascii=False),
                ),
            )
        db.commit()
    finally:
        db.close()


def too_many(gone: int, held: int) -> bool:
    return gone > GONE_FLOOR and gone > GONE_LIMIT * held


def prune_rows(
    cfg: dict[str, Any], type_slug: str, seen: set[str], *, allow_shrink: bool
) -> tuple[int, int]:
    """처음부터 받았으면 **이번에 안 온 것을 비활성으로 둔다** — (정리한 수, 멈춘 수).

    `since` 없이 받으면 지워진 것은 오지 않는다 — 「지워졌다」 를 들을 길이 없으니 부재로
    안다. 마지막 쪽까지 받은 뒤에만 부른다(중간에 대조하면 아직 안 받은 것이 지워진다).
    """
    db = connect(cfg)
    try:
        object_table(db, type_slug)
        held = [key for (key,) in db.execute(f'SELECT key FROM "{type_slug}" WHERE deleted=0')]
        gone = [key for key in held if key not in seen]
        if too_many(len(gone), len(held)) and not allow_shrink:
            return 0, len(gone)
        for part in chunks(gone):
            marks = ",".join("?" * len(part))
            db.execute(f'UPDATE "{type_slug}" SET deleted=1 WHERE key IN ({marks})', part)
        cut_touching(db, type_slug, gone)
        db.commit()
        return len(gone), 0
    finally:
        db.close()


def prune_relations(
    cfg: dict[str, Any], type_slug: str, seen: set[tuple[str, ...]], *, allow_shrink: bool
) -> tuple[int, int]:
    """선도 같다 — 처음부터 받았으면 이번에 안 온 선을 끊는다. (끊은 수, 멈춘 수)."""
    table = f"{type_slug}__relations"
    db = connect(cfg)
    try:
        relations_table(db, table)
        held = [
            tuple(one)
            for one in db.execute(f'SELECT src, relation, dst, dst_type FROM "{table}"')
        ]
        gone = [one for one in held if one not in seen]
        if too_many(len(gone), len(held)) and not allow_shrink:
            return 0, len(gone)
        db.executemany(
            f'DELETE FROM "{table}" WHERE src=? AND relation=? AND dst=? AND dst_type=?', gone
        )
        db.commit()
        return len(gone), 0
    finally:
        db.close()


def new_counts() -> dict[str, int]:
    return {
        "rows": 0,
        "deleted": 0,
        "pages": 0,
        "reset": 0,
        "restarted": 0,
        "full": 0,
        "renamed": 0,
        "revived": 0,
        "gone": 0,
        "held_back": 0,
    }


def pages(
    cfg: dict[str, Any], path: str, since: str, counts: dict[str, int], label: str
) -> Iterator[dict[str, Any]]:
    """쪽을 마지막까지 내준다 — 객체와 선이 **같은 규칙**(since · next · as_of · reset · 404).

    끝나면 `counts["full"]` 이 처음부터 받았는지(since 없이) 말한다 — 그때만 부재 대조를 한다.
    """
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": cfg["page_size"]}
        if since:
            params["since"] = since
        if cursor:
            params["cursor"] = cursor
        try:
            body = request_json(cfg, path, params)
        except CursorLost as lost:
            # 커서를 못 읽으면 커서 없이 **같은 시각부터** 다시 받는다 — 같은 행이 두 번 와도
            # 덮어쓰면 그만이다. 되풀이되면 무한히 돌 자리다.
            if not cursor or counts["restarted"]:
                sys.exit(f"{label}: 커서를 읽을 수 없다는 답이 되풀이됩니다 — 공개 측에 문의하세요: {lost}")
            print(f"    커서를 읽을 수 없다고 합니다 — 같은 시각부터 다시 받습니다: {lost}")
            cursor = None
            counts["restarted"] = 1
            continue
        if body.get("reset"):
            # **처음부터 다시 받으라는 말이다.** 빈 쪽을 「바뀐 것 없음」 으로 읽으면 이미
            # 끊긴 것을 영영 들고 있는다. 오늘은 선 쪽만 이 말을 하지만, 말이 오면 따른다.
            if counts["reset"]:
                sys.exit(
                    f"{label}: since 를 비웠는데도 reset 이 옵니다 — "
                    f"공개 측에 문의하세요: {body.get('reset_reason') or ''}"
                )
            print(f"    처음부터 다시 받습니다 — {body.get('reset_reason') or 'reset'}")
            since, cursor = "", None
            counts["reset"] = 1
            continue
        counts["pages"] += 1
        cursor = body.get("next")
        if not cursor:
            counts["full"] = 0 if since else 1
        yield body
        if not cursor:
            return


def pull_type(
    cfg: dict[str, Any], type_slug: str, state: dict[str, str], *, allow_shrink: bool = False
) -> dict[str, int]:
    """한 타입의 객체. 처음부터 받았으면 끝까지 받은 뒤 안 온 것을 비활성으로 둔다."""
    counts = new_counts()
    seen: set[str] = set()
    last: dict[str, Any] = {}
    for body in pages(cfg, f"/core/{type_slug}", state.get(type_slug, ""), counts, type_slug):
        items = body.get("items") or []
        # 자기 DB 로 고친 save_rows 가 아무것도 돌려주지 않아도 돈다(옛 키트에서 옮겨 온 것).
        done = save_rows(cfg, type_slug, items) or {}
        counts["renamed"] += done.get("renamed", 0)
        counts["revived"] += done.get("revived", 0)
        counts["rows"] += len(items)
        counts["deleted"] += sum(1 for one in items if one.get("deleted"))
        seen.update(str(one["key"]) for one in items if not one.get("deleted"))
        last = body
    if counts["full"]:
        counts["gone"], counts["held_back"] = prune_rows(
            cfg, type_slug, seen, allow_shrink=allow_shrink
        )
    # **끝까지 받았을 때만 시각을 옮긴다.**
    if last.get("as_of"):
        state[type_slug] = str(last["as_of"])
    return counts


def pull_relations(
    cfg: dict[str, Any], type_slug: str, state: dict[str, str], *, allow_shrink: bool = False
) -> dict[str, int]:
    """그 타입에서 출발하는 선 — 객체와 **같은 규칙**. 기준 시각은 `<타입>#relations` 로 따로."""
    mark = f"{type_slug}#relations"
    counts = new_counts()
    seen: set[tuple[str, ...]] = set()
    last: dict[str, Any] = {}
    label = f"{type_slug} 관계"
    for body in pages(cfg, f"/core/{type_slug}/relations", state.get(mark, ""), counts, label):
        items = body.get("items") or []
        save_relations(cfg, type_slug, items)
        counts["rows"] += len(items)
        counts["deleted"] += sum(1 for one in items if one.get("deleted"))
        seen.update(
            (str(one.get("src")), str(one.get("relation")), str(one.get("dst")), one.get("dst_type") or "")
            for one in items
            if not one.get("deleted")
        )
        last = body
    if counts["full"]:
        counts["gone"], counts["held_back"] = prune_relations(
            cfg, type_slug, seen, allow_shrink=allow_shrink
        )
    if last.get("as_of"):
        state[mark] = str(last["as_of"])
    return counts


def tail(counts: dict[str, int]) -> str:
    """한 줄 요약의 꼬리 — 일어난 것만."""
    parts = []
    if counts["reset"]:
        parts.append("처음부터 다시 받음(reset)")
    if counts["restarted"]:
        parts.append("커서 다시 받음")
    if counts["renamed"]:
        parts.append(f"식별자 옮김 {counts['renamed']}")
    if counts["revived"]:
        parts.append(f"되살아남 {counts['revived']}")
    if counts["gone"]:
        parts.append(f"안 와서 정리 {counts['gone']}")
    if counts["held_back"]:
        parts.append(f"정리 멈춤 {counts['held_back']}")
    return "".join(f" · {one}" for one in parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standard Platform 코어 수신")
    parser.add_argument("--config", default="config.ini")
    parser.add_argument("--full", action="store_true", help="처음부터 다시 받는다(끝나면 안 온 것을 정리)")
    parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help="처음부터 받았을 때 가진 것의 절반 넘게 안 와도 정리한다(정말 줄었을 때만)",
    )
    parser.add_argument("--version", action="version", version=f"sp_core_pull {KIT_VERSION}")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    catalog = request_json(cfg, "/core")
    opened = [one["slug"] for one in catalog.get("types") or []]
    print(
        f"키트 {KIT_VERSION} · 시스템 {catalog.get('system')} · 정의 판 {catalog.get('revision')}"
        f" · 공개 타입 {opened}"
    )
    changed = remember_catalog(cfg, catalog)
    for line in changed:
        print(line)

    problems: list[str] = []
    wanted = cfg["types"] or opened
    missing = [one for one in wanted if one not in opened]
    if missing:
        problems.append(f"공개되지 않은 타입은 건너뛰었습니다: {missing} — 공개 측 관리자에게 확인하세요")
        wanted = [one for one in wanted if one in opened]

    has_relations = {
        one["slug"]: bool(one.get("relations"))
        for one in catalog.get("types") or []
    }
    state: dict[str, str] = {} if args.full else load_state(cfg)
    if changed and state:
        # 새로 열린 칸 · 관계의 기존 값은 바뀐 것이 아니라서 증분으로 오지 않는다.
        print("정의가 바뀌어 전부 처음부터 다시 받습니다")
        state = {}

    def held(label: str, counts: dict[str, int]) -> None:
        if counts["held_back"]:
            problems.append(
                f"{label}: 처음부터 받았는데 가진 것 중 {counts['held_back']}건이 안 와서(절반 넘음) "
                "정리하지 않았습니다. 토큰의 부서 · 공개 범위를 공개 측에 확인하고, 정말 줄어든 "
                "것이면 --full --allow-shrink 로 한 번 더 받으세요"
            )

    # 객체를 **모두 먼저** 받는다 — 식별자 옮김 · 지워진 끝을 알아야 선을 바로 놓는다.
    revived = False
    pulled: list[str] = []
    for type_slug in wanted:
        try:
            counts = pull_type(cfg, type_slug, state, allow_shrink=args.allow_shrink)
        except NotPublished as caught:
            problems.append(f"{type_slug}: 공개되지 않은 타입이라 건너뛰었습니다 — {caught}")
            continue
        pulled.append(type_slug)
        print(
            f"  {type_slug}: {counts['rows']}건 수신"
            f"(삭제 {counts['deleted']}) · {counts['pages']}페이지{tail(counts)}"
        )
        held(type_slug, counts)
        revived = revived or counts["revived"] > 0
        save_state(cfg, state)

    if revived:
        # 되살아난 객체의 선은 바뀐 것이 아니라서 증분으로 다시 오지 않는다.
        print("  되살아난 객체가 있어 선을 처음부터 다시 받습니다")
        for mark in [one for one in state if one.endswith("#relations")]:
            state.pop(mark)

    for type_slug in pulled:
        if not has_relations.get(type_slug):
            continue
        try:
            edges = pull_relations(cfg, type_slug, state, allow_shrink=args.allow_shrink)
        except NotPublished as caught:
            problems.append(f"{type_slug} 관계: 공개되지 않아 건너뛰었습니다 — {caught}")
            continue
        print(
            f"  {type_slug} 관계: {edges['rows']}건 수신"
            f"(끊김 {edges['deleted']}) · {edges['pages']}페이지{tail(edges)}"
        )
        held(f"{type_slug} 관계", edges)
        save_state(cfg, state)

    print(f"저장 위치: {cfg['out_dir'].resolve()}")
    for one in problems:
        print(f"확인 필요: {one}", file=sys.stderr)
    return 2 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
