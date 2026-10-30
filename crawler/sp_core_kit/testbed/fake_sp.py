#!/usr/bin/env python3
"""가짜 SP 코어 창구 — 받는 기능을 **실제 SP 없이** 끝까지 시험하는 시험장.

    python fake_sp.py                  # http://127.0.0.1:8765/api · 토큰 test-token
    python fake_sp.py --port 9000

실제 SP 의 코어 API 와 **같은 모양 · 같은 규칙**으로 답한다. 실제 SP 에서는 일부러 만들 수
없는 상황(식별자가 두 번 바뀜 · 합치기 · 안 보이게 됨 · 선을 남긴 삭제 · 되살림 · 커서 오류 ·
오래 안 받음 · 정의 판 바뀜 · 공개 거둠 · 토큰 범위 줄어듦)을 단계마다 일으킨다.

단계마다 할 일
    1. POST /_step            다음 단계로 — 세상이 바뀌고, 이번 단계의 설명 · 실행 방법이 온다
    2. 받는 기능을 한 번 돌린다(설명의 run 이 「처음부터」 면 처음부터 다시 받기로)
    3. 받은 결과를 JSON 으로 내보내고  python compare.py --dump 그파일  → 통과해야 한다

관리용 주소(토큰 없이): GET /_status · GET /_expected · POST /_step · POST /_restart
의존성 없음(파이썬 3.8 이상 표준 라이브러리).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

TOKEN = "test-token"
#: 오류 코드의 접두사 — 실제 SP 는 설치마다 다를 수 있다. 받는 쪽은 **끝(-CORE-0001)으로** 가른다.
PREFIX = "APP"
#: 한 쪽에 싣는 최대 행 수. 일부러 작게 — 쪽 넘김(next · cursor)을 반드시 거치게.
PAGE_CAP = 2
EPOCH = datetime(2026, 10, 1, tzinfo=timezone.utc)


def stamp(tick: int) -> str:
    return (EPOCH + timedelta(seconds=tick)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def tick_of(text: str) -> int:
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(text)
    return int(round((moment - EPOCH).total_seconds()))


class Fail(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.body = {"error": {"code": code, "message": message}}


def error_code(module: str, number: int) -> str:
    return f"{PREFIX}-{module}-{number:04d}"


class World:
    """SP 한 벌 — 객체 · 선 · 끊긴 선의 기록(무덤)과 시계."""

    def __init__(self, page_cap: int = PAGE_CAP) -> None:
        self.page_cap = page_cap
        self.tick = 0
        self.step = 0
        self.info: dict[str, Any] = {"step": 0, "title": "시작 전 — POST /_step 으로 1단계를 여세요"}
        self.types: dict[str, dict[str, Any]] = {}
        self.kinds: dict[str, dict[str, Any]] = {}
        self.objects: dict[int, dict[str, Any]] = {}
        self.relations: dict[int, dict[str, Any]] = {}
        self.graves: dict[int, dict[str, Any]] = {}
        self.purged_before = 0
        self.cursor_lost_once = False
        self.closed_race: set[str] = set()
        self.frozen: dict[str, Any] | None = None
        self.ids = 0

    # --- 바꾸기 -------------------------------------------------------------------------

    def now(self) -> int:
        self.tick += 1
        return self.tick

    def new_id(self) -> int:
        self.ids += 1
        return self.ids

    def add_type(self, slug: str, label: str, properties: list[dict[str, Any]], *, open: bool) -> None:
        self.types[slug] = {"label": label, "open": open, "properties": properties}

    def add_kind(
        self, slug: str, label: str, src: str, dst: list[str], *, interface: str | None = None
    ) -> None:
        self.kinds[slug] = {"label": label, "src": src, "dst": dst, "interface": interface}

    def make(self, type_slug: str, key: str, label: str, **props: Any) -> int:
        oid = self.new_id()
        self.objects[oid] = {
            "id": oid,
            "type": type_slug,
            "key": key,
            "label": label,
            "status": "active",
            "props": {k: v for k, v in props.items() if v is not None},
            "at": self.now(),
            "deleted": False,
            "merged_into": None,
            "previous_keys": [],
            "hidden": False,
            "silent": False,
        }
        return oid

    def find(self, type_slug: str, key: str) -> int:
        return next(
            one["id"]
            for one in self.objects.values()
            if one["type"] == type_slug and one["key"] == key and not one["deleted"]
        )

    def edit(self, oid: int, *, label: str | None = None, status: str | None = None, **props: Any) -> None:
        one = self.objects[oid]
        if label is not None:
            one["label"] = label
        if status is not None:
            one["status"] = status
        for name, value in props.items():
            if value is None:
                one["props"].pop(name, None)
            else:
                one["props"][name] = value
        one["at"] = self.now()

    def relate(self, kind: str, src: int, dst: int, **props: Any) -> int:
        rid = self.new_id()
        self.relations[rid] = {
            "id": rid, "kind": kind, "src": src, "dst": dst, "props": props, "at": self.now()
        }
        return rid

    def line(self, kind: str, src: int, dst: int) -> int:
        return next(
            rid
            for rid, one in self.relations.items()
            if (one["kind"], one["src"], one["dst"]) == (kind, src, dst)
        )

    def cut(self, rid: int) -> None:
        """선을 끊는다 — 무덤이 남는다(받는 쪽에 「끊긴 선」 으로 간다)."""
        one = self.relations.pop(rid)
        self.graves[rid] = {**one, "at": self.now()}

    def refs_to(self, oid: int) -> list[tuple[int, str]]:
        return [
            (one["id"], name)
            for one in self.objects.values()
            for name, value in one["props"].items()
            if self.is_ref(one["type"], name) and value == oid
        ]

    def is_ref(self, type_slug: str, name: str) -> bool:
        return any(
            p["key"] == name and p["data_type"] == "object_ref"
            for p in self.types[type_slug]["properties"]
        )

    def delete(self, oid: int) -> None:
        """「참조를 비우고」 지운다 — 선은 무덤을 남기고 끊기고, 가리키던 칸은 빈다."""
        for rid in [r for r, one in self.relations.items() if oid in (one["src"], one["dst"])]:
            self.cut(rid)
        for holder, name in self.refs_to(oid):
            self.edit(holder, **{name: None})
        self.objects[oid]["deleted"] = True
        self.objects[oid]["at"] = self.now()

    def delete_leaving_lines(self, oid: int) -> None:
        """선을 남긴 채 지운다(묶음 적재의 「사라진 것」 처럼) — 선은 **무덤 없이** 안 나간다."""
        self.objects[oid]["deleted"] = True
        self.objects[oid]["at"] = self.now()

    def merge(self, loser: int, winner: int) -> None:
        """합친다 — 진 쪽의 선은 이긴 쪽으로 옮겨 새로 나가고, 진 쪽 기준의 옛 선은 **무덤 없이** 사라진다."""
        for holder, name in self.refs_to(loser):
            self.edit(holder, **{name: winner})
        for rid, one in list(self.relations.items()):
            if loser not in (one["src"], one["dst"]):
                continue
            src = winner if one["src"] == loser else one["src"]
            dst = winner if one["dst"] == loser else one["dst"]
            twin = [
                r
                for r, other in self.relations.items()
                if r != rid and (other["kind"], other["src"], other["dst"]) == (one["kind"], src, dst)
            ]
            if twin:
                del self.relations[rid]
            else:
                one.update(src=src, dst=dst, at=self.now())
        self.objects[loser].update(deleted=True, merged_into=winner, at=self.now())

    def rename(self, oid: int, key: str) -> None:
        """식별자를 바꾼다 — 그 객체만 바뀐다. 선과 남의 참조 칸은 그대로(받는 쪽이 옮긴다)."""
        one = self.objects[oid]
        one["previous_keys"].append(one["key"])
        one["key"] = key
        one["at"] = self.now()

    def hide(self, oid: int) -> None:
        """소유 부서가 토큰이 못 보는 곳으로 옮겨 감 — `hidden` 무덤이 가고, 선은 말없이 빠진다."""
        self.objects[oid].update(hidden=True, at=self.now())

    def purge_graves(self) -> None:
        """끊긴 선의 기록을 지운다 — 그보다 앞선 since 로 물으면 reset 으로 답한다."""
        self.purged_before = self.now()
        self.graves = {rid: one for rid, one in self.graves.items() if one["at"] >= self.purged_before}

    # --- 읽기(받는 쪽이 보는 것) ------------------------------------------------------------

    def served(self, type_slug: str) -> bool:
        return self.types[type_slug]["open"] and type_slug not in self.closed_race

    def seen(self, one: dict[str, Any]) -> bool:
        return not one["deleted"] and not one["hidden"] and not one["silent"] and self.served(one["type"])

    def kind_open(self, kind: str) -> bool:
        spec = self.kinds[kind]
        return self.types[spec["src"]]["open"] and any(self.types[t]["open"] for t in spec["dst"])

    def render(self, one: dict[str, Any]) -> dict[str, Any]:
        # 봉투는 **늘 같은 칸**이다 — 실제 SP 처럼 값이 없어도 null · false · [] 로 싣는다.
        row: dict[str, Any] = {
            "key": one["key"],
            "label": one["label"],
            "status": one["status"],
            "updated_at": stamp(one["at"]),
            "deleted": False,
            "hidden": False,
            "merged_into": None,
            "renamed_from": one["previous_keys"][-1] if one["previous_keys"] else None,
            "previous_keys": list(one["previous_keys"]),
            "properties": {},
        }
        if one["hidden"] and not one["deleted"]:
            row.update(label=one["key"], deleted=True, hidden=True)
            return row
        if one["deleted"]:
            row["deleted"] = True
            if one["merged_into"]:
                row["merged_into"] = self.objects[one["merged_into"]]["key"]
            return row
        props: dict[str, Any] = {}
        for name, value in one["props"].items():
            if self.is_ref(one["type"], name):
                target = self.objects.get(value)
                if target is None or not self.seen(target):
                    continue  # 못 보는 상대는 참조로도 안 나간다
                value = target["key"]
            if value in ([], ""):
                continue
            props[name] = value
        row["properties"] = props
        return row

    def page(
        self, stream: list[tuple[int, int, dict[str, Any]]], cursor: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """(시각, 순번, 행) 흐름에서 한 쪽 — as_of 는 첫 쪽을 부른 때의 시계다."""
        as_of = self.tick
        after: tuple[int, int] | None = None
        if cursor:
            if self.cursor_lost_once:
                self.cursor_lost_once = False
                raise Fail(404, error_code("CORE", 2), "커서를 읽을 수 없습니다. 처음부터 받으세요.")
            try:
                mark = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                as_of, after = int(mark["as_of"]), (int(mark["at"]), int(mark["n"]))
            except Exception:
                raise Fail(404, error_code("CORE", 2), "커서를 읽을 수 없습니다. 처음부터 받으세요.") from None
        rows = sorted(
            (one for one in stream if one[0] <= as_of and (after is None or one[:2] > after)),
            key=lambda one: one[:2],
        )
        picked = rows[: min(limit, self.page_cap)]
        more = len(rows) > len(picked)
        cursor_out = None
        if more:
            last = picked[-1]
            cursor_out = base64.urlsafe_b64encode(
                json.dumps({"as_of": as_of, "at": last[0], "n": last[1]}).encode()
            ).decode()
        return [one[2] for one in picked], as_of, cursor_out

    def rows(self, type_slug: str, since: int | None, cursor: str | None, limit: int) -> dict[str, Any]:
        self.require(type_slug)
        stream = []
        for one in self.objects.values():
            if one["type"] != type_slug or one["silent"]:
                continue
            if since is None:
                if not self.seen(one):
                    continue  # 처음 받는 쪽에는 무덤을 안 보낸다
            elif one["at"] <= since:
                continue
            stream.append((one["at"], one["id"], self.render(one)))
        items, as_of, nxt = self.page(stream, cursor, limit)
        return {
            "type_slug": type_slug,
            "as_of": None if nxt else stamp(as_of),
            "since": stamp(since) if since is not None else None,
            "next": nxt,
            "items": items,
        }

    def relation_rows(self, type_slug: str, since: int | None, cursor: str | None, limit: int) -> dict[str, Any]:
        self.require(type_slug)
        if since is not None and since < self.purged_before:
            return {
                "type_slug": type_slug,
                "as_of": None,
                "since": stamp(since),
                "next": None,
                "items": [],
                "reset": True,
                "reset_reason": "90일보다 오래된 시점부터는 끊긴 선을 알려 줄 수 없습니다 — "
                "`since` 를 비우고 처음부터 받으세요.",
            }
        stream = []
        for rid, one in self.relations.items():
            src, dst = self.objects[one["src"]], self.objects[one["dst"]]
            if src["type"] != type_slug or not self.seen(src) or not self.seen(dst):
                continue  # 양 끝이 살아 있고 보이는 것만
            if dst["type"] not in self.kinds[one["kind"]]["dst"]:
                continue
            if since is not None and one["at"] <= since:
                continue
            stream.append((one["at"], rid, self.edge(one, src, dst, deleted=False)))
        if since is not None:
            for rid, one in self.graves.items():
                src, dst = self.objects[one["src"]], self.objects[one["dst"]]
                if src["type"] != type_slug or not self.served(dst["type"]) or one["at"] <= since:
                    continue
                stream.append((one["at"], rid, self.edge(one, src, dst, deleted=True)))
        items, as_of, nxt = self.page(stream, cursor, limit)
        return {
            "type_slug": type_slug,
            "as_of": None if nxt else stamp(as_of),
            "since": stamp(since) if since is not None else None,
            "next": nxt,
            "items": items,
            "reset": False,
            "reset_reason": "",
        }

    def edge(self, one: dict[str, Any], src: dict[str, Any], dst: dict[str, Any], *, deleted: bool) -> dict[str, Any]:
        return {
            "src": src["key"],
            "relation": one["kind"],
            "dst": dst["key"],
            "dst_type": dst["type"],
            "evidence_note": "",
            "properties": {} if deleted else dict(one["props"]),
            "updated_at": stamp(one["at"]),
            "deleted": deleted,
        }

    def require(self, type_slug: str) -> None:
        if type_slug not in self.types or not self.served(type_slug):
            raise Fail(
                404,
                error_code("CORE", 1),
                f"열려 있는 코어 타입이 아닙니다: {type_slug}. 「GET /api/core」 로 목록을 보세요.",
            )

    def catalog(self, base: str) -> dict[str, Any]:
        listed = [slug for slug, spec in self.types.items() if spec["open"]]
        types = []
        for slug in listed:
            kinds = [k for k, spec in self.kinds.items() if spec["src"] == slug and self.kind_open(k)]
            alive = [one for one in self.objects.values() if one["type"] == slug and self.seen(one)]
            types.append(
                {
                    "slug": slug,
                    "label": self.types[slug]["label"],
                    "description": "",
                    "count": len(alive),
                    "updated_at": stamp(max([one["at"] for one in alive] or [0])),
                    "endpoint": f"{base}/core/{slug}",
                    "relations_endpoint": f"{base}/core/{slug}/relations",
                    "relations": kinds,
                    "relation_kinds": [self.kind_entry(k) for k in kinds],
                    "properties": self.types[slug]["properties"],
                }
            )
        shape = [
            (t["slug"], t["properties"], t["relations"], t["relation_kinds"]) for t in types
        ]
        digest = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()
        return {
            "system": "sp-testbed",
            "revision": f"{len(types)}-{int(digest[:8], 16) % 10**10:010d}",
            "as_of": stamp(self.tick),
            "types": types,
        }

    def kind_entry(self, kind: str) -> dict[str, Any]:
        spec = self.kinds[kind]
        dst_types = [t for t in spec["dst"] if self.types[t]["open"]]
        end = (
            {"slug": spec["interface"], "interface": True, "types": dst_types}
            if spec["interface"]
            else {"slug": spec["dst"][0], "interface": False, "types": dst_types}
        )
        return {
            "slug": kind,
            "label": spec["label"],
            "src": [{"slug": spec["src"], "interface": False, "types": [spec["src"]]}],
            "dst": [end],
        }

    def expected(self) -> dict[str, Any]:
        """지금 받는 쪽이 가져야 할 것 — 살아 있는 객체, 이어진 선, 옛 식별자."""
        if self.frozen is not None:
            return self.frozen
        objects: dict[str, list[dict[str, Any]]] = {}
        previous: dict[str, dict[str, str]] = {}
        for one in self.objects.values():
            if not self.served(one["type"]):
                continue
            for old in one["previous_keys"]:
                previous.setdefault(one["type"], {})[old] = one["key"]
            if self.seen(one):
                row = self.render(one)
                objects.setdefault(one["type"], []).append(
                    {k: row[k] for k in ("key", "label", "status", "properties")}
                )
        relations = []
        for one in self.relations.values():
            src, dst = self.objects[one["src"]], self.objects[one["dst"]]
            if self.seen(src) and self.seen(dst) and dst["type"] in self.kinds[one["kind"]]["dst"]:
                relations.append([src["type"], src["key"], one["kind"], dst["key"], dst["type"]])
        return {
            "step": self.step,
            "objects": objects,
            "relations": sorted(relations),
            "previous_keys": previous,
            "ignore_types": sorted(self.closed_race),
            "ref_properties": {
                slug: {
                    p["key"]: p["ref_type_slug"]
                    for p in spec["properties"]
                    if p["data_type"] == "object_ref"
                }
                for slug, spec in self.types.items()
            },
        }

    # --- 단계 -------------------------------------------------------------------------------

    def advance(self) -> dict[str, Any]:
        if self.step >= len(STEPS):
            raise Fail(409, "TESTBED-END", "마지막 단계까지 끝났습니다 — POST /_restart 로 처음부터.")
        self.step += 1
        title, run, status, rules, play = STEPS[self.step - 1]
        notes = play(self)
        self.info = {
            "step": self.step,
            "last_step": len(STEPS),
            "title": title,
            "what_happened": notes,
            "run": run,
            "expect_status": status,
            "rules": rules,
        }
        return self.info


def step_1(w: World) -> list[str]:
    w.add_type(
        "vendor",
        "공급사",
        [
            {"key": "country", "label": "국가", "data_type": "text"},
            {"key": "grade", "label": "등급", "data_type": "enum", "enum_options": ["A", "B", "C"]},
        ],
        open=True,
    )
    w.add_type(
        "part",
        "부품",
        [
            {"key": "vendor", "label": "주 공급사", "data_type": "object_ref", "ref_type_slug": "vendor", "multi": False},
            {"key": "weight", "label": "무게", "data_type": "number", "unit": "kg"},
            {"key": "tags", "label": "꼬리표", "data_type": "text", "multi": True},
            {"key": "released", "label": "출시일", "data_type": "date"},
        ],
        open=True,
    )
    w.add_type("plant", "공장", [{"key": "region", "label": "지역", "data_type": "text"}], open=False)
    w.add_kind("supplied_by", "공급처", "part", ["vendor"])
    w.add_kind("made_at", "생산지", "part", ["plant"])
    w.add_kind("inspected_by", "검사처", "part", ["vendor", "plant"], interface="inspector")
    v = {
        key: w.make("vendor", key, label, country=country, grade=grade)
        for key, label, country, grade in [
            ("V-1", "ACME", "KR", "A"),
            ("V-2", "한화", "KR", "B"),
            ("V-3", "ACME Inc.", "US", "A"),
            ("V-4", "대성", "KR", "C"),
            ("V-5", "미래", "JP", "B"),
            ("K-1", "공용검사소", "KR", "A"),
        ]
    }
    pl = w.make("plant", "PL-1", "구미", region="경북")
    pk = w.make("plant", "K-1", "구미 검사동", region="경북")
    p1 = w.make("part", "P-1", "볼트", vendor=v["V-1"], weight=1.5, tags=["M8", "강"], released="2026-01-10")
    p2 = w.make("part", "P-2", "너트", vendor=v["V-2"], weight=0.8, tags=["M8"])
    p3 = w.make("part", "P-3", "와셔", vendor=v["V-3"], weight=0.2)
    p4 = w.make("part", "P-4", "핀", vendor=v["V-4"], tags=["소형"])
    p5 = w.make("part", "P-5", "스프링", vendor=v["V-5"], weight=0.5)
    w.relate("supplied_by", p1, v["V-1"], n=3)
    w.relate("supplied_by", p1, v["V-3"])
    w.relate("supplied_by", p2, v["V-2"])
    w.relate("supplied_by", p3, v["V-3"])
    w.relate("supplied_by", p4, v["V-4"])
    w.relate("supplied_by", p5, v["V-5"])
    w.relate("supplied_by", p5, v["V-2"])
    w.relate("inspected_by", p1, v["K-1"])
    w.relate("inspected_by", p1, pk)
    w.relate("made_at", p1, pl)
    w.relate("made_at", p2, pl)
    return [
        "공개 타입 둘(vendor 6건 · part 5건)과 선 8개(supplied_by 7 · inspected_by 1).",
        "plant 타입은 아직 공개 전이다 — 카탈로그에 없고, plant 로 가는 선도 안 나간다.",
        f"한 쪽에 {w.page_cap}건씩만 준다 — next 가 빌 때까지 이어 받아야 다 받는다.",
    ]


def step_2(w: World) -> list[str]:
    v1, v2, v3 = w.find("vendor", "V-1"), w.find("vendor", "V-2"), w.find("vendor", "V-3")
    v4, v5 = w.find("vendor", "V-4"), w.find("vendor", "V-5")
    p2, p4, p5 = w.find("part", "P-2"), w.find("part", "P-4"), w.find("part", "P-5")
    w.edit(v1, label="ACME Korea")
    w.edit(p2, weight=2.0)
    w.edit(p4, tags=None)
    w.edit(v4, status="deprecated")
    w.delete(v5)
    w.merge(v3, v1)
    w.cut(w.line("supplied_by", p5, v2))
    v6 = w.make("vendor", "V-6", "신규", country="KR", grade="A")
    p6 = w.make("part", "P-6", "캡", vendor=v6, weight=0.1)
    w.relate("supplied_by", p6, v6)
    return [
        "V-1 이름 바뀜 · P-2 무게 바뀜 · P-4 꼬리표 비움(칸이 아예 안 온다) · V-4 사용 중지(deprecated).",
        "V-5 지움(그 선은 「끊긴 선」 으로 오고, P-5 의 공급사 칸은 빈다).",
        "V-3 를 V-1 에 합침 — V-3 는 merged_into V-1 무덤. P-3→V-3 선은 P-3→V-1 로 옮겨 새로 오고, "
        "P-1→V-3 는 P-1→V-1 과 겹쳐 **무덤 없이** 사라진다.",
        "P-5→V-2 선 끊음 · V-6 · P-6 새로 생김.",
    ]


def step_3(w: World) -> list[str]:
    v2, v6 = w.find("vendor", "V-2"), w.find("vendor", "V-6")
    p1, p4 = w.find("part", "P-1"), w.find("part", "P-4")
    w.rename(v2, "V-2a")
    w.rename(v2, "V-2b")
    w.rename(p1, "P-1X")
    w.hide(v6)
    w.delete_leaving_lines(p4)
    return [
        "V-2 의 식별자가 두 번 바뀜(V-2 → V-2a → V-2b) — 행은 renamed_from V-2a · previous_keys "
        "[V-2, V-2a] 로 온다. 받는 쪽이 가진 것은 V-2 다.",
        "P-1 → P-1X. 선과 다른 행의 참조 칸은 **다시 오지 않는다** — 받는 쪽이 옮기거나 풀어야 한다.",
        "V-6 이 안 보이게 됨(hidden) — 이름 · 칸 없이 무덤만 온다. 그 선은 말없이 빠진다.",
        "P-4 지움(선을 남긴 채) — P-4→V-4 선은 「끊긴 선」 으로 **오지 않는다.**",
    ]


def step_4(w: World) -> list[str]:
    v1, v2 = w.find("vendor", "V-1"), w.find("vendor", "V-2b")
    p5 = w.find("part", "P-5")
    v5 = w.make("vendor", "V-5", "미래(재등록)", country="JP", grade="B")
    w.edit(p5, vendor=v5)
    w.relate("supplied_by", p5, v5)
    for n in range(10, 22):
        part = w.make("part", f"P-{n}", f"부품 {n}", vendor=v1 if n % 2 else v2, weight=float(n))
        w.relate("supplied_by", part, v1 if n % 2 else v2)
    w.cursor_lost_once = True
    return [
        "지웠던 V-5 가 같은 식별자로 다시 생김(되살림) — P-5 가 다시 V-5 를 가리킨다.",
        "부품 12개(P-10 ~ P-21)와 그 선이 생김 — 여러 쪽에 걸친다.",
        "이번 단계에서 커서를 싣고 부르는 첫 요청은 404 · -CORE-0002(커서를 읽을 수 없음)로 답한다 — "
        "커서 없이 같은 since 로 다시 받아야 한다(멈추면 안 된다).",
    ]


def step_5(w: World) -> list[str]:
    v1 = w.find("vendor", "V-1")
    p3, p10 = w.find("part", "P-3"), w.find("part", "P-10")
    w.cut(w.line("supplied_by", p3, v1))
    w.delete(p10)
    w.purge_graves()
    return [
        "P-3→V-1 선을 끊고 P-10 을 지운 뒤, 끊긴 선의 기록을 정리함 — 받는 쪽이 「오래 안 받은」 상태가 됐다.",
        "선을 지난 since 로 물으면 reset: true 가 온다 — since 를 비우고 처음부터 받고, "
        "끝까지 받은 뒤 이번에 안 온 선을 끊어야 한다.",
    ]


def step_6(w: World) -> list[str]:
    w.types["plant"]["open"] = True
    return [
        "plant 타입을 공개함 — 정의 판(revision)이 바뀐다.",
        "plant 로 가는 선(made_at 2개 · inspected_by 1개)은 **예전에 만든 것**이라 since 로는 안 온다 — "
        "정의 판이 바뀌면 처음부터 다시 받아야 받는다.",
        "inspected_by 는 끝이 인터페이스다 — P-1X→K-1 이 vendor 와 plant 에 하나씩, 두 선이다 "
        "(key 는 타입 안에서만 하나).",
    ]


def step_7(w: World) -> list[str]:
    w.closed_race = {"plant"}
    w.edit(w.find("vendor", "V-1"), label="ACME KR")
    return [
        "카탈로그를 읽은 직후 plant 의 공개가 거두어진 상황 — 카탈로그에는 있지만 plant 를 부르면 "
        "404 · -CORE-0001 이다. plant 만 건너뛰고 나머지는 받고, 「확인 필요」 로 알려야 한다.",
        "V-1 이름 바뀜(ACME KR).",
    ]


def step_8(w: World) -> list[str]:
    w.closed_race = set()
    w.frozen = w.expected()
    keep = {"P-1X", "P-2"}
    for one in w.objects.values():
        if one["type"] == "part" and one["key"] not in keep:
            one["silent"] = True
    return [
        "토큰 주인의 소속이 줄어 부품 대부분이 **표식 없이** 안 보이게 된 상황.",
        "처음부터 다시 받기로 돌리면 가진 것의 절반 넘게 안 온다 — 정리(비활성 · 선 끊기)를 하지 말고 "
        "「확인 필요」 로 알려야 한다. 정답은 이 단계 전과 같다.",
    ]


#: (제목, 실행 방법, 기대하는 결과 상태, 보는 규칙, 일으키는 것)
STEPS = [
    ("첫 적재", "보통", "정상", "1 · 2 · 3 · 6 · 15", step_1),
    ("고침 · 지움 · 합침 · 끊음", "보통", "정상", "4 · 6 · 10 · 13", step_2),
    ("식별자 두 번 바뀜 · 안 보이게 됨 · 선을 남긴 삭제", "보통", "정상", "4 · 5 · 10", step_3),
    ("되살림 · 여러 쪽 · 커서 오류", "보통", "정상", "2 · 8 · 11", step_4),
    ("오래 안 받음 — reset", "보통", "정상", "7 · 9", step_5),
    ("정의 판 바뀜 — 타입 공개", "보통", "정상", "12 · 15", step_6),
    ("공개 거둠 — 그 타입만 건너뜀", "보통", "확인 필요", "11 · 16", step_7),
    ("토큰 범위 줄어듦 — 처음부터 다시 받기", "처음부터", "확인 필요", "7 · 16", step_8),
]


class Handler(BaseHTTPRequestHandler):
    world: World
    lock: threading.Lock
    quiet = False

    def do_GET(self) -> None:  # noqa: N802
        self.answer("GET")

    def do_POST(self) -> None:  # noqa: N802
        self.answer("POST")

    def answer(self, method: str) -> None:
        url = urlparse(self.path)
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        path = url.path.rstrip("/") or "/"
        try:
            with self.lock:
                status, body = self.route(method, path, query)
        except Fail as failed:
            status, body = failed.status, failed.body
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def route(self, method: str, path: str, query: dict[str, str]) -> tuple[int, Any]:
        w = self.world
        if path == "/_status" and method == "GET":
            return 200, w.info
        if path == "/_expected" and method == "GET":
            return 200, w.expected()
        if path == "/_step" and method == "POST":
            return 200, w.advance()
        if path == "/_restart" and method == "POST":
            type(self).world = World(w.page_cap)
            return 200, type(self).world.info
        if not path.startswith("/api/"):
            return 404, {"detail": "Not Found"}
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            raise Fail(401, error_code("AUTH", 1), "인증이 필요합니다 — Authorization: Bearer <토큰>")
        if method != "GET":
            return 405, {"detail": "Method Not Allowed"}
        parts = path.split("/")[2:]  # ["core", <타입>, "relations"]
        if parts == ["core"]:
            return 200, w.catalog(f"http://{self.headers.get('Host', '127.0.0.1')}/api")
        if len(parts) not in (2, 3) or parts[0] != "core" or (len(parts) == 3 and parts[2] != "relations"):
            return 404, {"detail": "Not Found"}
        since = self.since(query.get("since"))
        try:
            limit = int(query.get("limit", "500"))
        except ValueError:
            raise Fail(422, error_code("REQUEST", 1), "limit 은 1 ~ 2000 의 정수입니다") from None
        if not 1 <= limit <= 2000:
            raise Fail(422, error_code("REQUEST", 1), "limit 은 1 ~ 2000 의 정수입니다")
        cursor = query.get("cursor") or None
        if len(parts) == 2:
            return 200, w.rows(parts[1], since, cursor, limit)
        return 200, w.relation_rows(parts[1], since, cursor, limit)

    @staticmethod
    def since(text: str | None) -> int | None:
        if not text:
            return None
        try:
            return tick_of(text)
        except ValueError:
            raise Fail(
                422, error_code("REQUEST", 1), f"since 를 읽을 수 없습니다: {text} — 지난 응답의 as_of 를 그대로 넣으세요"
            ) from None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if not self.quiet:
            sys.stderr.write(f"[fake_sp] {self.command} {self.path} → {args[1] if len(args) > 1 else ''}\n")


def serve(port: int = 8765, page_cap: int = PAGE_CAP, *, quiet: bool = False) -> ThreadingHTTPServer:
    """서버를 만든다(돌리지는 않는다) — 시험에서 스레드로 띄울 때 쓴다. port=0 이면 빈 포트."""
    handler = type("BoundHandler", (Handler,), {"world": World(page_cap), "lock": threading.Lock(), "quiet": quiet})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="가짜 SP 코어 창구(시험장)")
    parser.add_argument("--port", type=int, default=8765, help="0 이면 빈 포트 — 잡힌 포트를 첫 줄에 찍는다")
    parser.add_argument("--page-cap", type=int, default=PAGE_CAP, help="한 쪽에 싣는 최대 행 수")
    parser.add_argument("--quiet", action="store_true", help="요청 기록을 찍지 않는다")
    args = parser.parse_args()
    server = serve(args.port, args.page_cap, quiet=args.quiet)
    port = server.server_address[1]
    # 첫 줄은 기계가 읽는다 — 자동 시험이 빈 포트(--port 0)로 띄우고 이 줄에서 포트를 얻는다.
    print(f"PORT={port}", flush=True)
    print(f"가짜 SP: http://127.0.0.1:{port}/api · 토큰 {TOKEN} · 단계 {len(STEPS)}개", flush=True)
    print("다음 단계: POST /_step · 정답: GET /_expected · 지금 단계: GET /_status", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
