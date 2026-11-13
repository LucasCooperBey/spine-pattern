"""Verifier: broken rows, legacy rows, and state-file alarms."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import spine_verify
from spine_core import Spine

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
VERIFY = os.path.join(ROOT, "lib", "spine_verify.py")


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.spine = os.path.join(self.root, "spine")
        self.state = os.path.join(self.root, "verify-heads.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _cli(self, *args, state=None, use_flag=True):
        if state is None:
            state = self.state
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["SPINE_VERIFY_STATE"] = state
        cmd = [sys.executable, VERIFY]
        if use_flag:
            cmd.extend(["--state", state])
        cmd.extend(args)
        return subprocess.run(
            cmd, capture_output=True, text=True, env=env, check=False,
        )

    def _nodes(self):
        return os.path.join(self.spine, "nodes.jsonl")

    def _append_three(self):
        sp = Spine(self.spine)
        rows = [
            sp.append("nodes", {"id": "n1", "payload": {"text": "alpha"}}),
            sp.append("nodes", {"id": "n2", "payload": {"text": "SECOND"}}),
            sp.append("nodes", {"id": "n3", "payload": {"text": "gamma"}}),
        ]
        return sp, rows

    def test_changed_byte_is_broken_at_its_line(self):
        # source: a changed byte in a chained row is broken at that line, and the
        # next row's link fails against the recomputed hash.
        self._append_three()
        path = self._nodes()
        with open(path, "rb") as handle:
            raw = handle.read()
        self.assertIn(b"SECOND", raw)
        tampered = raw.replace(b"SECOND", b"SECONE", 1)
        self.assertEqual(tampered.count(b"SECONE"), 1)
        with open(path, "wb") as handle:
            handle.write(tampered)
        result = spine_verify.verify_file(path)
        self.assertEqual(result["broken_lines"], [2, 3])
        self.assertEqual(result["chained"], 1)
        self.assertEqual(result["broken"], 2)
        self.assertEqual(result["legacy"], 0)

    def test_swapped_pair_is_broken(self):
        # source: swapping two chained rows breaks both, because each prev_row_hash
        # no longer names the row now above it.
        self._append_three()
        path = self._nodes()
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
        lines[1], lines[2] = lines[2], lines[1]
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        result = spine_verify.verify_file(path)
        self.assertEqual(result["chained"], 1)
        self.assertEqual(result["broken"], 2)
        self.assertEqual(result["broken_lines"], [2, 3])

    def test_dropped_middle_row_is_broken(self):
        # source: dropping the middle row breaks the row that still points at it.
        self._append_three()
        path = self._nodes()
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(lines[0])
            handle.write(lines[2])
        result = spine_verify.verify_file(path)
        self.assertEqual(result["chained"], 1)
        self.assertEqual(result["broken"], 1)
        self.assertEqual(result["broken_lines"], [2])

    def test_relinked_row_is_broken(self):
        # source: retargeting prev_row_hash without recomputing row_hash breaks the
        # row, because the digest includes the literal link. This goes red if
        # prev_row_hash is left out of the digest.
        _sp, rows = self._append_three()
        path = self._nodes()
        with open(path, encoding="utf-8") as handle:
            disk = [json.loads(line) for line in handle if line.strip()]
        forged = dict(disk[2])
        forged["prev_row_hash"] = rows[0]["row_hash"]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(disk[0]) + "\n")
            handle.write(json.dumps(forged) + "\n")
        result = spine_verify.verify_file(path)
        self.assertEqual(forged["row_hash"], rows[2]["row_hash"])
        self.assertEqual(result["chained"], 1)
        self.assertEqual(result["broken"], 1)
        self.assertEqual(result["broken_lines"], [2])

    def test_hashless_row_after_chain_is_broken(self):
        # source: a row with no hash after a chained row is broken (stripped hashes,
        # or a writer appending raw JSON onto a chained tail). This goes red if
        # that row is counted as legacy.
        sp = Spine(self.spine)
        sp.append("nodes", {"id": "kept"})
        path = self._nodes()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "stripped"}) + "\n")
        result = spine_verify.verify_file(path)
        self.assertEqual(result["chained"], 1)
        self.assertEqual(result["legacy"], 0)
        self.assertEqual(result["broken"], 1)
        self.assertEqual(result["broken_lines"], [2])

    def test_hashless_row_before_chain_is_legacy(self):
        # source: a row with no hash before the first chained row stays legacy
        # and is not rewritten.
        path = self._nodes()
        os.makedirs(self.spine, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "old"}) + "\n")
        with open(path, "rb") as handle:
            before = handle.read()
        sp = Spine(self.spine)
        sp.append("nodes", {"id": "new"})
        result = spine_verify.verify_file(path)
        self.assertEqual(result["legacy"], 1)
        self.assertEqual(result["chained"], 1)
        self.assertEqual(result["broken"], 0)
        with open(path, "rb") as handle:
            self.assertTrue(handle.read().startswith(before))

    def test_tail_incomplete_alarms_on_the_second_run(self):
        # source: an unterminated last line is tail_incomplete, not broken, and the
        # same tail on the next run prints ALARM: spine tail incomplete.
        path = self._nodes()
        os.makedirs(self.spine, exist_ok=True)
        frag = b'{"id":"torn"'
        with open(path, "wb") as handle:
            handle.write(b'{"id":"a"}\n' + frag)
        direct = spine_verify.verify_file(path)
        self.assertEqual(direct["tail_incomplete"], len(frag))
        self.assertEqual(direct["broken"], 0)
        self.assertEqual(direct["legacy"], 1)
        first = self._cli(self.spine, use_flag=False)
        self.assertEqual(first.returncode, 0)
        self.assertIn("tail_incomplete=%d bytes" % len(frag), first.stdout)
        self.assertNotIn("ALARM", first.stdout)
        self.assertTrue(first.stdout.strip().splitlines()[-1].startswith(
            "spine-verify: state=did_work "))
        self.assertFalse(os.path.exists(os.path.join(self.spine, ".verify-heads.json")))
        second = self._cli(self.spine, use_flag=False)
        self.assertEqual(second.returncode, 1)
        self.assertIn(
            "ALARM: spine tail incomplete %s" % spine_verify.report_path(path),
            second.stdout,
        )
        self.assertIn("broken=0", second.stdout.strip().splitlines()[-1])
        self.assertIn("state=could_not", second.stdout.strip().splitlines()[-1])

    def test_torn_line_sealed_by_next_append_blames_only_itself(self):
        # source: review of the first public push (2026-11-12). A crash leaves a
        # torn tail; the next append adds a newline and links to the last
        # complete row, so only the torn line is broken.
        sp, rows = self._append_three()
        with open(self._nodes(), "ab") as handle:
            handle.write(b'{"torn": tr')
        fourth = sp.append("nodes", {"id": "n4", "payload": {"text": "delta"}})
        self.assertEqual(fourth["prev_row_hash"], rows[2]["row_hash"])
        rec = spine_verify.verify_file(self._nodes())
        self.assertEqual(rec["broken_lines"], [4])
        self.assertEqual(rec["chained"], 4)
        self.assertEqual(rec["tail_incomplete"], 0)

    def test_rewrite_from_scratch_keeps_alarming(self):
        # source: a file replaced by a fresh valid chain prints
        # ALARM: spine history rewritten, and the state file keeps the old head
        # so the alarm repeats. A copy of the old hash inside a payload does not
        # count. This goes red if the remembered head is not checked.
        sp = Spine(self.spine)
        sp.append("nodes", {"id": "a", "payload": {"text": "one"}})
        second = sp.append("nodes", {"id": "b", "payload": {"text": "two"}})
        first = self._cli(self.spine)
        self.assertEqual(first.returncode, 0)
        path = self._nodes()
        os.remove(path)
        sp.append("nodes", {"id": "c", "payload": {"text": second["row_hash"]}})
        with open(path, encoding="utf-8") as handle:
            fresh = handle.read()
        self.assertIn(second["row_hash"], fresh)
        again = self._cli(self.spine)
        self.assertEqual(again.returncode, 1)
        self.assertIn(
            "ALARM: spine history rewritten %s: previous head %s missing"
            % (spine_verify.report_path(path), second["row_hash"]),
            again.stdout,
        )
        self.assertIn("broken=0", again.stdout.strip().splitlines()[-1])
        with open(self.state, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved[os.path.abspath(path)]["head"], second["row_hash"])
        third = self._cli(self.spine)
        self.assertEqual(third.returncode, 1)
        self.assertIn("spine history rewritten", third.stdout)
        with open(self.state, encoding="utf-8") as handle:
            kept = json.load(handle)
        self.assertEqual(kept[os.path.abspath(path)]["head"], second["row_hash"])

    def test_deleted_file_raises_ledger_missing(self):
        # source: a remembered file that is gone prints ALARM: spine ledger missing
        # and the state file keeps that head.
        sp = Spine(self.spine)
        row = sp.append("nodes", {"id": "only"})
        self.assertEqual(self._cli(self.spine).returncode, 0)
        path = self._nodes()
        os.remove(path)
        missing = self._cli(self.spine)
        self.assertEqual(missing.returncode, 1)
        self.assertIn(
            "ALARM: spine ledger missing %s: previous head %s"
            % (spine_verify.report_path(path), row["row_hash"]),
            missing.stdout,
        )
        self.assertNotIn("no_work", missing.stdout)
        again = self._cli(self.spine)
        self.assertEqual(again.returncode, 1)
        self.assertIn("spine ledger missing", again.stdout)
        with open(self.state, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved[os.path.abspath(path)]["head"], row["row_hash"])

    def test_row_count_drop_alarms_while_head_remains(self):
        # source: a row count that dropped while the remembered head is still a
        # row_hash prints ALARM: spine row count dropped, and the stored count stays.
        sp = Spine(self.spine)
        for i in range(3):
            sp.append("nodes", {"id": "n%d" % i})
        self.assertEqual(self._cli(self.spine).returncode, 0)
        path = self._nodes()
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(lines[0])
            handle.write(lines[2])
        dropped = self._cli(self.spine)
        self.assertEqual(dropped.returncode, 1)
        self.assertIn(
            "ALARM: spine row count dropped %s: previous rows 3 now 2"
            % spine_verify.report_path(path),
            dropped.stdout,
        )
        self.assertNotIn("history rewritten", dropped.stdout)
        with open(self.state, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved[os.path.abspath(path)]["rows"], 3)

    def test_exit_codes_clean_and_broken(self):
        # source: exit 0 when nothing is broken and no alarm fired, exit 1 when a
        # chained row was edited. A directory contributes both ledger names; a
        # file argument verifies that file only. State stays on --state.
        sp = Spine(self.spine)
        sp.append("nodes", {"id": "n1", "payload": {"text": "alpha"}})
        sp.edge("TRACES", "n1", "n2")
        clean = self._cli(self.spine)
        self.assertEqual(clean.returncode, 0)
        last = clean.stdout.strip().splitlines()[-1]
        self.assertEqual(
            last,
            "spine-verify: state=did_work files=2 chained=2 legacy=0 broken=0",
        )
        self.assertNotIn("ALARM", clean.stdout)
        self.assertTrue(os.path.isfile(self.state))
        self.assertFalse(os.path.exists(os.path.join(self.spine, ".verify-heads.json")))

        only = self._cli(self._nodes())
        self.assertEqual(only.returncode, 0)
        self.assertEqual(
            only.stdout.strip().splitlines()[-1],
            "spine-verify: state=did_work files=1 chained=1 legacy=0 broken=0",
        )
        self.assertNotIn("edges.jsonl", only.stdout)

        path = self._nodes()
        with open(path, "rb") as handle:
            raw = handle.read()
        with open(path, "wb") as handle:
            handle.write(raw.replace(b"alpha", b"alphb", 1))
        broken = self._cli(self.spine)
        self.assertEqual(broken.returncode, 1)
        last_broken = broken.stdout.strip().splitlines()[-1]
        self.assertTrue(last_broken.startswith("spine-verify: state=could_not "))
        self.assertNotEqual(last_broken.rsplit("broken=", 1)[-1], "0")
        self.assertIn("broken %s:1" % spine_verify.report_path(path), broken.stdout)


if __name__ == "__main__":
    unittest.main()
