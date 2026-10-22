# Read-only map inspection CLI

The first inspection slice reads an exact directory or supported archive and a
selected game installation. It reports observations, conditional risks and missing
coverage without importing a map into the launcher, activating it or changing any
authored content. A completed inspection is not a compatibility certificate.

## Run from the development checkout

Use the existing Python 3.12 development environment. No new runtime dependencies
are required. The module command works without reinstalling the environment:

```sh
.venv/bin/python -m smr_launcher.map_inspect inspect \
  "/absolute/path/to/map.7z" \
  --game "/absolute/path/to/Sid Meiers Railroads.app"
```

`pyproject.toml` declares the equivalent `smr-map` console entry point. That alias
becomes available when the package is installed; changing the metadata does not
install it into an existing environment. This implementation does not change the
signed Mac application or its command routing.

Use `--format json` for a machine-readable report. Add `--report` to save a new
private JSON file; the parent directory must already exist. Reports contain local
paths, content identities and map observations, so keep them private and outside
Git. An existing report is never overwritten.

```sh
.venv/bin/python -m smr_launcher.map_inspect inspect \
  "/absolute/path/to/map-directory" \
  --game "/absolute/path/to/Sid Meiers Railroads.app" \
  --format json \
  --report "/absolute/private/path/inspection-001.json"
```

By default the report includes every filename scenario candidate. Use
`--scenario "UserMaps/Example/RRT_Scenario_Example.xml"` to select an exact,
case-sensitive path from the input inventory. Filename detection does not prove
that the native engine discovers or loads that scenario.

Exit code `0` means the bounded inspection completed. Findings may still include
risks, missing inputs and unimplemented checks. Exit code `2` means inspection or
report creation was refused. Argument parsing also uses standard argparse usage
errors. Neither exit code establishes runtime acceptance.

## Preservation and acquisition

The CLI hashes all supplied regular files and all loose files under the selected
installation's sibling `SMRailroadsData/assets` directory. It also hashes the
consumed main executable, reads its Mach-O architecture and UUID, and gates native
rules on the exact supported build. Unknown builds retain mechanical inventory
and XML observations while native industry, city, event and collision-path
interpretations are withheld.

Directories and consumed files are opened through descriptor-based checks with
link refusal, file-type checks, size/count/depth limits and before/after identity
verification. Directories containing links or special files are refused. This is
a bounded local inspection, not protection against a privileged concurrent writer
or an atomic filesystem snapshot.

Archive inputs are copied into a uniquely named private workspace under
`~/Library/Application Support/SMR Research/inspections`. The original archive is
rechecked. The existing bounded extractor rejects unsafe paths, links and
collisions and freezes the extracted copy. Successful copies and failed attempts
are retained for evidence and resumption; the report or refusal identifies their
location. The tool does not automatically delete them.

Report destinations must be outside the map input, the whole selected
installation parent, and every Git working tree detected through a `.git`
ancestor. Linked output parents are refused. Reports use exclusive creation and
mode `0600`. If a write fails after file creation, a partial output can remain;
the CLI returns a refusal and will not overwrite that path on another attempt.

On a completed inspection, `preservation.input_unchanged` covers the inventoried
map input and original archive when supplied. `game_unchanged` covers the consumed
main executable, including confirming that an initially missing executable did
not appear. It does not claim a hash of every bundle component.
`stock_unchanged` covers the inventoried sibling loose assets, or is null when
unavailable. On refusal, unestablished preservation flags remain null.

Bounds include 100,000 entries, 8 GiB total and 512 MiB per file for each map/stock
inventory; depth 64; 1,000 scenarios; 10,000 resource queries; 100,000 cumulative
candidate edges; and 100,000 report records. XML is limited to 8 MiB per document,
128 MiB analyzed total, 100,000 nodes per document and depth 64. The reused archive
reader imposes its own compressed/expanded member limits. Budget exhaustion is an
explicit refusal, not silently truncated successful coverage.

## Report contract, version 1

The stable top-level fields are:

| Field | Meaning |
| --- | --- |
| `schema_version`, `analyzer` | Schema number, analyzer version and source-module content identity |
| `status`, `error` | Completed or refused operation; error type/message when refused |
| `input` | Input kind/location, exact regular-file inventory, content identity and original archive digest |
| `game` | Selected installation, consumed executable digest/UUID/architecture, build admission and stock identity |
| `scenarios` | Scenario paths/digests, ordered reference fields and conditional document observations |
| `findings` | Stable finding identities, categories, locations, observations, evidence IDs, preconditions and limits |
| `coverage` | Scoped checked, partial, unsupported or not-implemented domains with reasons |
| `resource_resolution` | Explicit scenario-reference counts separated into unique, ambiguous, empty, absent-loose, duplicate and unsupported-text observations; native selection remains unproven |
| `provenance` | Authored research descriptors bound to a build/module/static address, with consultation limits |
| `preservation` | Separate input, executable and stock stability results |
| `observation` | Start/end time, retained archive workspace and original-to-extracted member mapping |
| `runtime_acceptance` | Always `not_tested` in this CLI |

On refusal the same top-level keys remain present. Partial input, findings and
snapshot information survive when available; consumers must check `status`
before treating coverage as completed. An output-write failure can follow a
completed inspection and retain valid preservation results while status is
refused.

`content_identity` hashes sorted relative regular-file paths, sizes and SHA-256
digests. It excludes location, directory metadata and times. Equivalent archive
and directory payloads therefore have the same identity; wrapper members or extra
profile files can make complete input identities differ. Preservation separately
checks file/directory metadata and content during the observation window. Finding
IDs include the rule revision and observations, not timestamps. Finding locations
carry scope, relative path, document digest and an occurrence locator where
available. Exact input content and analyzer identity remain necessary context.

Reference text preserves ordered parsed character data, including whitespace,
empty values and duplicate fields. It is not a lossless lexical XML export. DTDs,
entities, namespaces, malformed XML and unsupported document shapes produce
coverage limitations; parser refusal does not establish that the game rejects
the document.

Industry identity checks preserve surrounding spaces and case. The supported
build passes nonempty text onward after testing a separate trimmed copy for
whitespace-only content; its industry reader does not trim that value. A padded
canonical name therefore remains a different candidate key. This does not prove
that its actor reaches the animation hazard: effective definitions, missing-field
inheritance and model construction remain preconditions. No automatic trimming
or renaming is implemented. Native evidence for this qualification is retained
in the private M2 finding ledger.

Analyzer version 1.1 adds structured parser reasons, declared encoding when
observable, parser line/column and invalid UTF-8 byte offsets where applicable.
These describe the inspection profile, not native rejection. Source bytes are
never transcoded or repaired to make the inspection parser accept them.

Launcher imports and refresh now use this inspection service with a launcher
report envelope (`schema=2`) around the CLI contract (`schema_version=1`). Old
loose-warning reports are not current evidence. The existing Import Checks view
uses the shared text formatter. Cache admission binds the analyzer, actual
executable, stock bytes/metadata and authored map observations. The launcher
restricts its input scope to prepared `CustomAssets` and `UserMaps`; saves,
settings and logs are excluded. The standalone CLI still inventories its entire
explicitly supplied archive or directory. A directory timestamp change can
invalidate a launcher report because native metadata effects remain unresolved.
None of this awards gameplay verification or applies repairs.

The five finding categories have distinct meanings:

- `confirmed_invalid_condition`: a condition supported strongly enough for the
  declared exact inputs and scope. This initial slice makes no native-invalid
  declarations.
- `compatibility_risk`: a supported conditional engine hazard; listed survival,
  selection and construction preconditions still matter.
- `heuristic_lead`: evidence worth investigating without a proven invalidity or
  crash conclusion.
- `missing_input`: absent or ambiguous inputs needed for an assessment.
- `unimplemented_check`: a rule, comparison or coverage domain the tool cannot
  currently establish.

`provenance.local_evidence_status` is `not_bound`: the CLI ships authored original
descriptors, not a live private-corpus reader or a semantic-proof certificate.
Private acceptance receipts may record the actual research consultation. Evidence
addresses and executable identities allow later inspection without shipping game
data or decompiled material.

## Implemented observations and limits

Resource lookup retains every matching ASCII basename candidate from both map and
loose stock assets. A unique candidate feeds conditional observations. The CLI
does not emulate native provider ordering, FILETIME comparisons, packed resource
selection or the final live merged registry. Duplicate scenario reference fields
disable dependent binding. Non-ASCII basename comparison is unimplemented.

For the supported executable, the current rules inspect:

- Exact industry keys against the mechanically extracted native registry.
  Directly placed outside-registry definitions are conditional risks; unused
  definitions are leads. Merge, pruning, model construction and runtime grouping
  eligibility remain explicit preconditions.
- City ownership for direct placements with explicit `bInCity` text `1`, using
  the selected `CityXMLFile` candidate or the default candidate. City keys use an
  ASCII case-insensitive projection. Non-ASCII or ambiguous city identities
  withhold unmatched-owner conclusions. Empty ownership is assessed separately.
  Inherited flags and nearest-city assignment are not simulated.
- Exact event titles and sequential modeled Stock/Good effect ranges. A later
  `EventEffect` child revisiting a modeled nonempty range is a conditional cleanup
  lead. Title-only stubs, missing/empty effects and unknown-only initial effect
  types do not establish that hazard. Actual retained native vector state remains
  unverified.
- Map-local NIF headers using the existing pure header reader. For the supported
  build, legacy headers are leads; they do not prove collision construction or a
  defect. Unknown builds receive only mechanical format/version observations.

Packed assets, general factory merges, complete terrain/model/texture dependency
resolution, objective evaluators, event scheduling and save-reference fixups
remain unimplemented. No absent warning certifies those domains.

## Next application slice

A simple GUI should call the inspection API without importing activation or
repair services, display the exact input/build identities, and group findings by
category with their provenance and coverage. Keep the CLI report as its contract;
do not reinterpret exit code zero as compatible.

Before adding automated repairs, specify feature-preserving transformations on
separate copies, independent positive/negative/counterexample controls, and exact
runtime acceptance. Runtime acceptance requires separate evidence for loading,
representative gameplay, objectives/events, a unique save, clean quit, exact save
reopen, continued play and restoration. Neither the CLI tests nor its model
checks establish those results.
