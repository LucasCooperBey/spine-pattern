#!/usr/bin/env python3
"""Walk spine JSONL files and report hash-lineage state: chained, legacy, broken.

Remembers each file's head hash, row count, and tail_incomplete so a later run
can see a rewrite, a dropped row, a missing file, or a torn tail that is still there.

One scheme only: row_hash and prev_row_hash. Exit 0 when nothing is broken and
no alarm fired. Exit 1 otherwise. The last line is always:

    spine-verify: state=<did_work|no_work|could_not> files=<n> chained=<n> legacy=<n> broken=<n>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import spine_core

_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", "venv", ".venv",
})
_LEDGER_NAMES = ("nodes.jsonl", "edges.jsonl")


def report_path(path: str) -> str:
    """Path to print. Relative to the working directory when that stays inside it."""
    absolute = os.path.abspath(path)
    relative = os.path.relpath(absolute, os.getcwd())
    if relative == "..":
        return absolute
    if relative.startswith(".." + os.sep):
        return absolute
    return relative


def discover_files(raw_paths: list) -> list:
    """Find nodes.jsonl and edges.jsonl under each directory. A file is taken as itself."""
    found = []
    seen = set()
    for raw in raw_paths:
        path = os.path.abspath(raw)
        if os.path.isfile(path):
            if path not in seen:
                seen.add(path)
                found.append(path)
            continue
        if not os.path.isdir(path):
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [
                name for name in dirnames
                if not name.startswith(".") and name not in _SKIP_DIRS
            ]
            for name in _LEDGER_NAMES:
                if name in filenames:
                    full = os.path.join(dirpath, name)
                    if full not in seen:
                        seen.add(full)
                        found.append(full)
    found.sort()
    return found


def _empty_result(prev_head) -> dict:
    return {
        "chained": 0,
        "legacy": 0,
        "broken": 0,
        "broken_lines": [],
        "head": None,
        "rows": 0,
        "tail_incomplete": 0,
        "head_present": not prev_head,
    }


def verify_file(path: str, prev_head: str | None = None) -> dict:
    """Stream one JSONL file. Return chained, legacy, and broken counts.

    An unterminated final line sets tail_incomplete to its byte length and is
    not broken when it does not parse. A row with no hash after a chained row
    is broken. A row with no hash before the first chained row is legacy.
    The remembered head matches only a parsed row's row_hash, not a substring.
    """
    chained = 0
    legacy = 0
    broken = 0
    broken_lines = []
    pred_state = "start"
    pred_hash = None
    tail_incomplete = 0
    head = None
    head_present = not prev_head

    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        return _empty_result(prev_head)
    with fh:
        line_no = 0
        while True:
            raw_line = fh.readline()
            if not raw_line:
                break
            line_no += 1
            unterminated = not raw_line.endswith(b"\n")
            if unterminated:
                tail_incomplete = len(raw_line)
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                if unterminated:
                    continue
                broken += 1
                broken_lines.append(line_no)
                pred_state = "legacy"
                pred_hash = None
                continue
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                if unterminated:
                    continue
                broken += 1
                broken_lines.append(line_no)
                pred_state = "legacy"
                pred_hash = None
                continue
            if not isinstance(obj, dict):
                if unterminated:
                    continue
                broken += 1
                broken_lines.append(line_no)
                pred_state = "legacy"
                pred_hash = None
                continue

            got_hash, got_prev = spine_core.chain_stamp(obj)
            if (isinstance(got_hash, str) and got_hash
                    and prev_head and got_hash == prev_head):
                head_present = True
            if got_hash:
                head = got_hash
            if got_hash is None and got_prev is None:
                # A hashless row after the chain started was stripped, or a
                # writer appended raw JSON onto a chained tail.
                if pred_state == "hash":
                    broken += 1
                    broken_lines.append(line_no)
                    continue
                legacy += 1
                pred_state = "legacy"
                pred_hash = None
                continue

            if pred_state == "start":
                expected_prev = "genesis"
            elif pred_state == "hash":
                expected_prev = pred_hash
            else:
                expected_prev = "legacy"

            recomputed = spine_core.canonical_hash(obj)
            bad = got_hash != recomputed or got_prev != expected_prev
            if bad:
                broken += 1
                broken_lines.append(line_no)
            else:
                chained += 1
            pred_state = "hash"
            pred_hash = recomputed

    return {
        "chained": chained,
        "legacy": legacy,
        "broken": broken,
        "broken_lines": broken_lines,
        "head": head,
        "rows": chained + legacy + broken,
        "tail_incomplete": tail_incomplete,
        "head_present": head_present,
    }


def _state_path(explicit: str | None, raw_paths: list) -> str | None:
    """--state wins, then SPINE_VERIFY_STATE, then .verify-heads.json in the first directory."""
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("SPINE_VERIFY_STATE")
    if env:
        return os.path.abspath(env)
    if not raw_paths:
        return None
    first = os.path.abspath(raw_paths[0])
    if os.path.isdir(first):
        directory = first
    else:
        directory = os.path.dirname(first) or "."
    return os.path.join(directory, ".verify-heads.json")


def _load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, val in data.items():
        if isinstance(key, str):
            out[os.path.abspath(key)] = val
    return out


def _save_state(path: str, state: dict) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".verify-heads.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _in_scope(key: str, raw_paths: list) -> bool:
    for raw in raw_paths:
        path = os.path.abspath(raw)
        if key == path:
            return True
        if os.path.isdir(path):
            prefix = path.rstrip(os.sep) + os.sep
            if key.startswith(prefix):
                return True
    return False


def _scope_state(state: dict, raw_paths: list) -> dict:
    return {key: val for key, val in state.items() if _in_scope(key, raw_paths)}


def _summary(state: str, files_n: int, chained: int, legacy: int, broken: int) -> str:
    return (
        "spine-verify: state=%s files=%d chained=%d legacy=%d broken=%d"
        % (state, files_n, chained, legacy, broken)
    )


def _int_field(record: dict, key: str) -> int | None:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _run(argv: list | None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify row_hash / prev_row_hash lineage on spine JSONL files.",
    )
    parser.add_argument(
        "--state", default=None, metavar="PATH",
        help="State file. Overrides SPINE_VERIFY_STATE. "
             "Default: .verify-heads.json in the first directory given.",
    )
    parser.add_argument(
        "paths", nargs="*", metavar="DIR_OR_FILE",
        help="A directory to search for nodes.jsonl and edges.jsonl, or a file to verify.",
    )
    args = parser.parse_args(argv)
    discovered = discover_files(args.paths)
    state_file = _state_path(args.state, args.paths)
    full_state = _load_state(state_file) if state_file else {}
    scoped = _scope_state(full_state, args.paths)

    if not discovered and not scoped:
        print(_summary("no_work", 0, 0, 0, 0))
        return 0

    alarmed = False
    entries = {}
    total_c = 0
    total_l = 0
    total_b = 0
    for path in discovered:
        prev = scoped.get(path)
        if not isinstance(prev, dict):
            prev = {}
        prev_head = prev.get("head")
        if not isinstance(prev_head, str) or not prev_head:
            prev_head = None
        rec = verify_file(path, prev_head=prev_head)
        total_c += rec["chained"]
        total_l += rec["legacy"]
        total_b += rec["broken"]
        shown = report_path(path)
        print("%s: chained=%d legacy=%d broken=%d" % (
            shown, rec["chained"], rec["legacy"], rec["broken"]))
        for line_no in rec["broken_lines"]:
            print("broken %s:%d" % (shown, line_no))
        tail_n = rec["tail_incomplete"]
        if tail_n:
            print("tail_incomplete=%d bytes %s" % (tail_n, shown))

        kept_head = rec["head"]
        kept_rows = rec["rows"]
        remembered = prev.get("head")
        if isinstance(remembered, str) and remembered and not rec["head_present"]:
            print("ALARM: spine history rewritten %s: previous head %s missing"
                  % (shown, remembered))
            alarmed = True
            kept_head = remembered
        prev_rows = _int_field(prev, "rows")
        if (prev_rows is not None and rec["rows"] < prev_rows and rec["head_present"]
                and isinstance(remembered, str) and remembered):
            print("ALARM: spine row count dropped %s: previous rows %d now %d"
                  % (shown, prev_rows, rec["rows"]))
            alarmed = True
            kept_rows = prev_rows
        if tail_n and prev.get("tail_incomplete"):
            print("ALARM: spine tail incomplete %s" % shown)
            alarmed = True
        entries[path] = {
            "head": kept_head,
            "rows": kept_rows,
            "tail_incomplete": tail_n,
        }

    missing = []
    for path in sorted(scoped):
        if path in entries:
            continue
        prev = scoped.get(path)
        if not isinstance(prev, dict):
            continue
        remembered = prev.get("head")
        head_s = remembered if isinstance(remembered, str) else ""
        print("ALARM: spine ledger missing %s: previous head %s"
              % (report_path(path), head_s))
        alarmed = True
        prev_rows = _int_field(prev, "rows")
        prev_tail = _int_field(prev, "tail_incomplete")
        entries[path] = {
            "head": remembered if isinstance(remembered, str) and remembered else prev.get("head"),
            "rows": prev_rows if prev_rows is not None else 0,
            "tail_incomplete": prev_tail if prev_tail is not None else 0,
        }
        missing.append(path)

    if state_file:
        new_state = {}
        for key, val in full_state.items():
            if key not in scoped and key not in entries:
                new_state[key] = val
        new_state.update(entries)
        _save_state(state_file, new_state)

    files_n = len(discovered) + len(missing)
    if total_b or alarmed:
        print(_summary("could_not", files_n, total_c, total_l, total_b))
        return 1
    print(_summary("did_work", files_n, total_c, total_l, total_b))
    return 0


def main(argv: list | None = None) -> int:
    try:
        return _run(argv)
    except Exception as exc:
        print("ALARM: spine-verify could not finish: %s" % exc)
        print(_summary("could_not", 0, 0, 0, 0))
        return 1


if __name__ == "__main__":
    sys.exit(main())
