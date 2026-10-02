#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Фрагмент ГОСТ без чтения файла целиком.

Корпус облачного агента (каталог corpus/ рядом со скриптом):
  python q.py toc|find|get|slice

Канон v7 в корне репозитория:
  python q.py --v7 toc|find|get|slice
  python scripts/gost_query.py toc|find|get|slice
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

GET_CHARS = 3200
GET_IDS = 4
FIND_HITS = 8
SNIP_CHARS = 220
SLICE_LINES = 80
SLICE_CHARS = 6000
TOC_LINES = 200

HEAD_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
HEAD_ID_RE = re.compile(r"^((?:\d{1,2}|[АБВГД])(?:\.\d+)*)\s+(.+)$")
BODY_ID_RE = re.compile(r"^((?:\d{1,2}|[АБВГД])(?:\.\d+)+)\s+(.+)$")
WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё-]{3,}")
ID_TOKEN_RE = re.compile(r"(?:[АБВГДабвгд]\.\d+(?:\.\d+)*|\d+(?:\.\d+)+)")

LETTER_FILE = {"А": "a", "Б": "b", "В": "v", "Г": "g", "Д": "d"}

AGENT = Path(__file__).resolve().parent


@dataclass
class Node:
    kind: str
    level: int
    cid: str
    title: str
    start: int
    end: int


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def norm_id(raw: str) -> str:
    cid = raw.strip().strip("«»\"'").rstrip(".")
    if cid[:1] in "абвгд":
        cid = cid[:1].upper() + cid[1:]
    return cid


def newest(root: Path, pattern: str) -> Path:
    hits = [p for p in root.glob(pattern) if p.is_file()]
    if not hits:
        raise SystemExit(f"нет файла: {root.name}/{pattern}")
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0]


def file_map(v7: bool) -> dict[str, Path]:
    if not v7:
        corpus = AGENT / "corpus"
        return {
            "core": corpus / "GOST_URIP_v7.md",
            "a": corpus / "Prilozhenie_A.md",
            "b": corpus / "Prilozhenie_B.md",
            "v": corpus / "Prilozhenie_V.md",
            "g": corpus / "Prilozhenie_G.md",
            "d": corpus / "Prilozhenie_D.md",
        }
    root = AGENT.parent
    found = {
        "core": newest(root, "ГОСТ_Устойчивое_развитие_исторических_поселений_v7*.md"),
        "a": newest(root, "Приложение_А_*.md"),
        "b": newest(root, "Приложение_Б_*.md"),
        "v": newest(root, "Приложение_В_*.md"),
        "g": newest(root, "Приложение_Г_*.md"),
        "d": newest(root, "Приложение_Д_*.md"),
    }
    svc = [p for p in root.glob("Служебные_материалы_*.md") if p.is_file()]
    if svc:
        svc.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        found["svc"] = svc[0]
    return found


def load_lines(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"нет файла: {path.name}")
    return path.read_text(encoding="utf-8").splitlines()


def parse(lines: list[str]) -> list[Node]:
    marks: list[tuple[str, int, str, str, int]] = []
    for i, line in enumerate(lines):
        head = HEAD_RE.match(line)
        if head:
            level = len(head.group(1))
            title_full = head.group(2).strip()
            ident = HEAD_ID_RE.match(title_full)
            if ident:
                marks.append(("head", level, ident.group(1), ident.group(2).strip(), i))
            else:
                marks.append(("head", level, "", title_full, i))
            continue
        body = BODY_ID_RE.match(line)
        if body:
            marks.append(("leaf", 9, body.group(1), body.group(2).strip(), i))
    nodes: list[Node] = []
    last = len(lines) - 1
    for idx, (kind, level, cid, title, start) in enumerate(marks):
        end = last
        if kind == "leaf":
            if idx + 1 < len(marks):
                end = marks[idx + 1][4] - 1
        else:
            for kind2, level2, _cid2, _title2, start2 in marks[idx + 1 :]:
                if kind2 == "head" and level2 <= level:
                    end = start2 - 1
                    break
        if end < start:
            end = start
        nodes.append(Node(kind, level, cid, title, start, end))
    return nodes


def best_by_id(nodes: list[Node]) -> dict[str, Node]:
    best: dict[str, Node] = {}
    for node in nodes:
        if not node.cid:
            continue
        prev = best.get(node.cid)
        span = node.end - node.start
        if prev is None or span > (prev.end - prev.start) or (
            span == (prev.end - prev.start) and node.start > prev.start
        ):
            best[node.cid] = node
    return best


def text_of(lines: list[str], node: Node) -> str:
    return "\n".join(lines[node.start : node.end + 1]).strip()


def clip(text: str, limit: int, hint: str) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut < int(limit * 0.6):
        dot = text.rfind(". ", 0, limit)
        cut = dot + 1 if dot >= int(limit * 0.6) else limit
    hidden = len(text) - cut
    return text[:cut].rstrip() + f"\n…[{hidden} знаков скрыто. {hint}]"


def has_term(hay: str, term: str) -> bool:
    return re.search(rf"(?<![0-9a-zа-яё-]){re.escape(term)}", hay) is not None


def own_text(lines: list[str], node: Node, nxt: int | None) -> str:
    end = node.end if nxt is None else min(node.end, nxt - 1)
    if end < node.start:
        end = node.start
    return "\n".join(lines[node.start : end + 1]).strip()


def file_for_id(cid: str, files: dict[str, Path]) -> str:
    key = LETTER_FILE.get(cid[:1], "core")
    if key not in files:
        raise SystemExit(f"нет файла для {cid}")
    return key


def immediate_children(nodes: list[Node], cid: str) -> list[str]:
    prefix = cid + "."
    depth = cid.count(".") + 1
    out: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        if node.kind != "head" or not node.cid.startswith(prefix):
            continue
        if node.cid.count(".") != depth or node.cid in seen:
            continue
        seen.add(node.cid)
        out.append(node.cid)
    return out


def cmd_toc(files: dict[str, Path], key: str, deep: bool) -> None:
    if key == "all":
        keys = [k for k in ("core", "a", "b", "v", "g", "d") if k in files]
    else:
        if key not in files:
            raise SystemExit(f"неизвестный файл: {key}. Ключи: {', '.join(files)}")
        keys = [key]
    shown = 0
    for key in keys:
        lines = load_lines(files[key])
        nodes = [n for n in best_by_id(parse(lines)).values() if n.kind == "head"]
        nodes.sort(key=lambda n: n.start)
        print(f"# {key} {files[key].name}")
        for node in nodes:
            if not deep and node.level > 3:
                continue
            if shown >= TOC_LINES:
                print(f"…[оглавление обрезано на {TOC_LINES}]")
                return
            title = node.title
            if len(title) > 140:
                title = title[:137] + "..."
            print(f"{node.cid}\t{title}")
            shown += 1
    if not deep:
        print("# без пунктов 4-го уровня. Полное оглавление: toc ФАЙЛ --deep")


def cmd_get(files: dict[str, Path], ids: list[str]) -> None:
    wanted = [norm_id(x) for x in ids if norm_id(x)][:GET_IDS]
    if not wanted:
        raise SystemExit("укажите номер пункта, например 4.2.3 или Б.1.1.1")
    if len(ids) > GET_IDS:
        print(f"# взяты первые {GET_IDS} номера")
    cache: dict[str, tuple[list[str], list[Node], dict[str, Node]]] = {}
    for cid in wanted:
        key = file_for_id(cid, files)
        if key not in cache:
            lines = load_lines(files[key])
            nodes = parse(lines)
            cache[key] = (lines, nodes, best_by_id(nodes))
        lines, nodes, by_id = cache[key]
        node = by_id.get(cid)
        if node is None:
            print(f"# нет пункта {cid}")
            continue
        body = text_of(lines, node)
        kids = immediate_children(nodes, cid)
        hint = "сузьте номер"
        if kids:
            hint = "дочерние: " + ", ".join(kids[:12])
        print(f"# {key} {cid} L{node.start + 1}-{node.end + 1}")
        print(clip(body, GET_CHARS, hint))
        print()


def snippet(text: str, needle: str) -> str:
    hay = norm(text)
    at = hay.find(needle) if needle else 0
    if at < 0:
        at = 0
    lo = max(0, at - 40)
    chunk = text[lo : lo + SNIP_CHARS].replace("\n", " ")
    chunk = re.sub(r"\s+", " ", chunk).strip()
    if lo > 0:
        chunk = "…" + chunk
    if lo + SNIP_CHARS < len(text):
        chunk = chunk + "…"
    return chunk


def cmd_find(files: dict[str, Path], query: str, only: str) -> None:
    query = query.strip()
    if not query:
        raise SystemExit("пустой запрос")
    ident = ID_TOKEN_RE.fullmatch(query.strip())
    if ident:
        cmd_get(files, [query])
        return
    terms = [norm(w) for w in WORD_RE.findall(query)]
    if not terms:
        raise SystemExit("в запросе нет слов длиннее 2 букв")
    keys = [only] if only else [k for k in ("core", "a", "b", "v", "g", "d") if k in files]
    if only and only not in files:
        raise SystemExit(f"неизвестный файл: {only}")
    hits: list[tuple[int, int, int, str, Node, str]] = []
    for key in keys:
        lines = load_lines(files[key])
        ordered = parse(lines)
        chosen = best_by_id(ordered)
        for i, node in enumerate(ordered):
            if node.cid and chosen.get(node.cid) is not node:
                continue
            nxt = ordered[i + 1].start if i + 1 < len(ordered) else None
            body = own_text(lines, node, nxt)
            if not body:
                continue
            title_n = norm(node.title)
            body_n = norm(body[:4000])
            matched = 0
            score = 0
            first = ""
            for term in terms:
                in_title = has_term(title_n, term)
                in_body = has_term(body_n, term)
                if not in_title and not in_body:
                    continue
                matched += 1
                score += 8 if in_title else 3
                first = first or term
            if not matched:
                continue
            if matched == len(terms):
                score += 20
            span = max(0, (nxt - 1 if nxt else node.end) - node.start)
            score -= min(6, span // 6)
            hits.append((score, matched, span, key, node, snippet(body, first)))
    full = [item for item in hits if item[1] == len(terms)]
    pool = full or hits
    pool.sort(key=lambda item: (-item[0], item[2], item[3], item[4].start))
    if not pool:
        print("# ничего")
        return
    if not full and len(terms) > 1:
        print("# нет фрагмента со всеми словами; ниже частичные")
    print(f"# {min(len(pool), FIND_HITS)} из {len(pool)}")
    for _score, _matched, _span, key, node, snip in pool[:FIND_HITS]:
        if node.kind == "leaf":
            print(f"{key}\t{node.cid}")
        else:
            title = node.title if len(node.title) <= 120 else node.title[:117] + "..."
            print(f"{key}\t{node.cid}\t{title}")
        print(snip)
        print()


def cmd_slice(files: dict[str, Path], key: str, start: int, count: int) -> None:
    if key not in files:
        raise SystemExit(f"неизвестный файл: {key}. Ключи: {', '.join(files)}")
    if start < 1:
        start = 1
    count = count if count > 0 else 40
    count = min(count, SLICE_LINES)
    lines = load_lines(files[key])
    chunk = lines[start - 1 : start - 1 + count]
    print(f"# {key} L{start}-{start + len(chunk) - 1} / {len(lines)}")
    out: list[str] = []
    used = 0
    for offset, line in enumerate(chunk):
        row = f"{start + offset}|{line}"
        if used + len(row) > SLICE_CHARS:
            print(f"…[лимит {SLICE_CHARS} знаков. Сузьте n]")
            break
        out.append(row)
        used += len(row) + 1
    print("\n".join(out))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="q.py")
    parser.add_argument("--v7", action="store_true", help="канон v7 в корне, не corpus/")
    sub = parser.add_subparsers(dest="cmd", required=True)

    toc = sub.add_parser("toc", help="оглавление: номер и заголовок")
    toc.add_argument("file", nargs="?", default="core")
    toc.add_argument("--deep", action="store_true", help="включая заголовки 4-го уровня")

    find = sub.add_parser("find", help="поиск, до 8 коротких попаданий")
    find.add_argument("query")
    find.add_argument("--file", default="")

    get = sub.add_parser("get", help="текст пунктов")
    get.add_argument("ids", nargs="+")

    sl = sub.add_parser("slice", help="окно строк")
    sl.add_argument("file")
    sl.add_argument("start", type=int)
    sl.add_argument("n", type=int, nargs="?", default=40)
    return parser


def main(argv: list[str] | None = None) -> None:
    configure_stdio()
    args = build_parser().parse_args(argv)
    files = file_map(args.v7)
    print("# v7" if args.v7 else "# corpus")
    if args.cmd == "toc":
        cmd_toc(files, args.file, args.deep)
    elif args.cmd == "find":
        cmd_find(files, args.query, args.file)
    elif args.cmd == "get":
        cmd_get(files, args.ids)
    elif args.cmd == "slice":
        cmd_slice(files, args.file, args.start, args.n)
    else:
        raise SystemExit(f"неизвестная команда: {args.cmd}")


if __name__ == "__main__":
    main()
