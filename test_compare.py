import unittest

from chain_replay import empty_result
from compare import scenario_results


def run_result(disposition):
    result = empty_result()
    result.update(checks=2, decisions={"7": disposition})
    return {"scenario": result}


class CompareTests(unittest.TestCase):
    def test_changed_decision_with_same_checkpoints(self):
        status, rows = scenario_results(run_result("accepted"), run_result("inconclusive"))
        self.assertEqual(status, "differences")
        self.assertEqual(rows["scenario"]["decision_differences"],
                         [{"event": 7, "baseline": "accepted", "candidate": "inconclusive"}])

    def test_bad_baseline_or_missing_observation_makes_outcome_inconclusive(self):
        baseline = run_result("accepted")
        candidate = run_result("invalid")
        baseline["scenario"]["differences"].append({"event": 4, "field": "utxo"})
        self.assertEqual(scenario_results(baseline, candidate)[0], "inconclusive")
        candidate["scenario"]["decisions"].clear()
        self.assertEqual(scenario_results(run_result("accepted"), candidate)[0], "inconclusive")

    def test_matching_dispositions_do_not_mask_changed_utxo(self):
        candidate = run_result("accepted")
        candidate["scenario"]["differences"].append({"event": 4, "field": "utxo"})
        self.assertEqual(scenario_results(run_result("accepted"), candidate)[0], "differences")
