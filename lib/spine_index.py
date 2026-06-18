"""
spine_index — the RETRIEVAL half of the Spine Pattern. Qdrant indexes the spine; it never owns it.

Indexes any `Spine`'s nodes into a Qdrant collection (node summaries via `text_repr`), with the
metadata payload needed for FILTERED, BOUNDED retrieval (K~4-6, the AI Engineer agent-memory default).

Default substrate: nomic-embed-text (768d) via Ollama /api/embed, QdrantClient on
127.0.0.1:6333. Swap in any embedder / vector store — the index is disposable.

Run:
  uv run --with qdrant-client --with requests python spine_index.py index <spine_dir> <collection> [--port 6333]
  uv run --with qdrant-client --with requests python spine_index.py search <collection> "query" [--k 5 --port 6333 --type DecisionTrace]
"""

from __future__ import annotations

import argparse
import uuid

import requests
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from spine_core import Spine

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
OLLAMA = "http://127.0.0.1:11434"
NS = uuid.UUID("5f1e0d9c-0000-4000-8000-5b111c0e3000")  # stable namespace for spine point ids


def embed(texts: list[str]) -> list[list[float]]:
    r = requests.post(f"{OLLAMA}/api/embed", json={"model": EMBED_MODEL, "input": texts}, timeout=180)
    r.raise_for_status()
    return r.json()["embeddings"]


def _point_id(node_id: str) -> str:
    return str(uuid.uuid5(NS, node_id))


def index(spine_dir: str, collection: str, host: str = "127.0.0.1", port: int = 6333) -> int:
    s = Spine(spine_dir)
    nodes = [n for n in s.all_nodes() if Spine.text_repr(n).strip()]
    if not nodes:
        print("no indexable nodes"); return 0
    cl = QdrantClient(host=host, port=port, timeout=60)
    if not cl.collection_exists(collection):
        cl.create_collection(collection, vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE))
    texts = [Spine.text_repr(n) for n in nodes]
    vecs: list[list[float]] = []
    for i in range(0, len(texts), 16):
        vecs.extend(embed(texts[i:i + 16]))
    if len(vecs) != len(nodes):
        raise RuntimeError(f"embed mismatch: {len(vecs)} != {len(nodes)}")
    pts = []
    for n, v in zip(nodes, vecs):
        conf = n.get("confidence")
        pts.append(PointStruct(id=_point_id(n["id"]), vector=v, payload={
            "node_id": n["id"], "type": n.get("type"), "project_id": n.get("project_id"),
            "status": n.get("status"), "needs_review": bool(n.get("needs_review")),
            "confidence": conf, "confidence_band": ("high" if (conf or 0) >= 0.85 else
                                                     "med" if (conf or 0) >= 0.6 else "low"),
            "created_at": n.get("created_at"), "summary": n.get("summary", ""),
            "text": Spine.text_repr(n),
        }))
    for i in range(0, len(pts), 64):
        cl.upsert(collection_name=collection, points=pts[i:i + 64])
    print(f"indexed {len(pts)} nodes -> {collection} @ {host}:{port}")
    return len(pts)


def search(collection: str, query: str, k: int = 5, host: str = "127.0.0.1", port: int = 6333,
           node_type: str | None = None, project_id: str | None = None,
           review_status: str | None = None) -> list[dict]:
    """Bounded (K~4-6), metadata-filtered retrieval — the spine neighborhood, not 'all related docs'."""
    cl = QdrantClient(host=host, port=port, timeout=60)
    must = []
    if node_type: must.append(FieldCondition(key="type", match=MatchValue(value=node_type)))
    if project_id: must.append(FieldCondition(key="project_id", match=MatchValue(value=project_id)))
    if review_status: must.append(FieldCondition(key="status", match=MatchValue(value=review_status)))
    flt = Filter(must=must) if must else None
    hits = cl.query_points(collection_name=collection, query=embed([query])[0],
                           query_filter=flt, limit=k, with_payload=True).points
    return [{"score": round(h.score, 3), **h.payload} for h in hits]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("index"); pi.add_argument("spine_dir"); pi.add_argument("collection"); pi.add_argument("--port", type=int, default=6333)
    ps = sub.add_parser("search"); ps.add_argument("collection"); ps.add_argument("query"); ps.add_argument("--k", type=int, default=5); ps.add_argument("--port", type=int, default=6333); ps.add_argument("--type", default=None); ps.add_argument("--project", default=None)
    a = ap.parse_args()
    if a.cmd == "index":
        index(a.spine_dir, a.collection, port=a.port)
    else:
        for r in search(a.collection, a.query, k=a.k, port=a.port, node_type=a.type, project_id=a.project):
            print(f"[{r['score']}] {r.get('type')} · {r.get('node_id')}\n    {r.get('summary','')[:140]}")
