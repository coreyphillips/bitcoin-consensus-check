import io
import json
import unittest
from unittest.mock import patch

from release_tags import BASELINE_COMMIT, tags_to_check, verify_tag


TAG_SHA = "1" * 40
COMMIT = "2" * 40


def refs(tag, commit=COMMIT):
    return f"{TAG_SHA} refs/tags/{tag}\n{commit} refs/tags/{tag}^{{}}\n"


class ReleaseTagsTest(unittest.TestCase):
    def test_sweep_checks_each_rc_only_while_its_release_is_open(self):
        lines = refs("v32.1", BASELINE_COMMIT)
        for tag in ("v31.2rc1", "v32.2rc1", "v32.2", "v32.3rc1", "v33.0rc1", "v33.0rc2"):
            lines += refs(tag)
        self.assertEqual([r["tag"] for r in tags_to_check(lines, None)],
                         ["v32.3rc1", "v33.0rc1", "v33.0rc2"])
        self.assertEqual(tags_to_check(lines, "v32.2rc1")[0]["commit"], COMMIT)

    def test_missing_lightweight_and_moved_tags_fail(self):
        baseline = refs("v32.1", BASELINE_COMMIT)
        for tag in ("v33.0rc1", "main", "v33.0rc1\n"):
            with self.assertRaises(ValueError):
                tags_to_check(baseline, tag)
        with self.assertRaises(ValueError):
            tags_to_check(baseline + f"{TAG_SHA} refs/tags/v33.0rc1\n", "v33.0rc1")
        with self.assertRaises(ValueError):
            tags_to_check(refs("v32.1"), None)

    def test_signature_must_match_both_tag_object_and_peeled_commit(self):
        release = {"tag": "v33.0rc1", "object": TAG_SHA, "commit": COMMIT}
        valid = {"tag": "v33.0rc1", "sha": TAG_SHA, "object": {"sha": COMMIT, "type": "commit"},
                 "verification": {"verified": True}}
        for overrides in ({}, {"tag": "v33.1"}, {"sha": COMMIT}, {"verification": {"verified": False}},
                          {"object": {"sha": TAG_SHA, "type": "commit"}}):
            metadata = io.BytesIO(json.dumps(dict(valid, **overrides)).encode())
            with patch("release_tags.urllib.request.urlopen", return_value=metadata):
                if overrides:
                    with self.assertRaises(ValueError):
                        verify_tag(release)
                else:
                    verify_tag(release)
