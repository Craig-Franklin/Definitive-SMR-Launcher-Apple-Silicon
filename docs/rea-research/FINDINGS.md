# Original findings and their evidence limits

This catalog contains original explanations and references to original tools.
Raw game tables, strings, pseudocode and function text are excluded. The version
is the SHA-256 of `knowledge-catalog.json`. Private binding receipts connect a
finding to a module SHA-256, UUID, architecture and verified export digest.
Neither a catalog entry nor a filled-in receipt establishes a runtime fact.

## SMR-K-001

The existing industry reader accepts one exact x86_64 executable SHA-256 and
reads a bounded, 36-entry static identity table. A map definition outside that
table is a useful review lead for the scanner. Membership alone does not prove
valid placement, successful loading, a particular crash mechanism or save safety.
Exact string comparison is the current diagnostic contract; the engine's
case/whitespace behavior is not established here.

`knowledge.py bind` independently compares the original byte reader's output
with Ghidra's imported data references and strings for every table slot. It
requires all 36 entries to match and preserves only matching-build provenance
in its operational response. The extracted identities remain private. This
cross-check validates the table reader against a second static representation;
it does not establish how every engine caller uses that table.

The 11 October 2026 acceptance run matched all 36 entries. The narrow installed
library audit inspected 236 catalog variants in 221 directories. It flagged 131
directories with outside-table identities, including 120 with explicit placement;
10 directories remained unresolved. These are static review leads, not counts of
broken maps or proven crashes. The broader auditor applies different projections
and coverage, so its category counts need not equal the narrow screen's counts.

Original implementation: `src/smr_launcher/industry_registry.py` and
`scripts/research/check_industry_registry.py`. Independently specified synthetic
maps cover a registered identity, an outside identity, explicit placement,
ambiguity and unknown-build refusal in `tests/test_industry_registry_audit.py`.
Binding/staleness/reference tests are in `tests/test_research_knowledge.py`.

## SMR-K-002

Loose-file XML candidate resolution is incomplete evidence about the native
virtual filesystem. Duplicate root fields, ambiguous filenames, unreadable
dependencies and packed assets reduce coverage. Preserve those observations
instead of choosing a candidate and promoting it to engine precedence. A
duplicate-field result remains unknown even when duplicate text is identical.

This is a limitation of the original auditors, not a newly verified engine
rule. See their synthetic ambiguity and duplicate-reference tests. A corpus
binding for SMR-K-001 does not verify native resolution semantics.

## SMR-K-003

The broader map auditor contains inherited models of merge behavior, goods and
bridge registries, reference consistency and numeric outliers. Its rules are
useful hypotheses/review leads, with their existing synthetic tests. This work
does not independently verify every model against native code. Receipts map
these rules to this finding and retain `partial` knowledge status; they cannot
borrow SMR-K-001's table cross-check as proof of unrelated semantics.

Promote a specific rule through a separate original finding only after bounded
local evidence, matching input identities, an independent counterexample and
appropriate verification. Runtime evidence remains separate.
