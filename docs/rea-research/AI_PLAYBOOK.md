# AI instructions for stability research

## Resume capsule

Read `README.md`, `VALIDATION.md`, this file and the current Project item first. Retrieve other documents by need. Initial objective: reproducible local tools and a reviewable research plan. Proprietary game analysis has an unresolved rights/disclosure prerequisite. Existing compatibility automation is paused. No initial game repair or compatibility claim is made.

Repository writes belong only in `Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon`, on the research branch or a scoped child branch. Explicitly pass the repository to GitHub CLI commands: this fork's default CLI repository can resolve to upstream. Preserve the main checkout's unrelated dirty documents and unaccepted source. Never mutate the shared compatibility handoff or resume its workers from this research plan.

## Model and effort selection

These are project defaults, not claims about a model being sufficient for every task. Verify actual available models and supported effort settings before launching a session. Use the user's existing plan; do not provision API credits or paid automation.

| Work | Default | Escalation condition |
| --- | --- | --- |
| Hashing, inventories, searches, JSON projection, tests, diff checks | Deterministic scripts; no model | A real interpretation question remains |
| Formatting, narrow summaries, status updates, small mechanical edits | Lowest-cost available suitable model; e.g. GPT-6 Luna low | One correction fails a clear acceptance check, or task reveals complex behavior |
| Original helper code, targeted function reasoning, regression design | GPT-6.1 Sol low; medium for nontrivial reasoning | Conflicting evidence, alias/pointer ambiguity, or a consequential unresolved inference |
| Difficult fault mechanism or consequential review | Sol medium first; a bounded higher-capability/high-effort review only with recorded reason | Two bounded attempts have no new evidence, or an independently stated hard question warrants it |

Do not default to high/xhigh for setup, discovery, project administration or routine code. No autonomous agent swarm is required. Delegate only when authorized and when independent acceptance or a clearly separate task justifies the additional context. A fresh independent reviewer gets a small evidence packet, not an entire conversation history.

[OpenAI reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning) describes the tradeoff between reasoning effort, latency and reasoning tokens. Model names, prices and entitlement change. API prices do not measure ChatGPT subscription consumption. Record observed usage when available and mark unavailable token counts as unknown, not zero.

## Context and tool budget

- One case, one question and one executable identity per investigation packet.
- Initial packet target: at most 3,000 tokens of task/evidence references. Tool output target: 2,000 tokens per response. These are soft project limits; retain full evidence privately and label truncated views explicitly.
- Query existing indexed metadata before decompilation. Retrieve one function and bounded neighbors, not an entire program. Increase scope only to test an explicit hypothesis.
- Default initial pass: at most 12 new analysis queries or 20 minutes of active investigation, whichever comes first. Stop with findings and a precise next input; do not loop on uncertainty. A larger batch needs a documented scope/budget decision, not repeated automatic retries.
- Reuse one provider session for related new queries after approved integration. Store successful snapshots and cache by binary SHA-256, provider/version/profile and exact query. Never infer cache validity from a filename.
- Preserve full output locally; summarize with deterministic projections. Do not print package manifests, full doctor catalogs or broad handoff state into every model context.
- Reuse accepted test results until a relevant change invalidates them. Run a meaningful failing regression, implement the change, then focused and required checks.

## Evidence contract

Use `case.template.json` as the starting shape. Keep private paths only in the local instance. Record:

1. Case/question, scope, date and rights/disclosure status.
2. Target edition, architecture, executable SHA-256 and image UUID; map archive/prepared variant identity and scenario where relevant.
3. Source branch/revision, REA package integrity, source review SHA, Ghidra/JDK/Node/compiler versions and analysis options.
4. Exact query and parameter set; evidence file digest and location within that file; full status and limitations.
5. Observations separated from hypotheses, inferred types/names, counterexamples and unknowns.
6. Acceptance criterion, falsifying experiment, result and unresolved dependency.
7. Model/effort actually used, tool calls, elapsed time, tokens when available, cache reuse and escalation reason.
8. Publication decision, review provenance and the minimum safe report.

Address rules: distinguish runtime virtual address, image-relative offset, preferred address, file offset and slice offset. Record ASLR/image base only from matching evidence. Match UUID/hash before symbolic interpretation. An unknown or translated frame remains unknown without an established mapping. Provider-inferred types and names are not original source declarations.

A provider timeout can leave analysis running. Preserve the exact response, session ID and cleanup state. `cleanup_incomplete` is not success. Do not blindly retry or broaden a process kill. Retain ownership and resolve only the session's resources.

## Conditional investigation protocol

After REA-03 permits the exact activity:

1. Verify the current accepted case evidence and matching immutable input; reuse the original Ghidra index if available and complete.
2. State one falsifiable question. Inspect minimal callers/callees and relevant references. Keep game pseudocode/disassembly local under the disclosure decision.
3. Seek a counterexample and reconcile contradictory evidence. Stop at a missing resource/provider binding instead of inventing the asset identity.
4. Describe behavior in original prose. Write an original synthetic regression from the independently stated condition; retain how it was specified.
5. Prefer a diagnostic or narrowly applicable prepared-copy transformation over executable patching. Refuse unknown build/asset identities. Preserve features, originals and saves; require idempotence and rollback.
6. Any game run uses the existing project's current admission, preservation, identity, watchdog, stop and restoration rules. This plan does not reset closed circuits or authorize replay of an old restore.
7. Report static/synthetic/runtime/load/save/reopen/continued-play evidence separately. A copied save is not proof it reopens.

## Publication and CI

No proprietary input, derived function export, REA snapshot or raw trace in commits, issue bodies, PR attachments, Actions logs/caches/artifacts, model prompts or shared Pages. The initial helper accepts only its built-in original fixture. Future export tooling must fail closed on fields/paths outside an explicit allowlist and must be tested against disguised text/JSON payloads, symlinks and wrong roots.

A publication scanner supports human review; it does not establish intellectual-property rights. Preserve original attribution and dependency licenses. Use signed commits, exact fork guards and remote readback. No automatic merge/release follows from this research plan.

## Required result format

Return a brief user summary: what was learned, what it changes, confidence/limits, and the next useful action. Put technical detail in the case record: inputs, command/query, observations, hypotheses, counterexamples, tests, resource use, status and open questions. Use standard English rather than long strings of internal gate labels.
