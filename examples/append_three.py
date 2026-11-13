"""Append three notes to a spine directory, for the verify example in the README.

Run from the repo root:  python3 examples/append_three.py spine
It stops when the directory already has rows, so the example always starts from three.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from spine_core import Spine  # noqa: E402

TARGET = Path(sys.argv[1] if len(sys.argv) > 1 else "spine")
if (TARGET / "nodes.jsonl").exists():
    sys.exit("%s already has rows. Delete the folder or name a new one." % TARGET)

sp = Spine(str(TARGET))
for number, text in enumerate(["alpha", "SECOND", "gamma"]):
    sp.append("nodes", sp.envelope("Note", "n%d" % number, payload={"text": text}))
print("appended 3 nodes to", sp.dir)
