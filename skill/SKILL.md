---
name: spine-pattern
description: >-
  Apply THE SPINE PATTERN when building or designing ANY memory, knowledge base, RAG, agent-memory, or "make X searchable" system. The pattern: a typed, append-only, provenance-rich JSON node SPINE is the durable source of truth. A vector DB (Qdrant/pgvector) INDEXES it for retrieval but NEVER replaces it. Use when the user says "build a knowledge base", "agent memory", "RAG", "vector DB", "Qdrant", "embed/index this", "make it searchable", "memory layer", "knowledge graph", "ingest into a vector store", "how should this remember", or is about to dump data straight into a vector DB. Prevents the anti-pattern of making the vector DB the memory. Reuses lib/spine_core.py, lib/spine_verify.py, lib/spine_index.py, and README.md.
---

# The Spine Pattern

**STOP before dumping anything into a vector DB.** First decide what the typed **source of truth** is, since the vector DB only indexes it.

## One line
A typed, append-only, provenance-rich JSON node *spine* is the durable source of truth. A vector DB *indexes* it for retrieval but never replaces it.

## The failure this prevents
Making the vector DB the memory → no replay, no audit, no supersede of stale facts, no metadata filtering, and "what do we know about X" returns fuzzy chunks instead of typed facts with provenance.

## The 13 principles (enforce these)
1. The log is truth; the index is disposable (rebuild it from the log anytime).
2. **Append-only: supersede, never overwrite** (new node + `SUPERSEDES` edge). The staleness fix.
3. Provenance on every derived value (`value + derivation + evidence + confidence + target`). No naked numbers.
4. Every judgment is a `DecisionTrace`/`ReviewAction` node rather than an invisible note.
5. One shared envelope for all node types.
6. Model memory after the business domain, not a generic schema.
7. Typed edges from a closed vocabulary, stored as append-only nodes.
8. Retrieval is metadata-filtered first, then bounded (K≈4 to 6).
9. Raw artifacts stay raw; nodes point at exact locators.
10. Only durable decisions graduate to the wiki.
11. Two memories, one envelope: Layer A project-KB (snapshot) + Layer B runtime spine (append-only), linked by `TRACES`.
12. Mark uncertainty machine-readable (`status:hypothesis`, `confidence`, `needs_review`).
13. Don't over-build the graph (defer Neo4j / Postgres tenancy / per-customer buckets until a gate demands).

## How to apply (import, don't rebuild)
Use the shared library in `lib/` (`lib/spine_core.py`, `lib/spine_verify.py`, `lib/spine_index.py`) and the README:
```python
from spine_core import Spine
sp = Spine("path/to/project/spine")
dv = sp.derived_value(value, unit, method, explanation, inputs, evidence, confidence, target)
sp.append("nodes", sp.envelope("DecisionTrace", "stable-id", payload={"derived": dv}, needs_review=True))
sp.supersede(old_id, new_node)   # never edit in place
```
```bash
uv run --with qdrant-client --with requests python lib/spine_index.py index <spine_dir> <collection>
uv run --with qdrant-client --with requests python lib/spine_index.py search <collection> "question" --k 5 --type DecisionTrace
```

## The 6-component checklist (a spine isn't done without all six)
1. Envelope · 2. Provenance wrapper · 3. Append + supersede · 4. Typed edges · 5. Indexer hook (`text_repr`) · 6. Retrieval filters (metadata-first, bounded K).

## Substrate
Qdrant on `127.0.0.1:6333` by default (`--port` changes it). Embedder: `nomic-embed-text` (768d) via Ollama on `127.0.0.1:11434`. pgvector or another embedder also works as the index, since the index is disposable and rebuilt from the spine.

## References
- Library and README: `lib/spine_core.py`, `lib/spine_verify.py`, `lib/spine_index.py`, and `README.md`.
- Verify a spine: `python3 lib/spine_verify.py path/to/spine` (README section "Hash lineage and verify").
