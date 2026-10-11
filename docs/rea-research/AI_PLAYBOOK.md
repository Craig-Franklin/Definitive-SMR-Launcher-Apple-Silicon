# AI instructions for stability research

## Resume capsule

Read `README.md`, `VALIDATION.md`, this file and the current Project item first. Retrieve other documents by need. Current objective: a complete local corpus of installed game native modules and reusable original knowledge for future building, debugging and crash analysis. Craig authorized local decompilation on 11 October 2026; REA-03 records that settled decision and is not a license-resolution gate. Raw game-derived content remains local. Existing compatibility automation is paused.

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

- Bulk corpus work covers all distinct in-scope native modules/slices, sequentially and with per-module checkpoints. Later interpretation uses one question and one matching build per packet.
- Initial packet target: at most 3,000 tokens of task/evidence references. Tool output target: 2,000 tokens per response. These are soft project limits; retain full evidence privately and label truncated views explicitly.
- Reuse existing imports/exports after identity and coverage checks. Bulk export belongs to persistent Ghidra plus ExportEngine.java; do not issue one isolated REA CLI import per function. After corpus creation, retrieve only relevant functions and bounded neighbors locally.
- The 12-query/20-active-minute limit applies to exploratory interpretation, not the authorized full bulk export. Bulk jobs use explicit module manifests, per-function timeout, bounded sequential resource use, progress receipts and resumable checkpoints. Stop a failing module, preserve its evidence and continue independent modules; never loop automatic retries.
- Reuse one provider session for related new queries after approved integration. Store successful snapshots and cache by binary SHA-256, provider/version/profile and exact query. Never infer cache validity from a filename.
- Preserve full output locally; summarize with deterministic projections. Do not print package manifests, full doctor catalogs or broad handoff state into every model context.
- Reuse accepted test results until a relevant change invalidates them. Run focused checks for changed code. Do not rerun the synthetic doctor/compile/decompile smoke, second-machine replay, broad provider/profile tests or dependency audits as routine blockers. Recheck an identity/version only when its inputs change or evidence is missing.

## Evidence contract

For engine-dependent development, crashes and map scans, follow [knowledge use and verification](KNOWLEDGE_USE.md). Record the `knowledge_use` consultation fields in the private case/receipt; future scanners must bind catalog revision and applied rule IDs. Missing coverage limits a claim, rather than forcing unrelated work to stop.

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

## Corpus and investigation protocol

Local extraction is authorized; proceed without another license/scope interview:

1. Inventory both installed app locations, deduplicate native modules/slices, preserve independent input copies and hash/UUID/tool identities. Reuse current private exports after digest and coverage checks. Extend the existing exporter/runner rather than starting a parallel decompiler.
2. Build the full private export with per-module completion and failure accounting. Then state a falsifiable question for each analysis task, inspect the relevant local neighborhood and preserve the observations privately. Keep pseudocode/disassembly out of cloud tool results.
3. Seek a counterexample and reconcile contradictory evidence. Stop at a missing resource/provider binding instead of inventing the asset identity.
4. Describe behavior in original prose. Write an original synthetic regression from the independently stated condition; retain how it was specified.
5. Prefer a diagnostic or narrowly applicable prepared-copy transformation over executable patching. Refuse unknown build/asset identities. Preserve features, originals and saves; require idempotence and rollback.
6. Any game run uses the existing project's current admission, preservation, identity, watchdog, stop and restoration rules. This plan does not reset closed circuits or authorize replay of an old restore.
7. Report static/synthetic/runtime/load/save/reopen/continued-play evidence separately. A copied save is not proof it reopens.

## Publication and CI

No proprietary input, derived function export, REA snapshot or raw trace in commits, issue bodies, PR attachments, Actions logs/caches/artifacts, cloud model prompts or shared Pages. The original `rea/evidence.py` helper accepts only its built-in fixture. `corpus.py` handles actual local module/slice provenance, private retrieval and closed operational publication reports; `knowledge.py` binds original finding IDs to matching static evidence. Use strict output projections for cloud-visible operation receipts; raw local queries stay private. Publish reviewed original analysis and original tools through explicit candidate-file review. Retain disguised payload, symlink/root and output-preservation regressions when changing the boundary.

A publication scanner supports human review; it does not establish intellectual-property rights. Preserve original attribution and dependency licenses. Use signed commits, exact fork guards and remote readback. Craig explicitly authorized this slice's reviewed PR merge and separate main-checkout replay on 11 October 2026. No release is authorized; check the existing release policy before merging.

## Required result format

Return a brief user summary: what was learned, what it changes, confidence/limits, and the next useful action. Put technical detail in the case record: inputs, command/query, observations, hypotheses, counterexamples, tests, resource use, status and open questions. Use standard English rather than long strings of internal gate labels.

## Avoidable work removed

No rights-resolution prerequisite, second-machine requirement before using this Mac, repeated passed smoke checks, all-provider/cache-profile campaign, autonomous workers, broad agent registration or requirement to finish a particular crash before building the corpus. REA cache replay is optional acceleration, not a prerequisite for persistent exports. Keep identity/input-preservation, useful failure/timeout accounting and exact publication review because they prevent wasted or unsafe output.

## AI session handoff

Maintain two views: the private detailed corpus and compact original knowledge notes. Each note states the question/subsystem in original language, matching build reference, evidence IDs, observation versus hypothesis, confidence, counterexamples and remaining unknowns. A future cloud session receives only reviewed original notes and safe operational metadata. It asks a local deterministic query to locate evidence without receiving raw code. Raw semantic inspection remains local; do not promise that a cloud model can inspect the full local corpus without disclosure. Preserve a short local resume record after each completed module or accepted finding.
