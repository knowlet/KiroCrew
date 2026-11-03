import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from skill_script_helpers import load_skill_script

REPO = "example/fork"
SINCE = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
META = {"full_name": REPO, "fork": True}
PR = {"state": "closed", "merged_at": None, "base": {"repo": {"full_name": REPO}}}


class ForkGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_skill_script(
            "fix_loop_metrics_fork",
            Path(__file__).resolve().parents[1] / ".github/scripts/fix_loop_metrics.py",
        )

    def check(self, metadata: object, pages: object) -> bool:
        with patch.object(self.mod, "gh_json", side_effect=[metadata, pages]) as gh:
            result = self.mod._fork_without_merged_prs(REPO)
            if len(gh.call_args_list) == 2:
                self.assertEqual(
                    gh.call_args_list[1].args,
                    (
                        "api", f"repos/{REPO}/pulls?state=closed&per_page=100",
                        "--paginate", "--slurp",
                    ),
                )
            return result

    def test_empty_fork(self) -> None:
        self.assertTrue(self.check(META, [[]]))

    def test_multiple_pages_unmerged(self) -> None:
        self.assertTrue(self.check(META, [[PR] * 100, [PR]]))

    def test_later_page_merged(self) -> None:
        self.assertFalse(
            self.check(META, [[PR] * 100, [dict(PR, merged_at="2026-01-01T00:00:00Z")]])
        )

    def test_nonfork_or_bad_metadata(self) -> None:
        for metadata in [
            None, [], {}, {"fork": True}, dict(META, fork=False),
            dict(META, full_name="other/repo"),
        ]:
            with self.subTest(metadata=metadata):
                self.assertFalse(self.check(metadata, [[]]))

    def test_incomplete_pages(self) -> None:
        for pages in [
            None, [], {}, [None], [{}], [[{}]], [[dict(PR, state="open")]],
            [[dict(PR, base={})]], [[dict(PR, base={"repo": {"full_name": "other/repo"}})]],
        ]:
            with self.subTest(pages=pages), self.assertRaises(RuntimeError):
                self.check(META, pages)

    def test_metadata_permission_error(self) -> None:
        with (
            patch.object(self.mod, "gh_json", side_effect=RuntimeError("403")),
            self.assertRaisesRegex(RuntimeError, "403"),
        ):
            self.mod._fork_without_merged_prs(REPO)

    def test_pagination_failure(self) -> None:
        with (
            patch.object(self.mod, "gh_json", side_effect=[META, RuntimeError("page 2 HTTP 500")]),
            self.assertRaisesRegex(RuntimeError, "HTTP 500"),
        ):
            self.mod._fork_without_merged_prs(REPO)

    def test_real_gh_wrapper_rejects_partial_stdout(self) -> None:
        partial = subprocess.CompletedProcess([], 1, "[[", "page 2 HTTP 403")
        with (
            patch.object(self.mod.subprocess, "run", return_value=partial),
            self.assertRaisesRegex(RuntimeError, "403"),
        ):
            self.mod.gh_json("api", "unused", "--paginate", "--slurp")

    def test_malformed_json_fails(self) -> None:
        with patch.object(self.mod, "run", return_value="[[broken"), self.assertRaises(ValueError):
            self.mod.gh_json("api", "unused")

    def test_empty_wrapper_output_not_valid_pages(self) -> None:
        with (
            patch.object(self.mod, "gh_json", side_effect=[META, []]),
            self.assertRaises(RuntimeError),
        ):
            self.mod._fork_without_merged_prs(REPO)

    def test_merged_search_empty_fork_passes(self) -> None:
        def gh(*args: str) -> list | dict:
            if args[0] == "pr":
                return []
            return META if args[1] == f"repos/{REPO}" else [[]]
        with (
            patch.object(self.mod, "gh_json", side_effect=gh),
            patch.object(self.mod, "_local_pr_evidence", return_value=True),
        ):
            self.assertEqual(self.mod.merged_prs_since(REPO, SINCE), [])

    def test_nonfork_keeps_existing_error(self) -> None:
        with (
            patch.object(self.mod, "gh_json", return_value=[]),
            patch.object(self.mod, "_local_pr_evidence", return_value=True),
            self.assertRaisesRegex(RuntimeError, "pull-requests: read"),
        ):
            self.mod.merged_prs_since(REPO, SINCE)

    def test_closed_pr_first_page_forbidden(self) -> None:
        with (
            patch.object(self.mod, "gh_json", side_effect=[META, RuntimeError("HTTP 403")]),
            self.assertRaisesRegex(RuntimeError, "403"),
        ):
            self.mod._fork_without_merged_prs(REPO)

    def test_nonempty_search_skips_probe(self) -> None:
        pr = {"number": 1, "mergedAt": SINCE}
        with (
            patch.object(self.mod, "gh_json", return_value=[pr]) as gh,
            patch.object(self.mod, "_fork_without_merged_prs") as probe,
        ):
            self.assertEqual(self.mod.merged_prs_since(REPO, SINCE), [pr])
            probe.assert_not_called()
            self.assertTrue(all(c.args[0] == "pr" for c in gh.call_args_list))

    def test_shallow_keeps_existing_error(self) -> None:
        with (
            patch.object(self.mod, "gh_json", return_value=[]),
            patch.object(self.mod, "_local_pr_evidence", return_value=None),
            self.assertRaisesRegex(RuntimeError, "pull-requests: read"),
        ):
            self.mod.merged_prs_since(REPO, "2026-01-01T00:00:00Z")

    def test_empty_history_no_extra_probe(self) -> None:
        with (
            patch.object(self.mod, "gh_json", return_value=[]) as gh,
            patch.object(self.mod, "_local_pr_evidence", return_value=False),
        ):
            self.assertEqual(self.mod.merged_prs_since(REPO, SINCE), [])
            self.assertTrue(all(c.args[0] == "pr" for c in gh.call_args_list))

    def test_search_cap_still_fails(self) -> None:
        with (
            patch.object(self.mod, "gh_json", return_value=[{}] * 1000),
            self.assertRaisesRegex(RuntimeError, "1000-item cap"),
        ):
            self.mod.merged_prs_since(REPO, "2026-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
