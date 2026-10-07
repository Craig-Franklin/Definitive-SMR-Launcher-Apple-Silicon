"""Private, input-bound gameplay observations; static checks never earn a badge."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
import json

from .activation import _assert_no_symlink_ancestor, _atomic_json


CHECKS = (
    "scenario_loaded",
    "gameplay",
    "autosave_written",
    "manual_save_written",
    "manual_save_reloaded",
    "continued_after_reload",
)


class VerificationError(ValueError):
    """The private gameplay record needs inspection."""


@dataclass(frozen=True)
class GameplayVerification:
    archive_sha256: str
    game_executable_sha256: str
    variant_id: str
    assets_sha256: str
    observed_at: str
    reporter: str
    checks: dict[str, bool]
    scope: str
    issue: str = ""
    resources_sha256: str = ""

    def __post_init__(self) -> None:
        for value in (self.archive_sha256, self.game_executable_sha256,
                      self.variant_id, self.assets_sha256):
            if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise VerificationError("Gameplay record needs exact SHA-256 identities")
        if (not isinstance(self.resources_sha256, str) or
                (self.resources_sha256 and (len(self.resources_sha256) != 64 or
                 any(char not in "0123456789abcdef" for char in self.resources_sha256)))):
            raise VerificationError("Gameplay resource identity must be a SHA-256 digest")
        try:
            observed = datetime.fromisoformat(self.observed_at)
        except (ValueError, TypeError) as exc:
            raise VerificationError("Gameplay record date is invalid") from exc
        if observed.tzinfo is None:
            raise VerificationError("Gameplay record needs a dated time zone")
        if (not isinstance(self.reporter, str) or not isinstance(self.scope, str)
                or not isinstance(self.issue, str) or not self.reporter.strip()
                or len(self.reporter) > 120 or not self.scope.strip()
                or len(self.scope) > 500 or len(self.issue) > 500):
            raise VerificationError("Gameplay record needs bounded reporter and scope")
        if (not isinstance(self.checks, dict) or set(self.checks) != set(CHECKS)
                or any(type(value) is not bool for value in self.checks.values())):
            raise VerificationError("Gameplay record has incomplete checks")

    @property
    def status(self) -> str:
        # Every six-check report is an unscoped limited fact, including reports
        # that recorded resource metadata. None supplies named scenario actions.
        return reduce_verification((), (), {}, historical=(self,)).status

    def as_json(self) -> dict:
        return dict(archive_sha256=self.archive_sha256,
                    game_executable_sha256=self.game_executable_sha256,
                    variant_id=self.variant_id, assets_sha256=self.assets_sha256,
                    observed_at=self.observed_at, reporter=self.reporter,
                    checks=self.checks, scope=self.scope, issue=self.issue,
                    resources_sha256=self.resources_sha256)

    @classmethod
    def from_json(cls, value: dict) -> "GameplayVerification":
        if not isinstance(value, dict):
            raise VerificationError("Gameplay record is not an object")
        try:
            return cls(value["archive_sha256"], value["game_executable_sha256"],
                       value["variant_id"], value["assets_sha256"],
                       value["observed_at"], value["reporter"], value["checks"],
                       value["scope"], value.get("issue", ""), value.get("resources_sha256", ""))
        except (KeyError, TypeError) as exc:
            raise VerificationError("Gameplay record is incomplete") from exc


class VerificationStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def records(self) -> tuple[GameplayVerification, ...]:
        _assert_no_symlink_ancestor(self.path)
        if not self.path.exists():
            return ()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("schema") != 1 or not isinstance(document.get("records"), list):
                raise VerificationError("Gameplay record file has an unexpected schema")
            if len(document["records"]) > 1000:
                raise VerificationError("Gameplay record file exceeds the entry limit")
            return tuple(GameplayVerification.from_json(value) for value in document["records"])
        except (OSError, ValueError, AttributeError) as exc:
            raise VerificationError("Gameplay record file needs inspection") from exc

    def latest(self, archive_sha256: str, game_executable_sha256: str,
               variant_id: str, assets_sha256: str, *,
               resources_sha256: Optional[str] = None) -> Optional[GameplayVerification]:
        matched = [
            item for item in self.records()
            if (item.archive_sha256, item.game_executable_sha256,
                item.variant_id, item.assets_sha256) ==
               (archive_sha256, game_executable_sha256, variant_id, assets_sha256)
            and (resources_sha256 is None or item.resources_sha256 == resources_sha256)
        ]
        return matched[-1] if matched else None

    def append(self, record: GameplayVerification) -> None:
        existing = list(self.records())
        if len(existing) >= 1000:
            raise VerificationError("Gameplay record entry limit reached")
        existing.append(record)
        _atomic_json(self.path, dict(schema=1, records=[item.as_json() for item in existing]))


@dataclass(frozen=True)
class ScenarioStatus:
    scenario_path: str
    scenario_sha256: str
    actions: tuple[tuple[str, str], ...]
    status: str
    limitations: tuple[str, ...] = ()

    def as_json(self):
        return dict(scenario_path=self.scenario_path, scenario_sha256=self.scenario_sha256,
                    actions=dict(self.actions), status=self.status, limitations=list(self.limitations))


@dataclass(frozen=True)
class LimitedScenarioObservation:
    record: object
    interpretation: object = None

    @property
    def issue(self):
        outcome = (self.interpretation.payload["outcome"] if self.interpretation is not None
                   else self.record.payload["outcome"])
        if outcome != "failure":
            return ""
        return "Historical scenario issue (inputs not fully attested): " + self.record.payload["scenario"]["scenario_path"]

    def as_json(self):
        return {"record": self.record.value,
                "interpretation": self.interpretation.value if self.interpretation is not None else None,
                "limitation": "Original scenario observation retained; input completeness was not attested."}


@dataclass(frozen=True)
class VerificationSummary:
    scenarios: tuple[ScenarioStatus, ...]
    status: str
    historical: tuple[GameplayVerification, ...] = ()
    scope: str = "Scenario coverage; missing actions remain unknown."
    limited: tuple[LimitedScenarioObservation, ...] = ()

    @property
    def issue(self):
        failures = [s.scenario_path for s in self.scenarios if s.status == "Known issue"]
        warnings = [r.issue for r in self.historical if r.issue]
        return "\n".join([*("Observed issue: " + s for s in failures),
                           *("Historical unscoped issue: " + x for x in warnings),
                           *(item.issue for item in self.limited if item.issue)])

    def as_json(self):
        return dict(status=self.status, scenarios=[s.as_json() for s in self.scenarios],
                    historical=[dict(record=r.as_json(), limitation="Unscoped limited observation")
                                for r in self.historical], scope=self.scope, limited=[r.as_json() for r in self.limited])


def reduce_verification(records, scenarios, context, *, historical=()):
    """The sole scenario/action/edition status authority.

    Current claims require exact scenario + build context + versioned contract.
    Unknown applicability is not N/A. Static findings never enter action results.
    Logging provenance is deliberately excluded from observed engine inputs.
    """
    from .scenario_evidence import REQUIRED_ACTIONS, FEATURES, SCHEMA, context_identity, canonical
    records, scenarios = tuple(records), tuple(scenarios)
    by_id = {r.id:r for r in records}
    current_identity = context_identity(context)
    raw_context_identity = context_identity(context, require_complete=False)
    contracts = [r for r in records if r.kind == "contract"]
    observations = [r for r in records if r.kind == "observation"]
    interpretations = {}
    interpretation_records = {}
    for r in records:
        if r.kind == "interpretation":
            interpretations[r.payload["observation"]] = r.payload
            interpretation_records[r.payload["observation"]] = r
    results = []
    for scenario in scenarios:
        actions = dict.fromkeys(REQUIRED_ACTIONS, "unknown")
        limitations = []
        matched = []
        for c in contracts:
            p = c.payload
            b = by_id[p["build"]].payload
            if p["version"] == SCHEMA and c.value["schema"] == SCHEMA and canonical(p["scenario"]) == canonical(scenario) and current_identity is not None and by_id[p["build"]].value["schema"] == SCHEMA and context_identity(b["context"]) == current_identity:
                matched.append(c)
        contract = matched[-1] if matched else None
        complete = bool(contract)
        if contract is None:
            limitations.append("Action applicability and feature expectations have not been reviewed.")
        else:
            cp = contract.payload
            complete = cp["version"] == SCHEMA
            for f in FEATURES:
                if cp["features"][f]["state"] != "reviewed":
                    complete = False
                    limitations.append("Unresolved expectation: " + f)
            for action in REQUIRED_ACTIONS:
                a = cp["actions"][action]
                if a["applicability"] == "unknown":
                    complete = False
                    continue
                if a["applicability"] == "not_applicable":
                    actions[action] = "not_applicable"
                    continue
                outcomes = []
                for r in records:
                    if r.kind != "observation":
                        continue
                    p = r.payload
                    if canonical(p["scenario"]) != canonical(scenario) or p["contract"] != contract.id or p["action"] != action or context_identity(p["actual_context"]) != current_identity:
                        continue
                    i = interpretations.get(r.id)
                    if i is None:
                        continue
                    # Historical facts and partial observations cannot gain passes by reanalysis.
                    outcome = i["outcome"]
                    if outcome == "pass" and (p["outcome"] != "pass" or
                            p["actual_assertions"].get("expected") != p["protocol"]["expected"] or
                            p["actual_assertions"].get("satisfied") is not True):
                        outcome = "unknown"
                    if outcome == "pass" and any(p["actual_assertions"].get(k) != expected
                            for k, expected in p["protocol"].get("required_assertions", {}).items()):
                        outcome = "unknown"
                    if outcome == "pass" and any(p["actual_assertions"].get("features", {}).get(k) != expected
                            for k, expected in p["protocol"].get("feature_assertions", {}).items()):
                        outcome = "unknown"
                    outcomes.append(outcome)
                if "failure" in outcomes:
                    actions[action] = "failure"
                elif outcomes:
                    actions[action] = outcomes[-1]
        # A valid raw-first ledger can contain pending observations. They
        # cannot be ignored because earlier complete passes happened to exist.
        # Failure stays Known issue until explicitly interpreted/withdrawn;
        # every other pending outcome conservatively blocks that action.
        for raw in observations:
            op = raw.payload
            if canonical(op["scenario"]) != canonical(scenario) or raw_context_identity is None or context_identity(op["actual_context"], require_complete=False) != raw_context_identity:
                continue
            ip = interpretations.get(raw.id)
            if ip is None:
                if op["outcome"] == "failure":
                    actions[op["action"]] = "failure"
                elif actions[op["action"]] != "failure":
                    actions[op["action"]] = "unknown"
                complete = False
                limitations.append("An observation is awaiting review: " + op["action"].replace("_", " "))
            elif ip["outcome"] == "failure":
                actions[op["action"]] = "failure"
                complete = False
        if contract is not None:
            for feature in contract.payload["features"].values():
                if any(actions[a] != "pass" for a in feature["actions"]):
                    complete = False
        if any(v not in ("pass", "not_applicable") for v in actions.values()):
            complete = False
        if current_identity is None:
            limitations.append("Exact game options or resource coverage has not been confirmed.")
        if any(r.value["schema"] != SCHEMA for r in records):
            limitations.append("Older observations are retained with their original scope; incomplete inputs cannot establish current coverage.")
        status = ("Known issue" if "failure" in actions.values() else
                  "Verified" if complete else "Not verified")
        results.append(ScenarioStatus(scenario["scenario_path"], scenario["scenario_sha256"],
                                     tuple(actions.items()), status, tuple(limitations)))
    limited = tuple(LimitedScenarioObservation(raw, interpretation_records.get(raw.id))
                    for raw in observations if raw.value["schema"] != SCHEMA
                    and any(canonical(raw.payload["scenario"]) == canonical(scenario) for scenario in scenarios))
    edition_status = ("Known issue" if any(s.status == "Known issue" for s in results) else
                      "Verified" if results and all(s.status == "Verified" for s in results) else
                      "Known issue" if any(r.issue for r in historical) or any(r.issue for r in limited) else "Not verified")
    return VerificationSummary(tuple(results), edition_status, tuple(historical), limited=limited)
