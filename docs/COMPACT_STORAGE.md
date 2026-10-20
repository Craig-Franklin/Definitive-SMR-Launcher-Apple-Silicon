# Compact inactive library storage

Issue [#246](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/246)
tracks acceptance. Compact storage targets launcher Application Support below 10 GB,
with below 5 GB preferred, while preserving originals, editions, profiles and saves.
Source tests, isolated package checks and installed migration are separate results.
Nothing enables migration automatically on startup or upgrade.

## Storage representation

Every cold directory has a sibling `.cold.json` manifest. It records exact relative
paths, empty directories, content SHA-256 and length, modes, ownership, modification
times in nanoseconds, extended attributes and macOS ACLs. Restored files are independent
regular files, including saves with identical content. Birth/access times, inode numbers
and filesystem change times are not preservation identities. Unsupported file flags,
special modes, links, hardlinks and volume crossings refuse conversion.

Manifests retain original raw extended attributes, including `com.apple.provenance`.
On macOS, newly created restored copies keep their OS-assigned provenance label;
reproducing that label exactly is not promised. Only this label may differ from the
original manifest. Bytes and all other metadata remain exact, including quarantine,
custom or unknown extended attributes, ACLs, ownership, modes and modification times.

Verified original payloads reference retained canonical archives. The production reader
inspects archive membership, streams member bytes, verifies their SHA-256 and size, and
rechecks the canonical archive's content identity. It never assumes imports match an
archive by name. Other bytes use shared, independently compressed zlib objects. Each
path keeps its own metadata. Edition restoration uses these exact representations;
it does not rerun preparation or compatibility rules.

Small authored scenario and map-description files also retain compressed presentation
copies. Browsing reads those bounded bytes without materializing map collections.
Cold import-check reports and gameplay records remain retained. Cold storage does not
promote old reports into current resource or gameplay acceptance; known historical
warnings remain limited evidence. Read-only recipe planning and guarded/observed game
consumers refuse cold inputs instead of silently preparing or recovering them.

Canonical archives remain retained when maps are removed in compact mode. This protects
cross-map, uncatalogued-profile, in-flight and rollback references. There is deliberately
no compressed-object garbage collector in this version. Unreferenced compressed objects
remain retained; removal is not a promise to reclaim their bytes.

## Transactions and working trees

The application and profile locks, enrolled installation binding, literal stopped-game
probe, settled activation state and absence of pending removal gate conversion. A plan
binds catalogue bytes, installation binding, active identity and every candidate tree's
content and metadata. Apply re-derives the complete proposal before its first compact
commit. Unknown directories and explicit pins are quantified exclusions. Explicitly
included unmatched profiles must pass the existing profile identity/asset validation.
The active live profile is never a compact target.

Before a hot tree is disposed, compressed/archive-backed bytes are restored to private
scratch and compared against the manifest, allowing only the restored-copy provenance
exception above. After exclusive directory creation,
the schema-2 per-tree journal durably records its device, inode and creation time.
A crash before that record leaves unknown scratch intact. Names alone never grant
cleanup ownership. Before backup disposal, recovery verifies directory identity and
all actual surviving entries, refusing added or changed data. A durable disposal
transition permits a safe partial deletion to resume. Scratch cleanup additionally
bounds every remaining path and file to the planned payload or its verified prefix;
unknown, replaced or changed scratch remains intact for inspection.

Completed scratch records pin the observed provenance label. Later readbacks must
match that saved label before publication or disposal; they cannot replace it.
Before publishing a materialized copy, a private receipt binds its exact observed
provenance to the original manifest digest and directory creation identity. Original entries still
pin every other field. Recovery and rollback require this creation-bound readback
for known restored copies and refuse later provenance edits or replacement paths.
Original/source preflight remains strict for all metadata, including provenance;
the restored-copy exception never applies to original trees.

New compact transactions may declare one root-only provenance transition before
renaming the original to its backup. A purpose/version-checked declaration binds
the completed creation-owned check's exact measured root label to the original
manifest digest, creation identity and unique backup destination. Original raw
entries and their digest remain unchanged. Before the first durable observation,
the owned backup must match the complete original manifest and identity, allowing
only its root provenance to equal the original or exact predeclared target value.
That same prior proof permits settlement after process death following rename.
The first accepted observation is pinned immutably; all later backup and partial
disposal reads must match that endpoint, even if the other declared value appears.
Explicit label absence differs from a missing declaration or observation. Missing,
malformed or stale declarations grant no allowance; legacy backups retain exact
original-metadata checks. No sibling label or later observation can retroactively
authorize a transition. Quarantine, other xattrs, ACLs, modes, times, payloads,
topology and creation identity retain their existing strict preservation checks.

The whole-library journal records the exact plan and completed trees. A cancellation
may leave that journal pending: resume or roll back the same proposal before ordinary
mutations. Rollback restores initial paths, bytes and metadata with only the
OS-assigned provenance exception above, using the creation-bound exact observed
receipt for restored copies. It refuses changed catalogue, active identity or later
file/metadata edits. It never overwrites a newer save to force rollback.

After migration, ordinary imports publish a cold writable profile directly from verified
prepared bytes and retained saves. Incoming canonical references are streamed and
verified before profile registration, avoiding redundant original-asset blobs.
They do not clone a complete eager profile. Existing
archive extraction and preparation still operate on one incoming map before their trees
are packed. Activation/Play prepare the selected record, switch through the existing
transaction and pack inactive working trees before dispatch. Explicit map preparation
can retain one selected working record for guarded consumers or recipe planning.
Ordinary named recipe creation and reuse apply the same eviction policy, retaining
the selected edition and its independent saves. Existing cold editions materialize
before ordinary validation; guarded and read-only reuse require an intact warm cache. Free
space is checked before scratch expansion. Long operations report through existing
native jobs/activity indicators, and preparation checks cancellation before dispatch.

## Installed application bridge

The existing `tools.start`, `tools.status` and `tools.cancel` native bridge carries these
named operations. This is normal bundled application functionality, not a script or
arbitrary-code execution endpoint.

- `library.compact.plan`: optional `exclude` relative tree identities and
  `include_profiles` exact profile IDs. The completed job exposes only a proposal
  SHA-256, tree/exclusion counts and excluded logical bytes in `compact_proposal`.
- `library.compact.apply`: requires `consent: true` and exact `plan_sha256`.
- `library.compact.rollback`: requires the same exact proposal identity and consent.
- `library.prepare`: requires `consent: true` and one current `profile_id`; no launch.

Raw proposals stay under the private library's `compact-proposals/<sha256>.json`.
Native status never carries member paths or tree manifests. Apply and rollback read
only the proposal named by its validated digest and verify its contents again.
The source CLI provides equivalent `compact-plan --output`, `compact-apply --plan`,
`compact-rollback --plan` and `prepare-map` commands. Keep proposal files private.
Neither bridge exposure nor a passing job admits a live migration without its owner.

## Budget and verification limits

The measured original-archive and independently compressed-save floor is about 3.207 GB.
A complete additional compressed asset store would push that floor above 5.576 GB,
before metadata, working copies or updates. Verified archive references avoid that
second asset store and preserve a route to the preferred budget. These estimates are
not installed acceptance. Exact exclusions, manifest/object allocation, protected update
artifacts, temporary expansion and the selected working set still need measurement.
Folder allocation is distinct from unique physical SSD reclamation and APFS snapshots.

Original knowledge finding SMR-K-002 limits claims about native resource precedence.
This implementation retains exact paths and resource metadata and introduces no new
resource-resolution rule. Filesystem round trips do not establish actual game loading,
gameplay, save/reopen behavior or general map compatibility. Root owns final source
integration, signed deployment, live migration and measured storage acceptance.
