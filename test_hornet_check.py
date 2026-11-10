import unittest

from hornet_check import GENESIS, judge


class HornetScoringTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        for network, (block_hash, bits) in GENESIS.items():
            hornet = {"target": "target", "work": "work"}
            core = {"header": "header", "hash": block_hash, "bits": bits, **hornet}
            self.rows[network] = {"hornet": hornet, "baseline": dict(core), "candidate": dict(core)}

    def test_disagreements_have_a_trusted_baseline(self):
        self.assertEqual(judge(self.rows), ("agreement", []))
        self.rows["signet"]["candidate"]["target"] = "other"
        status, differences = judge(self.rows)
        self.assertEqual(status, "differences")
        self.assertEqual([(d["field"], d["implementation"]) for d in differences], [("target", "candidate")])
        self.rows["signet"]["baseline"]["target"] = "other"
        status, differences = judge(self.rows)
        self.assertEqual(status, "inconclusive")
        self.assertEqual(len(differences), 2)

    def test_wrong_identity_and_missing_coverage(self):
        self.rows["regtest"]["candidate"]["hash"] = "other-hash"
        self.assertEqual(judge(self.rows)[0], "differences")
        del self.rows["main"]
        with self.assertRaisesRegex(ValueError, "three distinct"):
            judge(self.rows)
