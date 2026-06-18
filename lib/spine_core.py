"""
spine_core — the reusable engine for THE SPINE PATTERN.

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
        if kind not in FILES:
            raise ValueError(f"unknown spine kind '{kind}'; expected {list(FILES)}")
        with open(os.path.join(self.dir, FILES[kind]), "a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return obj

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
        p = os.path.join(self.dir, FILES[kind])
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

    def all_nodes(self) -> list[dict]:
        out: list[dict] = []
        for k in FILES:
            out.extend(self.read(k))
        return out

    def stats(self) -> dict:
        return {k: len(self.read(k)) for k in FILES}

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
