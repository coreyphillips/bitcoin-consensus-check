import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import chain_replay as chain


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.meta = {"format": "bcs-block-corpus", "format_version": 1,
                     "counts": {"events": 5, "blocks": 0, "headers": 1, "invalidate": 0,
                                "restart": 1, "checks": 2, "normative_checks": 1}}
        checkpoint = {"type": "check", "tip": "fixture-tip", "height": 1, "utxo":
                      {"hash_serialized_3": "frozen", "txouts": 2}, "normative": True, "n": 1}
        self.events = [{"type": "reset", "network": "regtest", "genesis": "genesis", "options": {}},
                       {"type": "headers", "headers": [], "via": "rpc", "now": 42},
                       dict(checkpoint, normative=False), {"type": "restart", "options": {}},
                       dict(checkpoint, n=2)]
        self.operations = []
        self.header_outcome = "accepted"

    def handle(self, request):
        op = request["op"]
        self.operations.append(op)
        return {"chain_reset": {"genesis": "genesis"}, "chain_process_headers": {"result": self.header_outcome},
                "chain_tip": {"tip": "fixture-tip", "height": 1},
                "utxo_hash": {"hash_serialized_3": "actual", "txouts": 2}}[op]

    def replay(self):
        result = chain.empty_result()
        harness = Mock()
        harness.handle.side_effect = self.handle
        upstream = Mock()
        upstream.event_request.return_value = {"op": "chain_process_headers"}
        chain.replay_events(Path("fixture"), self.meta, self.events, harness, upstream, result)
        return result

    def test_restart_and_checkpoint_comparison(self):
        result = self.replay()
        self.assertEqual(self.operations.count("chain_process_headers"), 2)
        self.assertEqual(result["decisions"], {"1": "accepted"})
        self.assertEqual(result["checks"], 2)
        self.assertEqual(result["diagnostics"]["utxo_different"], 1)
        self.assertEqual([(d["event"], d["field"]) for d in result["differences"]], [(4, "utxo")])

    def test_incomplete_fixture_and_unknown_result_fail(self):
        with self.assertRaisesRegex(ValueError, "corpus counts"):
            chain.verify_counts(self.meta, self.events[:3])
        self.header_outcome = "not-supported"
        with self.assertRaisesRegex(RuntimeError, "unexpected chain_process_headers disposition"):
            self.replay()

    def test_worker_failure_and_timeout_are_reported(self):
        with patch.object(chain.subprocess, "Popen") as popen, patch.object(chain.os, "killpg") as kill:
            proc = popen.return_value
            proc.pid = 123
            proc.communicate.side_effect = [subprocess.TimeoutExpired("worker", 1), (b"", b"")]
            result = chain.run_scenario(Path("."), Path("bitcoind"), Path("scenario"))
            self.assertTrue(result["errors"])
            self.assertEqual(result["checks"], 0)
            kill.assert_called_once_with(123, chain.signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
