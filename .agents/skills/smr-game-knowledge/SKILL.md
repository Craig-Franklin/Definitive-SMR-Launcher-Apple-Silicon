---
name: smr-game-knowledge
description: Use this repository's local Railroads game knowledge for engine-dependent development, compatibility questions, custom-map scans, debugging and crash analysis. Reuse matching private evidence, record verification limits and keep raw game content local. Unrelated UI, prose and packaging changes do not require corpus consultation.
---

# Use the Railroads game knowledge

Work from the repository root. Read [knowledge use and verification](../../../docs/rea-research/KNOWLEDGE_USE.md) and the current coverage in [VALIDATION.md](../../../docs/rea-research/VALIDATION.md). Select relevant finding IDs from [the original catalog](../../../docs/rea-research/knowledge-catalog.json), [FINDINGS.md](../../../docs/rea-research/FINDINGS.md) or [the developer guide](../../../docs/rea-research/GAME_DEVELOPER_GUIDE.md); do not load every research document or the whole corpus.

## Reuse private evidence

On Craig's existing Mac, the accepted corpus is outside Git at:

```sh
export REA_PRIVATE_ROOT="$HOME/Library/Application Support/SMR Research/corpus-v1"
python3 scripts/research/corpus.py verify --root "$REA_PRIVATE_ROOT"
python3 scripts/research/knowledge.py catalog
```

Run `verify` before a new investigation or after local evidence changes. Reuse a successful verification within an unchanged investigation. The separate `corpus-main-replay-v1` is comparison evidence, not a replacement for the accepted corpus. These private directories are shared across local branches; Git does not copy them. Missing local evidence means `unavailable`, not permission to reconstruct everything or claim a match. Continue independent original-code work and state the limitation.

Match executable/module SHA-256, UUID and architecture/slice before applying a finding. For maps, also bind archive/prepared variant and dependency identities. For crashes, distinguish runtime address, image base, static address and file offset; unsupported Rosetta mappings stay unknown.

Use [CORPUS.md](../../../docs/rea-research/CORPUS.md) for bounded `corpus.py lookup` commands and exact limits. Choose one question and the relevant neighborhood. Lookup files, raw crash reports, extracted names/tables, pseudocode, strings and scanner JSON stay local outside Git and cloud tool results. Redirect both scanner streams to private files. Cloud sessions use reviewed original notes and the existing closed operational projections. A publication candidate still requires content review; a scanner or review marker is not legal clearance or authenticated approval.

## Apply and verify

- Follow the matching crash, map or development workflow in `KNOWLEDGE_USE.md`. Pass `--corpus-root` to research auditors when matching private evidence is available.
- Record `knowledge_use` in the private case or scanner receipt: consulted/applied/rejected IDs, knowledge revision, build/input identities, coverage gaps and verification. Use `matched`, `partial`, `unavailable` or `not_applicable` honestly.
- Specify new engine-dependent rules with original synthetic positive/negative cases and a counterexample. Keep static risk, synthetic test results and actual game load/save/reopen evidence separate. No scan finding does not prove compatibility.
- Keep new original findings discoverable with stable IDs, provenance, applicability and limits. Preserve raw evidence privately; publish only reviewed original explanations and tools.

Local decompilation is already authorized; do not reopen that decision. Reuse accepted imports and smoke results unless a relevant change invalidates them. This skill does not authorize game execution, save/profile/map mutation, resuming paused compatibility workers, cloud raw-corpus MCP registration, paid services or new workers. Preserve the existing publication boundary and private handoff; consult [AI_PLAYBOOK.md](../../../docs/rea-research/AI_PLAYBOOK.md) only when starting new extraction or a targeted research investigation.
