# Reproducible local setup

## Scope

This setup validates REA on our original C fixture. It does not import, decompile, launch or modify the game. It does not register an MCP server or install agent instructions globally. Tool installation uses the network; native fixture analysis runs locally. No paid provider or API key is required.

## Prerequisites

The first machine used Node 24.13.0, npm 11.6.2, Ghidra 12.1.4 and the arm64 Homebrew JDK 21.0.12.1. REA 6.4.0 accepts Node `^22.19.0 || ^24.11.0 || >=26.0.0`. Use the pinned tested versions where possible; changed versions require a fresh validation record.

Use an official [Ghidra release](https://github.com/NationalSecurityAgency/ghidra/releases) and its required full JDK, with the host-native decompiler present. The fixture uses Xcode Command Line Tools (`xcrun clang`) to cross-compile x86-64 on the Apple Silicon host. It need not execute that fixture under Rosetta. Record the compiler/macOS versions: compiled binary hashes can vary across toolchains even when the source is identical.

Installers for Ghidra/JDK and host OS are not vendored or fully locked by npm. Record their version, distribution source and archive digest in the private environment manifest. A package lock reproduces npm dependencies, not the entire computer.

## Install from this branch

In the isolated research worktree:

```sh
npm ci --prefix scripts/research/rea --ignore-scripts --no-audit --no-fund
scripts/research/rea/node_modules/.bin/rea --version
```

The checked-in lockfile pins REA and transitive package integrity values. Lifecycle scripts are disabled for this tested macOS CLI/Ghidra lane. Do not use unpinned `npx ...@latest` or `rea update` during a recorded investigation. Upgrades are separate changes with another smoke test. The source snapshot reviewed is recorded in `toolchain.json`; the npm integrity record is separate.

## Select existing Ghidra and Java

Example for the first Mac; adjust to actual installations on another machine:

```sh
export GHIDRA_INSTALL_DIR=/opt/homebrew/opt/ghidra/libexec
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export SMR_RESEARCH_ROOT="$HOME/Library/Application Support/SMR Research/rea-6.4.0"
python3 scripts/research/rea/smoke.py --private-root "$SMR_RESEARCH_ROOT"
```

These variables apply to this shell. They do not change global Java settings. Ghidra project paths must have no dot-prefixed components. Choose a private, non-synced directory outside every Git worktree; check the machine's actual backup/sync policy. The helper creates unique owner-only run directories and refuses output inside a Git worktree. It never overwrites prior receipts or accepts an arbitrary game target.

The helper checks scoped provider readiness, builds our x86-64 fixture, decompiles its `bounded_index` function, repeats the exact query with a snapshot, and checks that the binary's SHA-256 is unchanged. Full output and stderr stay in the private run folder. The content-presence assertions are smoke checks, not semantic-equivalence proof. Review result status/limitations and cleanup separately.

Each step records elapsed time and exit status. The fixture lane sets a 512 MiB JVM heap and 120-second provider startup deadline. The heap does not cap total RSS and the startup deadline does not bound every operation. The helper has no external whole-process watchdog: if REA stalls beyond its documented deadline, retain the run and inspect its owned processes; do not restart automatically or kill unrelated Java processes. Production investigation limits belong in REA-04.

## Local directory layout for future admitted research

```text
SMR Research/
  tools/                    # optional pinned tool distributions
  originals/                # authorized immutable input copies; never a public repo
  manifests/                # identity, versions, hashes, rights decision, provenance
  projects/                 # persistent Ghidra projects for existing export workflow
  index/                    # searchable private export with completeness records
  cases/<case-id>/           # exact questions, observations, competing explanations
  snapshots/                # REA cache; proprietary when generated from the game
  reports-candidate/        # derived summaries awaiting publication review
  rea-6.4.0/synthetic-*/     # original-fixture validation runs
```

Use a separate clone/worktree for publication. Never point broad agent indexing, GitHub Actions artifacts, public crash attachments or sync jobs at this directory. Owner-only file permissions protect against other local users; they do not prevent upload by software running as your account.

## Optional AI integration, after the disclosure decision

Use REA's pinned CLI for local manual work first. Do not run global `rea setup` as part of this initial setup. When MCP becomes appropriate, inspect the pinned setup dry-run and select only the intended client/profile, preserve its prior configuration, use absolute pinned binary/provider paths, verify effective registrations, and restart that client. Record rollback instructions from the actual diff. REA tool results sent to a cloud assistant leave the machine even when the provider runs locally.

For genuinely local-only proprietary analysis, use manual Ghidra/CLI or evaluate an on-device model separately with network/external fallbacks disabled and verified. This project has not installed or benchmarked such a model.

## Reproduce and roll back

A second machine should check out this branch, install the specified tools, run `npm ci`, run the fixture helper and compare structured behavior and observed versions. Exact timings are not acceptance criteria. Preserve each receipt independently.

To stop using this setup, leave the optional directory unused or remove only its generated `node_modules` after confirming the exact path. No global agent configuration or launcher runtime was changed. Retain private receipts and any future authorized evidence according to the chosen retention policy; do not delete them as a routine cleanup side effect.
