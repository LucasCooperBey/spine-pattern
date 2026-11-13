"""Quick start: record a derived value with its evidence, then replace it without editing in place.

Run from the repo root:  python3 examples/quickstart.py
It writes to ./demo-spine (delete that folder to start over).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from spine_core import Spine  # noqa: E402

sp = Spine("demo-spine")

# Every derived number carries how it was derived and the evidence behind it.
evidence = [sp.source_ref("plan-sheet-A2", "page 2, dimension 8x246")]
dv = sp.derived_value(1968, "sq_ft", "area_from_dimensions", "8 ft x 246 ft",
                      {"w": 8, "l": 246}, evidence, 0.95, "target_surface")
old = sp.append("nodes", sp.envelope("DecisionTrace", "wall-area",
                                     payload={"derived": dv}, needs_review=True))

# A re-measure changes the answer. The old node stays; a new node supersedes it.
dv2 = sp.derived_value(2000, "sq_ft", "area_from_dimensions", "8 ft x 250 ft after a re-measure",
                       {"w": 8, "l": 250}, evidence, 0.9, "target_surface")
new_node = sp.envelope("DecisionTrace", "wall-area-v2", payload={"derived": dv2}, needs_review=True)
sp.supersede(old["id"], new_node)  # never edit in place

for node in sp.read("nodes"):
    print("node", node["id"], node["payload"]["derived"]["value"])
for edge in sp.read("edges"):
    print("edge", edge["rel"], edge["src"], "->", edge["dst"])

# Edge types are a closed set, so a typo cannot invent a new relation.
try:
    sp.edge("REPLACES", new_node["id"], old["id"])
except ValueError as err:
    print("refused:", err)
