"""Execute reporting workflow shells offline; no GitHub or model calls."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gui-user-test.yml"
STEPS = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["gui-user-test"]["steps"]


@unittest.skipUnless(shutil.which("bash"), "workflow requires Bash")
class TestGuiReportingFallback(unittest.TestCase):
    def run_step(
        self,
        name,
        *,
        outcomes=("success", "failure", "skipped"),
        capability="false",
        api_rc="0",
        issue_rc="0",
        friction=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "results"
            results.mkdir()
            if friction:
                (results / "friction_ledger.json").write_text("{}", encoding="utf-8")
                (results / "friction.json").write_text("{}", encoding="utf-8")
            stub = root / "gh"
            stub.write_text(
                '#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n'
                'if [ "$1" = api ]; then echo "$CAPABILITY"; exit "$API_RC"; fi\n'
                'if [ "$1 $2" = "issue create" ]; then exit "$ISSUE_RC"; fi\n'
                'exit 0\n', encoding="utf-8",
            )
            stub.chmod(0o755)
            env = {
                **os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "GUI_OUT": str(root), "RUNNER_TEMP": str(root),
                "GITHUB_STEP_SUMMARY": str(root / "step.md"),
                "GITHUB_OUTPUT": str(root / "outputs"),
                "CALLS": str(root / "calls"), "CAPABILITY": capability,
                "API_RC": api_rc, "ISSUE_RC": issue_rc,
                "BOOT_OUTCOME": outcomes[0], "CREDENTIAL_OUTCOME": outcomes[1],
                "RUN_OUTCOME": outcomes[2], "VERDICT": "ERROR", "DATE": "2026-01-01",
                "RUN_URL": "https://example.test/run", "ARTIFACT_URL": "",
                "SHA": "abc123", "REPO": "example/project",
            }
            script = next(step["run"] for step in STEPS if step.get("name") == name)
            result = subprocess.run(
                ["bash", "-e", "-c", script], env=env, cwd=root,
                capture_output=True, encoding="utf-8", errors="replace",
            )
            files = {
                path.name: path.read_text(encoding="utf-8")
                for path in root.iterdir()
                if path.is_file()
            }
            return result, files

    def test_missing_summary_reports_observed_outcomes(self):
        for outcomes in [
            ("success", "failure", "skipped"),
            ("failure", "skipped", "skipped"),
            ("success", "success", "failure"),
            ("success", "success", "success"),
            ("skipped", "skipped", "skipped"),
        ]:
            with self.subTest(outcomes=outcomes):
                result, files = self.run_step("Summarize", outcomes=outcomes)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("verdict=ERROR", files["outputs"])
                self.assertIn("boot=%s; credentials=%s; harness=%s" % outcomes, files["step.md"])
                self.assertNotIn("target did not boot", files["step.md"])

    def test_disabled_issues_retains_report_without_issue_calls(self):
        result, files = self.run_step("File or update the nightly report issue")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("issues are disabled", files["step.md"])
        self.assertEqual(files["calls"].splitlines(), ["api repos/example/project --jq .has_issues"])
        self.assertIn("boot=success; credentials=failure; harness=skipped", files["gui-issue-body.md"])
        self.assertNotIn("target did not boot", files["gui-issue-title.txt"])

    def test_disabled_friction_does_not_file(self):
        result, files = self.run_step("File new-user friction issues", friction=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("issues are disabled", files["step.md"])
        self.assertEqual(len(files["calls"].splitlines()), 1)

    def test_capability_failure_or_unknown_is_not_disabled(self):
        for name in ["File or update the nightly report issue", "File new-user friction issues"]:
            for capability, api_rc in [("", "1"), ("null", "0"), ("", "0")]:
                with self.subTest(name=name, capability=capability, api_rc=api_rc):
                    result, files = self.run_step(
                        name, capability=capability, api_rc=api_rc, friction=True
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(files["calls"].splitlines()), 1)

    def test_enabled_issues_still_files_and_failure_is_red(self):
        for issue_rc in ["0", "1"]:
            with self.subTest(issue_rc=issue_rc):
                result, files = self.run_step(
                    "File or update the nightly report issue",
                    capability="true",
                    issue_rc=issue_rc,
                )
                self.assertEqual(result.returncode, int(issue_rc), result.stderr)
                self.assertIn("issue create", files["calls"])

    def test_missing_results_remains_failed_lane(self):
        result, _ = self.run_step("Lane status")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("harness outcome: skipped", result.stdout)


if __name__ == "__main__":
    unittest.main()
