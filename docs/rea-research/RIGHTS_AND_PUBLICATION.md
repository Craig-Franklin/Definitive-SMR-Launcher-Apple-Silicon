# Game rights, private analysis and publication

## Actual concern

The aim is stability research without exposing the game's proprietary code or assets. Decompiled pseudocode is reconstructed output, not recovered original source, but it can still reproduce protected expression. Calling it pseudocode, storing it in a private GitHub repository, renaming symbols, or rewriting it through an AI does not make it safe to distribute.

This is an engineering boundary and a record of unresolved legal questions, not legal clearance or a guarantee against a claim.

## What the reviewed sources establish

The [Railroads Steam page](https://store.steampowered.com/app/7600/Sid_Meiers_Railroads/) links to a [Take-Two license agreement](https://store.steampowered.com//eula/7600_eula_0). Its License Conditions restrict reverse engineering, decompilation, disassembly and modification, and also restrict instructions for prohibited conduct. Ownership of a purchased copy does not itself grant those rights.

[Take-Two's current general terms, section 2.3](https://www.take2games.com/legal/en-US/) additionally restrict use of its content as source material for generative AI and discuss statutory exceptions to reverse-engineering restrictions. Whether those terms govern this specific Mac edition/purchase is unresolved. The [Steam Subscriber Agreement, section 2.G](https://store.steampowered.com/subscriber_agreement/) also contains reverse-engineering restrictions subject to its stated permissions and applicable law.

[17 USC 1201(f)](https://www.law.cornell.edu/uscode/text/17/1201) contains a limited interoperability provision with conditions. It is not a blanket permission for game stabilization, publication, AI processing or bypassing protection. Jurisdiction, contractual terms, purpose and method matter. A qualified assessment or appropriately scoped written permission is needed to resolve the proposed activity. Feral's general open-source notices are not a license to the game engine.

A targeted filename inventory of the installed Steam game found third-party notices but did not identify an authoritative game EULA file. This is not an exhaustive determination of all terms presented at purchase/install. REA-03 must record edition, storefront, applicable license evidence, and the resulting allowed scope.

## Default storage and disclosure policy

| Material | Default destination | Publication rule |
| --- | --- | --- |
| Commercial executable, libraries, assets, game strings/tables | Separate private local research directory | Do not publish |
| Decompiled code, disassembly, function dumps, Ghidra/Hopper/IDA databases, REA snapshots | Private local only | Do not publish or send to cloud AI under this plan |
| Full crash reports, memory/register dumps, saves, logs, screenshots and resource inventories | Private local only | Use only an explicitly reviewed derived summary; existing reporting policies remain separately scoped |
| Downloaded custom maps and repaired map payloads | Private originals/prepared copies | Author permission/license review per package; public download access is insufficient |
| Our setup code, lockfiles, diagnostics, original documentation | Repository branch | Review provenance and staged content, then publish |
| Wholly original synthetic fixtures and regressions | Repository | Publish with applicable project license |
| Findings in our own words, bounded numeric evidence, source links, tool/build versions | Candidate report | Review case by case; exclude copied expression, extracted tables and private identifiers |
| Existing game-specific offsets or hashes | Local record first | Publish only when needed and reviewed; a hash is provenance, not permission or proof of harmless content |

Do not relabel decompiled code as an original implementation. A behavior specification and an independently authored implementation can reduce copying, but no clean-room or legal-compliance claim follows merely from using a different agent. This repository's MIT license for Apple Silicon additions does not license the game or all inherited upstream material.

## Safe work that can proceed now

Install pinned analysis tools; compile and inspect our original fixture; improve original launcher code and synthetic tests; document the evidence model; locate authoritative terms; plan a bounded rights request or qualified review. Existing authorized diagnostic work retains its own boundaries. Do not silently expand automatic reports to include REA output.

Game binary analysis, decompilation, new proprietary extraction, binary patching, DRM/access-control changes and AI ingestion of proprietary results are not activated by this plan. No game-specific decompilation walkthrough is included before the rights decision.

## Proposed rights request for human review

Ask the relevant rights holders to clarify permission for personal, noncommercial, local analysis of a legitimately purchased Mac edition to diagnose crashes and custom-map interoperability. Describe the exact methods, whether any AI would process results locally or remotely, retention, preservation of originals, and the intention to publish only original tools and reviewed non-code findings. Explicitly exclude redistribution of executable/asset content and access-control bypass. Resolve whether a third-party engine component requires separate permission.

This is a draft scope for a request, not permission already received. No request has been sent to Feral, 2K or Take-Two.

## Publication procedure

1. Start from a clean branch in the personal fork and an explicit list of candidate files.
2. Keep private output outside all Git roots. `.gitignore` is a convenience, not a security boundary; `git add -f`, existing tracked files and commit history can bypass it.
3. Review the entire branch diff and newly added history for proprietary data, secrets, absolute personal paths, large/binary files and copied snippets. Review JSON/text as carefully as binary files. Never stage the whole research folder blindly.
4. Run tests using original synthetic data. Cloud CI receives no game installation, analysis database or secrets for research.
5. Push signed commits only to the exact personal fork. Verify remote commit identity/signature. A future release/export gate should use a positive path/schema allowlist plus negative leak fixtures; regexes alone cannot certify rights.
6. Publish only the reviewed report. Keep raw local evidence reachable by stable IDs in a private manifest, without placing paths/content in public issues.

Until REA-04 implements the export gate, publication is an explicit file-by-file review. Do not claim an automated legal or data-loss prevention system exists.
