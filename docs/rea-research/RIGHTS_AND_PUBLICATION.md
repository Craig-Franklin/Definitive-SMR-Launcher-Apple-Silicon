# Local analysis and publication boundary

## Current instruction — 11 October 2026

Craig authorized decompiling everything locally, clarified that the concern is exposing proprietary code/assets, and asked to rework the project around reusable knowledge for future AI-assisted building, debugging and crash analysis. This supersedes the initial synthetic-only scope and REA-03 license-resolution prerequisite. Do not repeat that interview or block local extraction on a rights-holder message or legal review. No rights-holder message is authorized.

Record this as the user's authorization and chosen project scope, not as third-party permission or legal clearance. Previously noted license questions remain unadjudicated; they are not an implementation dependency under the current instruction. This document defines handling and publication, not legal advice.

## Local corpus

All installed game binaries/libraries, assets, extracted strings/tables, disassembly, reconstructed pseudocode, analysis databases, snapshots, raw crash/log/memory evidence and derived raw indexes remain outside Git in a private local research directory. Preserve originals; work from hash-verified independent copies. Do not modify profiles, maps, saves or executables. No game launch or access-control bypass is authorized by the corpus task.

A cloud agent sees tool results returned to it even when the tool runs locally. Therefore local scripts may process the corpus, but their cloud-visible responses must be a closed projection of operational metadata or reviewed original findings. Never print source/disassembly/pseudocode/string tables, broad inventories, raw diagnostics or arbitrary exception messages into cloud tool results. Local-only raw inspection uses local tools/manual analysis; installing or evaluating an on-device model is a separate optional task, not a prerequisite for building the corpus. Do not register a cloud REA MCP to expose the raw corpus.

## Publishable analysis

Our original tools, synthetic tests, methods, independently authored behavior explanations, diagnosis hypotheses, limitations and original diagnostics may be published after reviewing the exact candidate content. Link findings to private evidence IDs without exposing local paths or embedding raw evidence. A function dump renamed as a summary or a lightly rewritten pseudocode listing is not an original finding.

The original-analysis notes can be more expressive than the current synthetic helper's fixed report vocabulary. That helper does not approve arbitrary prose. Review prose candidates explicitly; extend publication tooling when a concrete schema is needed. The private corpus, a candidate analysis note and an approved publication are distinct artifacts. A scanner supports content review and does not confer legal clearance.

## One publication check per change

1. Select the exact original files or analysis candidates; keep raw corpus/receipts outside all Git roots.
2. Review the staged diff and newly added history for game content, copied snippets/tables, secrets and private paths. `.gitignore` alone is insufficient.
3. Run focused synthetic tests if original code changed. CI receives no game data or private corpus.
4. Make signed commits and push only to the exact personal-fork research branch; verify remote identity/signature. Keep PRs draft until reviewed; no automatic merge/release.

Do not add a separate permission interview for routine local extraction or for publishing original tools already within scope. An uncertain candidate can stay private while other work continues. A real disclosure change, purchase, game mutation or runtime task needs its own concrete scope decision.
