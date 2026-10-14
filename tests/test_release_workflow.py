"""Release-condition cases from the actual workflow, without network or builds."""

from pathlib import Path
import re
from types import SimpleNamespace
import unittest


class ReleaseWorkflowTests(unittest.TestCase):
    def test_required_release_survives_skipped_build_but_keeps_safety_gates(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/macos-app.yml"
        ).read_text()
        release = workflow.split("\n  release:\n", 1)[1]
        condition = release.split("if:", 1)[1].split("\n    needs:", 1)[0].strip()
        if condition.startswith(">"):
            condition = " ".join(condition.splitlines()[1:]).strip()
        condition = condition.removeprefix("${{").removesuffix("}}").strip()
        expression = (
            condition.replace("&&", " and ")
            .replace("||", " or ")
            .replace("!cancelled()", "not cancelled()")
        )
        defaults = dict(
            cancelled=False,
            plan="success",
            checks="success",
            required="true",
            repository="Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon",
            event="push",
            ref="refs/heads/main",
        )
        cases = [("required release with intentionally skipped build", {}, True)]
        for key, values in (
            ("cancelled", [True]),
            ("plan", ["failure", "cancelled", "skipped", ""]),
            ("checks", ["failure", "cancelled", "skipped", ""]),
            ("required", ["false", "", True]),
            ("repository", ["ageekhere/Definitive-SMR-Launcher"]),
            ("event", ["pull_request"]),
            ("ref", ["refs/heads/topic", "refs/tags/v0.14.0"]),
        ):
            cases.extend((f"{key}={value}", {key: value}, False) for value in values)
        cases += [
            ("manual " + name, dict(changes, event="workflow_dispatch"), expected)
            for name, changes, expected in cases
            if "event" not in changes
        ]
        for name, changes, expected in cases:
            with self.subTest(name=name):
                values = dict(defaults, **changes)
                context = dict(
                    cancelled=lambda: values["cancelled"],
                    needs=SimpleNamespace(
                        plan=SimpleNamespace(
                            result=values["plan"],
                            outputs=SimpleNamespace(release=values["required"]),
                        ),
                        checks=SimpleNamespace(result=values["checks"]),
                    ),
                    github=SimpleNamespace(
                        repository=values["repository"],
                        event_name=values["event"],
                        ref=values["ref"],
                    ),
                )
                # GitHub applies implicit success() when no status function is
                # present. The observed run has a deliberately skipped build
                # ancestor, so that implicit condition blocks the release.
                explicit_status = re.search(r"\b(?:cancelled|always|failure|success)\(", condition)
                actual = bool(explicit_status) and eval(expression, {"__builtins__": {}}, context)
                self.assertEqual(actual, expected)
        self.assertIn("needs: [plan, checks]", release)
