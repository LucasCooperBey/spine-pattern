# The Spine Pattern

**Make the past queryable and trustworthy, so the present can be built on without fear.**

> Most software remembers like someone scribbling over the same sticky note: when a value changes, the old one is overwritten and gone. The Spine Pattern makes software remember like an accountant keeps a ledger — every change is a new dated entry that records *who, what, when, and why*, the old entry is marked superseded but never deleted, and the fast "current" view is just a pointer at the latest line.

A typed, append-only, provenance-rich JSON node **spine** is the durable **source of truth**. A vector DB (Qdrant / pgvector) **indexes** it for retrieval but **never replaces** it.

---

## The failure this kills

The default move — "dump everything into a vector DB and call it memory" — quietly destroys the things that make memory *useful*:

```
   VECTOR-DB-AS-MEMORY (the anti-pattern)        THE SPINE (this pattern)
   ────────────────────────────────────         ──────────────────────────────
   • can't replay history                        • full append-only history
   • can't audit "why is this value X?"          • provenance on every value
   • can't supersede a stale fact                • supersede-never-overwrite
   • can't filter by type/status/confidence      • typed nodes + metadata filters
   • "what do we know about X?" → fuzzy chunks    • → typed facts with receipts
```

```
   BEFORE (sticky note)                  AFTER (ledger)
   ┌──────────────┐                      ┌────────────────────────────────┐
   │ value: 78%   │                      │ v1  78%   (seed)        ──────┐ │ kept
   └──────┬───────┘                      │ v2  81%   why: 7 inputs ──────┤ │ kept
          │ change → ERASE + rewrite      │ v3  90%   why: regenerated ◄──┘ │ CURRENT
   ┌──────▼───────┐                      └────────────────────────────────┘
   │ value: 81%   │  ← 78% gone forever       "current" points at the latest line.
   └──────────────┘                            nothing is ever erased.
```

---

## The principles (enforce all of them)

1. **The log is truth; the index is disposable.** You can rebuild the vector DB from the spine anytime.
2. **Append-only — supersede, never overwrite.** To change a value, write a *new* node + a `SUPERSEDES` edge. This is the staleness fix.
3. **Provenance on every derived value:** `value + derivation + evidence + confidence + target`. No naked numbers.
4. **Every judgment is a node** (a `DecisionTrace` / `ReviewAction`), not an invisible note that evaporates after the reply.
5. **One shared envelope** for all node types.
6. **Model memory after the business domain,** not a generic schema.
7. **Typed edges** from a closed vocabulary (`DERIVED_FROM`, `RECONCILES_WITH`, `EVALUATES`, `TRACES`…), stored as append-only nodes.
8. **Retrieval is metadata-filtered first, then bounded** (top-K ≈ 4–6). Pull the node neighborhood, not "all related docs."
9. **Raw artifacts stay raw** (files on disk / object storage); nodes point at exact locators, never inline blobs.
10. **Only durable decisions graduate to the wiki.** The spine holds every trace; the wiki holds the conclusions.
11. **Two memories, one envelope:** Layer A = project knowledge-base (snapshot) + Layer B = runtime spine (append-only), linked by `TRACES`.
12. **Mark uncertainty machine-readable:** `status:"hypothesis"`, `confidence`, `needs_review`.
13. **Don't over-build the graph.** Defer Neo4j / multi-tenant infrastructure until a real requirement demands it.

**The 6-component checklist (a spine isn't done without all six):** Envelope · Provenance wrapper · Append + supersede · Typed edges · Indexer hook (`text_repr`) · Retrieval filters (metadata-first, bounded K).

---

## What's in this repo

| Path | Role |
|---|---|
| `lib/spine_core.py` | the engine — `Spine(dir)`: envelope, append-only writers, typed edges, `supersede()`, `derived_value()`, `read/stats`, `text_repr` |
| `lib/spine_index.py` | the retrieval half — index any spine into Qdrant (`nomic-embed-text` 768d via Ollama) + bounded, metadata-filtered `search()` |
| `skill/SKILL.md` | the auto-invoking agent skill — fires whenever someone builds memory/RAG/"make X searchable" |

### Quick start (Python reference engine)

```python
from spine_core import Spine
sp = Spine("path/to/project/spine")
dv = sp.derived_value(1968, "sq_ft", "area_from_dimensions", "8x246",
                      {"w": 8, "l": 246}, evidence, 0.95, "target_surface")
sp.append("nodes", sp.envelope("DecisionTrace", "stable-id",
                               payload={"derived": dv}, needs_review=True))
sp.supersede(old_id, new_node)        # never edit in place
```

```bash
uv run --with qdrant-client --with requests python lib/spine_index.py index <spine_dir> <collection>
uv run --with qdrant-client --with requests python lib/spine_index.py search <collection> "a question" --k 5 --type DecisionTrace
```

The Python engine is the **reference implementation**. The pattern is stack-agnostic — it has been re-expressed in TypeScript + Drizzle/Postgres (append-only snapshot table behind a materialized "HEAD" row), TypeScript + SQLite, and append-only Markdown. The *principles* travel; the engine is one convenient embodiment.

---

## Backstory (why this is whole, and how it came to be)

- **Ultimate solve.** Not "remember." It makes the past *queryable and trustworthy* — so you can build on, audit, replay, and never silently lose what the system knows.
- **Why it matters.** Lossy memory is a foundation crack: every agent and app built on it inherits an unauditable, un-undoable, un-trustable past. That caps how autonomous a system can ever safely become.
- **Downstream effects.** Trustworthy memory → systems you can extend without re-verifying → more autonomy → and, in a product, "auditable AI memory" becomes a genuine differentiator ("we can show you exactly why every number is what it is, and we never lose your history").
- **Best use cases.** Any system of record; any AI memory / RAG layer; any "learns over time" loop; anything where *"why did this change?"* must be answerable. **Not for:** ephemeral scratch data, or caches you're happy to lose.
- **How we learned it.** Crystallized from a design memo mined out of a large **AI Engineer YouTube corpus** (hundreds of videos, embedded into a vector DB for retrieval); first proven end-to-end in a production system; then hardened into a house standard via an adversarial perfection process (two critique passes + cross-model review).
- **What inspired it.** Andrej Karpathy's append-only **LLM-wiki** idea (a hand-curated, never-deleted knowledge log) is the philosophical root. The Spine Pattern generalizes it: from one human wiki to a typed substrate any app, agent, or filesystem process can stand on.
- **Source material.** A large AI Engineer YouTube corpus (embedded into a vector DB for retrieval) + Karpathy's LLM-wiki writing + an originating design memo. The exact corpus talks behind the pattern:

  **Named in the originating memo:**
  - [Why your agents need decision traces, not just documents — Zach Blumenfeld, Neo4j](https://www.youtube.com/watch?v=B9h9ovW5H9U) (`B9h9ovW5H9U`) — principle 4: every judgment is a node
  - [Stop Using RAG as Memory — Daniel Chalef, Zep](https://www.youtube.com/watch?v=T5IMo5ntyhA) (`T5IMo5ntyhA`) — the anti-pattern this kills
  - [Architecting Agent Memory: Principles, Patterns, and Best Practices — Richmond Alake, MongoDB](https://www.youtube.com/watch?v=W2HVdB4Jbjs) (`W2HVdB4Jbjs`) — memory modeled after the domain
  - [Make your own event-sourced agent harness using stream processors — Jonas Templestein, Iterate](https://www.youtube.com/watch?v=vi-2nasppAg) (`vi-2nasppAg`) — append-only log as truth
  - [Harness Engineering: How to Build Software When Humans Steer, Agents Execute — Ryan Lopopolo, OpenAI](https://www.youtube.com/watch?v=am_oeAoUhew) (`am_oeAoUhew`) — durable substrate under agents
  - [Human seeded Evals — Samuel Colvin, Pydantic](https://www.youtube.com/watch?v=o_LRtAomJCs) (`o_LRtAomJCs`) — review/judgment as first-class data
  - [Pydantic is all you need: Jason Liu](https://www.youtube.com/watch?v=yj-wSRJwrrc) (`yj-wSRJwrrc`) — typed envelopes over loose blobs
  - [Pydantic is STILL all you need: Jason Liu](https://www.youtube.com/watch?v=pZ4DIH2BVqg) (`pZ4DIH2BVqg`) — typed envelopes, the sequel

  **Supporting corpus talks** (same themes, surfaced by retrieval):
  - [Stateful Agents — Full Workshop with Charles Packer of Letta and MemGPT](https://www.youtube.com/watch?v=E0k9Ppq6yXY) (`E0k9Ppq6yXY`)
  - [From Stateless Nightmares to Durable Agents — Samuel Colvin, Pydantic](https://www.youtube.com/watch?v=flf_IKnFYnE) (`flf_IKnFYnE`)
  - [Memory Masterclass: Make Your AI Agents Remember What They Do! — Mark Bain, AIUS](https://www.youtube.com/watch?v=gsedOXz8FX4) (`gsedOXz8FX4`)
  - [Jack Morris: Stuffing Context is not Memory, Updating Weights is](https://www.youtube.com/watch?v=Jty4s9-Jb78) (`Jty4s9-Jb78`)
  - [BDD, ADR, PRD, WTF: Capturing Decisions for Humans and AI Alike — Michal Cichra, Safe Intelligence](https://www.youtube.com/watch?v=504PvfXou5Y) (`504PvfXou5Y`)
  - [The Knowledge Graph Mullet: Trimming GraphRAG Complexity — William Lyon](https://www.youtube.com/watch?v=tYCu_57jzL8) (`tYCu_57jzL8`) — principle 13: don't over-build the graph
  - [When Vectors Break Down: Graph-Based RAG for Dense Enterprise Knowledge — Sam Julien, Writer](https://www.youtube.com/watch?v=XlAIgmi_Vow) (`XlAIgmi_Vow`)
- **Historical evolution.** Karpathy append-only wiki (concept) → a typed-node-json-spine memo (formalized from the corpus) → first proven instance in a production system → promoted to house standard with reusable library + skill → rolled out across multiple systems, each adoption hardened by perfection passes.

---

## Adoption status

The pattern has been adopted across several production systems — a typed-node spine + vector index as the reference instance, an idea ledger carrying the confidence / needs-review envelope, a decision wiki with supersede-flip + embed-ready node summaries, and an append-only metric-snapshot table behind a live HEAD row.

**Each adoption sharpens this shared library + skill, so the next adoption is faster and safer than the last.**

---

## Substrate

- **Embedder:** the reference engine uses `nomic-embed-text` (768d) via Ollama `/api/embed` — swap in any embedder.
- **Vector DB:** Qdrant in the reference engine; pgvector / Voyage / any vector store also count as "the index." The point is that the index is disposable and rebuildable from the spine.

---

*The Spine Pattern — a typed, append-only, provenance-rich memory log that the vector DB indexes but never replaces.*
