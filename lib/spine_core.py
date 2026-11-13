"""
spine_core: the reusable engine for THE SPINE PATTERN.

Pattern (house standard): a typed, append-only, provenance-rich JSON node "spine" is the durable
SOURCE OF TRUTH; a vector DB indexes it for retrieval but never replaces it.

This module is project-agnostic. Point it at a directory and it gives you:
  - the shared node ENVELOPE (id/type/schema_version/status/project_id/source_refs/
    created_at/updated_at/created_by/confidence/needs_review/review_reason/payload/summary)
  - APPEND-ONLY writers (nodes/edges/artifacts/runs/evals JSONL)
  - SUPERSEDE (new node + SUPERSEDES edge; never mutate)
  - typed EDGES, DERIVED-VALUE wrapping, read/stats, text_repr (for indexing)

New projects should `from spine_core import Spine` and add their own domain constructors on top.
Retrieval lives in the sibling `spine_index.py`.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

FILES = {
    "nodes": "nodes.jsonl",
    "edges": "edges.jsonl",
    "artifacts": "artifacts.jsonl",
    "runs": "runs.jsonl",
    "evals": "evals.jsonl",
}

# Default typed relations. Extend per project via Spine(extra_rels=...).
BASE_EDGE_RELS = {
    "DERIVED_FROM", "BELONGS_TO_PROJECT", "REFERENCES", "FLAGS_GAP", "DECIDED_BY",
    "REVIEWED_BY", "EVALUATES", "SUPERSEDES", "TRACES",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slug(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-._:" else "-" for c in str(s).lower())[:80]


# row_hash covers every field except these two, then the literal prev_row_hash.
_CHAIN_SKIP = ("row_hash", "prev_row_hash")
_READ_BLOCK = 4096


def _canon_json(body) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_hash(obj: dict) -> str:
    """sha256 of the canonical body plus the literal prev_row_hash.

    The body skips row_hash and prev_row_hash. When prev_row_hash is present
    its literal value is appended, so a retargeted link changes the digest.
    """
    if not isinstance(obj, dict):
        return _sha256_text("")
    body = {k: v for k, v in obj.items() if k not in _CHAIN_SKIP}
    blob = _canon_json(body)
    if "prev_row_hash" in obj:
        prev = obj.get("prev_row_hash")
        literal = prev if isinstance(prev, str) else _canon_json(prev)
        blob = blob + "\n" + literal
    return _sha256_text(blob)


def chain_stamp(row):
    """Return (row_hash, prev_row_hash) when the row carries either field."""
    if not isinstance(row, dict):
        return (None, None)
    if "row_hash" not in row and "prev_row_hash" not in row:
        return (None, None)
    digest = row.get("row_hash")
    prev = row.get("prev_row_hash")
    digest_s = digest if isinstance(digest, str) and digest else None
    prev_s = prev if isinstance(prev, str) and prev else None
    if digest_s or prev_s:
        return (digest_s, prev_s)
    return (None, None)


def _find_last_nl(fh, size: int):
    """Byte index of the last newline in [0, size), or None."""
    pos = size
    while pos > 0:
        n = min(_READ_BLOCK, pos)
        pos -= n
        fh.seek(pos)
        chunk = fh.read(n)
        nl = chunk.rfind(b"\n")
        if nl != -1:
            return pos + nl
    return None


def _last_line_bytes(path: str):
    """Last newline-terminated non-empty line, or None.

    A tail with no terminating newline is ignored so the last complete line is used.
    """
    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        return None
    with fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if size == 0:
            return None
        fh.seek(-1, os.SEEK_END)
        if fh.read(1) == b"\n":
            term = size - 1
        else:
            term = _find_last_nl(fh, size)
            if term is None:
                return None
        if term <= 0:
            return None
        buf = b""
        pos = term
        while pos > 0:
            n = min(_READ_BLOCK, pos)
            pos -= n
            fh.seek(pos)
            buf = fh.read(n) + buf
            nl = buf.rfind(b"\n")
            if nl != -1:
                line = buf[nl + 1:]
                while True:
                    if line:
                        return line
                    buf = buf[:nl]
                    nl = buf.rfind(b"\n")
                    if nl == -1:
                        break
                    line = buf[nl + 1:]
        return buf if buf else None


def _needs_leading_newline(path: str) -> bool:
    """True when the file exists, is non-empty, and its last byte is not a newline."""
    try:
        with open(path, "rb") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                return False
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"
    except OSError:
        return False


def _last_row(path: str):
    raw = _last_line_bytes(path)
    if not raw:
        return None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if isinstance(obj, dict):
        return obj
    return None


def _prev_from_row(last):
    """prev_row_hash implied by the last complete row."""
    if not isinstance(last, dict):
        return "legacy"
    digest = last.get("row_hash")
    if isinstance(digest, str) and digest:
        return digest
    return "legacy"


def chain_fields(path: str, obj: dict) -> dict:
    """Return a new dict with row_hash and prev_row_hash set. Never mutates obj.

    prev_row_hash is the last complete row's row_hash. It is genesis when the
    file is missing or zero-byte, and legacy when the file is non-empty but
    the last complete line has no row_hash or cannot be parsed.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    if size is None or size == 0:
        prev = "genesis"
    else:
        last = _last_row(path)
        if last is None:
            prev = "legacy"
        else:
            prev = _prev_from_row(last)
    stamped = dict(obj)
    stamped["prev_row_hash"] = prev
    stamped["row_hash"] = canonical_hash(stamped)
    return stamped


def _write_chained_row(path: str, obj: dict, handle) -> dict:
    """Stamp chain fields and append one JSONL row. Separates a torn tail first."""
    chained = chain_fields(path, obj)
    if _needs_leading_newline(path):
        handle.write("\n")
    handle.write(json.dumps(chained, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
    return chained


@contextlib.contextmanager
def _flocked(path: str):
    """Exclusive flock on path + '.lock', held for the body of the with-block."""
    lock_path = path + ".lock"
    handle = open(lock_path, "ab")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def _read_rows(path: str):
    """Return (rows, skipped).

    Undecodable and torn lines are skipped and counted. Blank lines are ignored.
    A missing file is ([], 0).
    """
    if not os.path.exists(path):
        return [], 0
    rows = []
    skipped = 0
    with open(path, "rb") as handle:
        for raw in handle:
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped += 1
                continue
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                skipped += 1
                continue
            if not isinstance(obj, dict):
                skipped += 1
                continue
            rows.append(obj)
    return rows, skipped


class Spine:
    """An append-only typed-node spine rooted at `spine_dir`."""

    def __init__(self, spine_dir: str, *, default_creator: str = "agent", extra_rels: set[str] | None = None):
        self.dir = os.path.abspath(spine_dir)
        os.makedirs(self.dir, exist_ok=True)
        self.creator = default_creator
        self.rels = set(BASE_EDGE_RELS) | (extra_rels or set())

    # -- ids / envelope -------------------------------------------------------------
    def node_id(self, node_type: str, stable: str) -> str:
        return f"node:{slug(node_type)}:{slug(stable)}"

    def envelope(self, node_type: str, stable_id: str, *, project_id: str | None = None,
                 status: str = "draft", source_refs: list[dict] | None = None,
                 created_by: str | None = None, confidence: float | None = None,
                 needs_review: bool = False, review_reason: str | None = None,
                 payload: dict[str, Any] | None = None, summary: str = "") -> dict:
        ts = now_iso()
        return {
            "id": self.node_id(node_type, stable_id), "type": node_type, "schema_version": "0.1",
            "status": status, "project_id": project_id, "summary": summary,
            "source_refs": source_refs or [], "created_at": ts, "updated_at": ts,
            "created_by": created_by or self.creator, "confidence": confidence,
            "needs_review": needs_review, "review_reason": review_reason, "payload": payload or {},
        }

    @staticmethod
    def source_ref(artifact_id: str, locator: str, quote: str = "") -> dict:
        return {"artifact_id": artifact_id, "locator": locator, "quote": quote}

    @staticmethod
    def derived_value(value: Any, unit: str | None, method: str, explanation: str, inputs: dict,
                      evidence: list[dict], confidence: float, target: str,
                      needs_review: bool = True) -> dict:
        return {"value": value, "unit": unit,
                "derivation": {"method": method, "explanation": explanation, "inputs": inputs},
                "evidence": evidence, "confidence": confidence, "needs_review": needs_review,
                "target": target}

    # -- append-only writers --------------------------------------------------------
    def append(self, kind: str, obj: dict) -> dict:
        """Append a chained row. Returns the stamped row; the caller's dict is not mutated."""
        if kind not in FILES:
            raise ValueError(f"unknown spine kind '{kind}'; expected {list(FILES)}")
        path = os.path.join(self.dir, FILES[kind])
        # One lock per file: <file>.lock. The tail read and the write share it.
        # flush and fsync finish before the lock is released.
        with _flocked(path):
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                return _write_chained_row(path, obj, handle)

    def edge(self, rel: str, src_id: str, dst_id: str, *, note: str = "", created_by: str | None = None) -> dict:
        if rel not in self.rels:
            raise ValueError(f"unknown edge rel '{rel}'; allowed {sorted(self.rels)}")
        e = {"id": self.node_id("edge", f"{rel}:{src_id}->{dst_id}"), "type": "Edge", "rel": rel,
             "src": src_id, "dst": dst_id, "note": note, "created_at": now_iso(),
             "created_by": created_by or self.creator}
        return self.append("edges", e)

    def supersede(self, old_id: str, new_node: dict, *, kind: str = "nodes") -> dict:
        """Append a replacing node + a SUPERSEDES edge. NEVER mutates the old node (staleness fix)."""
        self.append(kind, new_node)
        self.edge("SUPERSEDES", new_node["id"], old_id, note="supersedes prior node")
        return new_node

    # -- read / index helpers -------------------------------------------------------
    def read(self, kind: str) -> list[dict]:
        rows, _skipped = _read_rows(os.path.join(self.dir, FILES[kind]))
        return rows

    def all_nodes(self) -> list[dict]:
        out: list[dict] = []
        for k in FILES:
            out.extend(self.read(k))
        return out

    def stats(self) -> dict:
        out = {}
        skipped = 0
        for kind in FILES:
            rows, n = _read_rows(os.path.join(self.dir, FILES[kind]))
            out[kind] = len(rows)
            skipped += n
        out["skipped_lines"] = skipped
        return out

    @staticmethod
    def text_repr(n: dict) -> str:
        """Compact text for vector indexing (index node summaries, not the whole blob)."""
        p = n.get("payload", {}) or {}
        bits = [n.get("type", ""), n.get("summary", "")]
        for k in ("decision", "reasoning", "message", "what", "action", "name", "detail"):
            if p.get(k):
                bits.append(str(p[k]))
        return " | ".join(b for b in bits if b)[:1200]


if __name__ == "__main__":
    import sys
    s = Spine(sys.argv[1] if len(sys.argv) > 1 else ".")
    print("spine:", s.dir, "counts:", s.stats())
