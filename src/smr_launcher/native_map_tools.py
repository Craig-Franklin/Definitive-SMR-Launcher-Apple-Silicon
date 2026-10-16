"""Contextual native map tools; existing stores and application guards own writes.

Only operational projections cross the bridge. No diagnostic files, save bytes,
prepared paths or recipe internals are returned. Presentation is not authority.
"""

from datetime import datetime
from collections import OrderedDict
import getpass
import secrets
from pathlib import Path

from .activation import ORIGINAL
from .gui import LauncherWindow, MAC_TEST_LABELS, _checks_text
from .history_ui import timeline_text
from .play_history import (
    FAILURES,
    IDENTITIES,
    LABELS,
    HistoryError,
    HistoryWriteOutcomeUnknown,
    PlayHistoryStore,
    context,
    digest,
    issue_number,
    issue_readback,
    same_map,
)
from .verification import CHECKS


def text(params, key):
    value = params.get(key, "")
    if not isinstance(value, str):
        raise ValueError(key + " must be text")
    return value


def confirmed(params):
    if params.get("confirmed") is not True:
        raise ValueError("Explicit native confirmation is required")


def dispatch(library, app, method, params, *, launch=None):
    try:
        return _dispatch(library, app, method, params, launch=launch)
    except Exception as exc:
        readonly = method in (
            "maptools.inspect",
            "maptools.original_context",
            "maptools.issue_url",
            "maptools.play_preflight",
        )
        # A custom removal refusal can occur after catalogue commit or staged
        # moves. The retained transaction journal is authoritative uncertainty.
        pending = Path(app.library) / "removal.json"
        try:
            removal_pending = pending.exists() or pending.is_symlink()
        except OSError:
            removal_pending = True
        if not readonly and (
            isinstance(exc, (OSError, HistoryWriteOutcomeUnknown)) or removal_pending
        ):
            error_type = OSError if isinstance(exc, OSError) else RuntimeError
            raise error_type(
                "Map tool outcome needs inspection; do not repeat the action: " + str(exc)
            ) from exc
        raise


class NativePlayRequests:
    """Bounded one-use launch reviews, scoped to one service session and app owner."""

    def __init__(self):
        self.tickets = OrderedDict()

    @staticmethod
    def inspect(library, app, params):
        profile = text(params, "profile_id")
        prepare = params.get("prepare_compatibility", True)
        if type(prepare) is not bool:
            raise ValueError("prepare_compatibility must be Boolean")
        preparation = None
        expected_plan = None
        if text(params, "game_executable_sha256") != app.installation.executable_sha256:
            raise ValueError("The game build changed; review Play again")
        app._stopped()
        if app.profiles.journal.exists():
            raise ValueError("A profile switch needs recovery; inspect Installation & Recovery")
        if profile == ORIGINAL:
            target = dict(
                profile_id=ORIGINAL,
                archive_sha256="",
                variant_id="",
                game_executable_sha256=app.installation.executable_sha256,
            )
            warning, name = "", "Original Game"
        else:
            record = library.record(app, profile)
            library.require_scope(app, record, params, build=True)
            target = dict(
                profile_id=record.profile_id, **context(record, app.installation.executable_sha256)
            )
            target.pop("scenario")
            verification = app.verification_for(record)
            warning = verification.issue if verification else ""
            name = record.name
            if prepare:
                choices = app.compatibility_recipes(record)
                if len(choices) > 1:
                    warning += "\n\nMultiple compatibility editions are available. Choose one in Map Details; this launch uses the original edition."
                elif choices:
                    choice = choices[0]
                    plan = app.plan_recipe_batch(
                        [dict(profile_id=record.profile_id, recipe_id=choice.recipe_id)]
                    )
                    if plan["all_supported"]:
                        expected_plan = {
                            k: v
                            for k, v in plan["jobs"][0].items()
                            if k not in ("index", "profile_id", "recipe_id", "status")
                        }
                        preparation = dict(
                            recipe_id=choice.recipe_id,
                            title=choice.title,
                            plan_digest=digest("native-play-preparation", plan),
                        )
                        warning += (
                            "\n\n"
                            + choice.title
                            + "\n"
                            + choice.description
                            + "\n\nPrepare or reuse a separate compatibility edition and play it. "
                            "Original map and saves stay intact; existing saves remain on their own edition."
                        )
                    else:
                        warning += (
                            "\n\nCompatibility preparation is unavailable: "
                            + plan["jobs"][0]["reason"]
                            + ". This launch uses the original edition. Inspect Map Details before continuing."
                        )
        warning = warning.strip()
        return dict(
            target=target,
            name=name,
            warning=warning,
            preparation=preparation,
            prepare_compatibility=prepare,
            expected_plan=expected_plan,
            fingerprint=digest("native-play-review", target, warning, prepare, preparation),
        )

    def preflight(self, library, app, params):
        review = self.inspect(library, app, params)
        expected_plan = review.pop("expected_plan")
        ticket = secrets.token_hex(32)
        self.tickets[ticket] = (app, review, expected_plan)
        while len(self.tickets) > 32:
            self.tickets.popitem(last=False)
        return dict(ticket=ticket, **review)

    def commit(self, library, app, params, launch):
        # Consume before revalidation or side effects. No failure permits replay.
        saved = self.tickets.pop(text(params, "ticket"), None)
        if saved is None or saved[0] is not app:
            raise ValueError("This Play review expired or was used; review Play again")
        confirmed(params)
        review = saved[1]
        current = self.inspect(library, app, params)
        if (
            current["target"] != review["target"]
            or current["fingerprint"] != review["fingerprint"]
            or text(params, "fingerprint") != review["fingerprint"]
        ):
            raise ValueError("The Play target or warning changed; review Play again before launch")
        if launch is None:
            raise ValueError("The protected native launch operation is unavailable")
        launch_profile = review["target"]["profile_id"]
        try:
            if review["preparation"] is not None:
                record = library.record(app, launch_profile)
                edition = app.create_recipe_edition(
                    record, review["preparation"]["recipe_id"], expected_plan=saved[2]
                )
                verification = app.verification_for(edition)
                if verification and verification.issue:
                    # The source review did not cover this edition's known issue.
                    # Preparation is complete, but no launch has been attempted.
                    return dict(
                        target=review["target"],
                        prepared_profile_id=edition.profile_id,
                        message=(
                            edition.name
                            + " is ready but has a known issue: "
                            + verification.issue
                            + " Select this edition in the library and review Play before launching."
                        ),
                    )
                launch_profile = edition.profile_id
            launch(launch_profile)
        except Exception as exc:
            # Profile preparation or launch may have progressed before failure.
            # A failure receipt cannot certify that nothing happened.
            raise RuntimeError(
                "Native launch outcome unknown; inspect before trying again: " + str(exc)
            ) from exc
        return dict(
            target=review["target"],
            launched_profile_id=launch_profile,
            message="Railroads launched. Quit it before changing maps.",
        )


def _dispatch(library, app, method, params, *, launch=None):
    """Resolve current identities for every call, then invoke one guarded operation."""
    if method == "maptools.play_preflight":
        return library.play_requests.preflight(library, app, params)
    if method == "maptools.play_commit":
        return library.play_requests.commit(library, app, params, launch)
    if method == "maptools.original_context":
        return dict(game_executable_sha256=app.installation.executable_sha256)
    if method == "maptools.activate_original":
        confirmed(params)
        if text(params, "game_executable_sha256") != app.installation.executable_sha256:
            raise ValueError("The game build changed; inspect current identities first")
        if app.profiles.journal.exists():
            raise ValueError("A profile switch needs recovery; inspect Installation & Recovery")
        app.activate(ORIGINAL)
        return dict(message="Original Game activated without launch. Saved games were preserved.")

    record = library.record(app, text(params, "profile_id"))
    library.require_scope(app, record, params, build=True)
    ctx = context(record, app.installation.executable_sha256)
    store = PlayHistoryStore(app.library)

    if method == "maptools.inspect":
        all_rows = store.records()
        rows = tuple(e for e in all_rows if same_map(e, ctx))
        older = tuple(
            e
            for e in all_rows
            if e["archive_sha256"] == ctx["archive_sha256"] and not same_map(e, ctx)
        )
        # Read failures are explicit; one unavailable section must not hide others.
        result = dict(
            context=ctx,
            scenarios=list(record.scenarios),
            history=[{**e, "label": LABELS[e["kind"]]} for e in rows],
            issues=issue_readback(rows),
            fixes_text=timeline_text(tuple(e for e in rows if e["kind"].startswith("fix_"))),
            scope_text="Current edition and game build\n"
            + "\n".join(f"{key}: {ctx[key]}" for key in IDENTITIES)
            + "\n\nLegacy Mac reports do not invent games, scenario actions or completions. "
            "A closed issue supplies no gameplay evidence. Later success does not clear "
            "an unresolved failure without an exact checkpoint test. Unknown remains unknown. "
            "Issue refresh sends only personal-repository issue numbers; saves and diagnostics stay private."
            + "\n\nExact current event identities\n\n"
            + timeline_text(rows, detailed=True)
            + f"\n\n{len(older)} retained events on other variants/builds (excluded from this card).\n\n"
            + timeline_text(older, detailed=True),
            legacy_checks=[dict(id=k, label=MAC_TEST_LABELS[k]) for k in CHECKS],
        )
        try:
            result["recipes"] = [
                dict(id=r.recipe_id, title=r.title, description=r.description)
                for r in app.compatibility_recipes(record)
            ]
        except Exception as exc:
            result["recipes_error"] = "Compatibility options need inspection: " + str(exc)
        try:
            result["checks_text"] = _checks_text(app.import_checks(record))
        except Exception as exc:
            result["checks_error"] = "Import checks need inspection: " + str(exc)
        try:
            result["legacy_text"] = LauncherWindow._verification_details(
                app._legacy_verification_for(record)
            )
        except Exception as exc:
            result["legacy_error"] = "Legacy Mac reports need inspection: " + str(exc)
        return result

    if method == "maptools.refresh_checks":
        return dict(checks_text=_checks_text(app.import_checks(record, refresh=True)))
    if method == "maptools.issue_url":
        url = text(params, "issue_url")
        issue_number(url)
        rows = tuple(e for e in store.records() if same_map(e, ctx))
        if not any(e["url"] == url for e in issue_readback(rows)):
            raise HistoryError("Choose a linked issue from this exact edition/build")
        return dict(url=url)
    if method == "maptools.refresh_issues":
        rows = tuple(e for e in store.records() if same_map(e, ctx))
        store.refresh_issues(rows)
        library.invalidate()
        return dict(
            message="Issue states refreshed. Issue state is separate from gameplay evidence."
        )

    confirmed(params)
    if method == "maptools.legacy_record":
        scope, issue = text(params, "scope").strip(), text(params, "issue").strip()
        checks = params.get("checks")
        if not scope or len(scope) > 500 or len(issue) > 500:
            raise ValueError(
                "Enter a test scope. Scope and issue must each be 500 characters or fewer."
            )
        if (
            not isinstance(checks, dict)
            or set(checks) != set(CHECKS)
            or any(type(v) is not bool for v in checks.values())
        ):
            raise ValueError("Choose each legacy observation explicitly")
        result = app.record_gameplay(
            record, datetime.now().astimezone().isoformat(), getpass.getuser(), checks, scope, issue
        )
        library.invalidate()
        return dict(message="Legacy Mac test recorded: " + result.status)
    if method in ("maptools.link_issue", "maptools.record_fix"):
        scenario = text(params, "scenario")
        if scenario and scenario not in record.scenarios:
            raise HistoryError("Choose a scenario from this exact map edition")
        ctx = {**ctx, "scenario": scenario}
        if method == "maptools.link_issue":
            scope = text(params, "scope")
            if scope not in ("exact", "related") or (scope == "exact" and not scenario):
                raise HistoryError(
                    "Exact linkage needs a known scenario; choose related scope for unknown"
                )
            store.link(ctx, text(params, "issue_url").strip(), scope=scope)
            library.invalidate()
            return dict(message="Issue linked locally. No GitHub issue was changed.")
        kind = text(params, "kind")
        if kind not in ("fix_proposed", "fix_implemented", "fix_test_passed", "fix_test_failed"):
            raise HistoryError("Choose a fix implementation or test fact")
        token = text(params, "operation_id")
        if not token or len(token) > 100:
            raise HistoryError("A retained confirmation identity is required")
        fields = dict(
            fix_id=text(params, "fix_id") or digest("native-fix", ctx, token),
            issue_url=text(params, "issue_url"),
        )
        if kind in ("fix_test_passed", "fix_test_failed"):
            target = next(
                (
                    e
                    for e in store.records()
                    if e["event_id"] == text(params, "target_id")
                    and same_map(e, ctx)
                    and e["kind"] in FAILURES
                ),
                None,
            )
            if target is None or not target["checkpoint"]:
                raise HistoryError("Choose an attributed failure with a known failed checkpoint")
            fields.update(
                target_id=target["event_id"],
                checkpoint=target["checkpoint"],
                attempt_id=text(params, "attempt_id"),
            )
        # The validated store rejects invalid issue, fix, scenario, open and success
        # attribution. A retained draft identity makes explicit resubmission idempotent.
        event = store.once(ctx, kind, event_id=digest("native-fact", ctx, token), **fields)
        library.invalidate()
        return dict(message=LABELS[kind] + " recorded.", fix_id=event["fix_id"])
    if method == "maptools.create_recipe":
        edition = app.create_recipe_edition(record, text(params, "recipe_id"))
    elif method == "maptools.create_experimental":
        if params.get("difficulty") is not True or params.get("editor", False) is not False:
            raise ValueError("Only the existing experimental custom difficulty option is available")
        edition = app.create_edition(record, editor=False, difficulty=True)
    elif method == "maptools.remove":
        app.remove_map(record)
        library.invalidate()
        return dict(
            removed=record.profile_id,
            message="Map and unshared launcher archives removed. Saved games kept for exact-version re-import.",
        )
    else:
        raise ValueError("Unknown native map tool: " + method)
    library.invalidate()
    return dict(
        profile_id=edition.profile_id,
        message="Separate edition ready with its own saves. Creation and static checks do not prove gameplay.",
    )
