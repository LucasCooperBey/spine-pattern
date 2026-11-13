"""Hash chain on Spine.append: row_hash, prev_row_hash, lock, torn reads."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import spine_core
from spine_core import Spine, canonical_hash


class ChainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.spine = os.path.join(self._tmp.name, "spine")

    def tearDown(self):
        self._tmp.cleanup()

    def test_three_appends_link_from_genesis(self):
        # source: Spine.append stamps prev_row_hash genesis, then each previous row_hash
        # (append stamps the chain across three rows, and the lock file is <file>.lock).
        sp = Spine(self.spine)
        rows = []
        for i in range(3):
            rows.append(sp.append("nodes", {"id": "n%d" % i, "payload": {"i": i}}))
        self.assertEqual(rows[0]["prev_row_hash"], "genesis")
        self.assertEqual(rows[1]["prev_row_hash"], rows[0]["row_hash"])
        self.assertEqual(rows[2]["prev_row_hash"], rows[1]["row_hash"])
        for row in rows:
            self.assertEqual(row["row_hash"], canonical_hash(row))
            self.assertTrue(row["row_hash"].startswith("sha256:"))
        disk = sp.read("nodes")
        self.assertEqual(
            [row["prev_row_hash"] for row in disk],
            [row["prev_row_hash"] for row in rows],
        )
        self.assertEqual(
            [row["row_hash"] for row in disk],
            [row["row_hash"] for row in rows],
        )
        path = os.path.join(self.spine, "nodes.jsonl")
        self.assertTrue(os.path.isfile(path + ".lock"))

    def test_append_does_not_mutate_caller_dict(self):
        # source: append returns a stamped copy and leaves the caller's dict unchanged.
        sp = Spine(self.spine)
        obj = {"id": "a", "payload": {"n": 1}}
        snapshot = json.dumps(obj, sort_keys=True)
        out = sp.append("nodes", obj)
        self.assertEqual(json.dumps(obj, sort_keys=True), snapshot)
        self.assertIsNot(out, obj)
        self.assertNotIn("row_hash", obj)
        self.assertNotIn("prev_row_hash", obj)
        self.assertIn("row_hash", out)
        self.assertEqual(out["prev_row_hash"], "genesis")

    def test_prev_is_legacy_after_nonempty_unhashed_file(self):
        # source: prev_row_hash is genesis for a zero-byte file, and legacy when the
        # file is non-empty and the last complete line has no hash or does not parse.
        sp = Spine(self.spine)
        edges = os.path.join(self.spine, "edges.jsonl")
        open(edges, "wb").close()
        edge_row = sp.append("edges", {"id": "e"})
        self.assertEqual(edge_row["prev_row_hash"], "genesis")

        nodes = os.path.join(self.spine, "nodes.jsonl")
        with open(nodes, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "old"}) + "\n")
        node_row = sp.append("nodes", {"id": "new"})
        self.assertEqual(node_row["prev_row_hash"], "legacy")
        self.assertEqual(node_row["row_hash"], canonical_hash(node_row))

        artifacts = os.path.join(self.spine, "artifacts.jsonl")
        with open(artifacts, "wb") as handle:
            handle.write(b'{"id":"truncated"')
        art = sp.append("artifacts", {"id": "next"})
        self.assertEqual(art["prev_row_hash"], "legacy")
        with open(artifacts, "rb") as handle:
            data = handle.read()
        self.assertIn(b'{"id":"truncated"\n', data)

    def test_missing_trailing_newline_is_repaired_before_append(self):
        # source: a file whose last byte is not a newline gets one before the next
        # append, and the previous bytes are not rewritten.
        sp = Spine(self.spine)
        path = os.path.join(self.spine, "nodes.jsonl")
        os.makedirs(self.spine, exist_ok=True)
        raw = b'{"id":"old","payload":{"text":"keep"}}'
        with open(path, "wb") as handle:
            handle.write(raw)
        row = sp.append("nodes", {"id": "new", "payload": {"text": "next"}})
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertTrue(data.startswith(raw + b"\n"))
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(row["prev_row_hash"], "legacy")
        disk = sp.read("nodes")
        self.assertEqual(disk[0]["id"], "old")
        self.assertNotIn("row_hash", disk[0])
        self.assertEqual(disk[1]["row_hash"], row["row_hash"])

    def test_read_skips_torn_line_and_stats_counts_it(self):
        # source: read skips a torn or undecodable line and stats exposes skipped_lines.
        sp = Spine(self.spine)
        sp.append("nodes", {"id": "a"})
        nodes = os.path.join(self.spine, "nodes.jsonl")
        with open(nodes, "ab") as handle:
            handle.write(b'{"torn": ')
        self.assertEqual(len(sp.read("nodes")), 1)
        self.assertEqual(sp.stats()["nodes"], 1)
        self.assertEqual(sp.stats()["skipped_lines"], 1)
        edges = os.path.join(self.spine, "edges.jsonl")
        with open(edges, "wb") as handle:
            handle.write(b"\xff\n")
        self.assertEqual(sp.read("edges"), [])
        self.assertEqual(sp.stats()["skipped_lines"], 2)

    def test_canonical_hash_ignores_key_order(self):
        # source: canonical JSON sorts keys, so key order does not change row_hash,
        # and the literal prev_row_hash is part of the digest.
        left = canonical_hash({"z": 1, "a": 2, "prev_row_hash": "genesis"})
        right = canonical_hash({"a": 2, "z": 1, "prev_row_hash": "genesis"})
        self.assertEqual(left, right)
        without_prev = canonical_hash({"z": 1, "a": 2})
        self.assertNotEqual(left, without_prev)

    def test_long_row_still_links_to_previous_hash(self):
        # source: the previous row is read in blocks, so a row longer than one
        # block still supplies prev_row_hash for the next append.
        sp = Spine(self.spine)
        first = sp.append("nodes", {"id": "pad", "payload": {"blob": "x" * 5000}})
        second = sp.append("nodes", {"id": "next"})
        self.assertEqual(second["prev_row_hash"], first["row_hash"])
        self.assertGreater(
            len(json.dumps(first, ensure_ascii=False).encode("utf-8")),
            spine_core._READ_BLOCK,
        )


if __name__ == "__main__":
    unittest.main()
