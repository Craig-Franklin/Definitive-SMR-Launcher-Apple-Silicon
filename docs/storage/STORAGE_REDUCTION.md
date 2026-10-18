# Storage reduction and existing-library migration

Source delivery is tracked in [issue #246](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/246).
The three optimizations preserve existing archive names, variant identities, profile paths and save handling. They do not perform a startup migration of an existing library.

## New storage behavior

Completed updater exchanges retain a durable receipt. Authenticated startup keeps the current transaction's verified previous app, archive and helper. Older completed transactions can be removed only under the install lock, after checking their paths, publisher, archive identity and helper process absence. Ready transactions, live or unidentified helpers, shared artifacts, unknown contents and legacy artifacts without receipts are retained. Cleanup errors are reported and the receipt remains for inspection or a safe retry.

Collection imports preserve an immutable SHA-256 archive in `original-archives`. A download remains available until catalogue publication succeeds. The importer then checks both archive identities and removes only its own verified SHA-1 download path. Local/external source files remain intact. Single and bulk collection operations use canonical archives after SHA-256, declared-size and collection SHA-1 checks; this also supports retained archives without a catalogue row. Failed or cancelled imports retain their download.

Map asset copies use native macOS `fclonefileat`, with independent inodes and metadata. Unsupported or cross-volume cloning uses ordinary copying. Native ordinary copying preserves data, stat, ACLs and extended attributes through `fcopyfile`. Permission, capacity and integrity errors propagate. A private scratch copy is validated before exclusive file publication; the existing outer tree transaction controls preparation and profile publication. Only `CustomAssets` and `UserMaps` are cloned. Saves, clean-baseline creation and the observed-I/O safety adapter retain their existing paths.

Apple documents private subsequent writes and independent metadata for clones in [clonefile(2)](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/man/man2/clonefile.2); [copyfile(3)](https://github.com/apple-oss-distributions/copyfile/blob/main/copyfile.3) describes the fallback metadata facilities. Native success proves that cloning ran, not how much unique physical space was saved.

## Migration proposal: separate authority and execution

This is a plan, not an implemented migration operator or permission to change an existing library. The source handoff authorizes a read-only inventory. Detailed paths, identities and candidate lists stay in private evidence. Any live migration needs a fresh, exact reviewed candidate, stopped launcher/game state, preservation evidence and explicit live-data authority.

The first proposed migration handles verified duplicate archives only. Its inventory binds catalogue identity, each canonical archive's SHA-256, the download's matching SHA-256 and collection SHA-1, size, device/inode, link count, mode and modification/change timestamps. Both files must be independent regular files and unchanged after hashing. A changed catalogue invalidates the inventory. Candidates must be refreshed immediately before execution.

Candidate allocated-byte sums are an upper bound for the inventoried files, not guaranteed reclaimable storage. The estimate is a range from zero through that sum until an isolated pilot measures actual volume allocation. APFS shared extents, snapshots and concurrent volume writes prevent deriving unique savings from logical size, Finder or `du` alone.

Exclude every save, live or stored profile, active helper, pending update, legacy updater artifact without adequate transaction provenance, unknown/symlink/hardlink path, evidence-pinned object and ambiguous owner. Uncatalogued profiles are provenance questions; catalogue absence never makes them disposable. The inventory must not recurse into profiles or read saves. Prepared asset conversion is a later migration phase, requiring its own exact inventory and pilot; future cloning does not shrink existing full copies.

### Proposed archive transaction and recovery

1. Acquire existing application coordination. Confirm no game, import, removal, profile switch or update owner conflicts. Refuse stale inventory, changed identities, missing canonical archives and preservation/evidence pins.
2. Recheck every candidate and its catalogue references. Write a private, durable journal containing exact original/quarantine paths, preimages, identities, checksums and expected canonical archive. Use an exclusive same-volume quarantine directory.
3. Rename only the verified duplicate download to quarantine. Flush parent directories. Preserve the canonical archive and catalogue. A crash before commit restores the quarantined download after identity verification; a collision refuses recovery and preserves both paths.
4. Verify offline single/bulk reuse, import/reimport, edition sharing and canonical integrity using isolated copies before authorizing final disposal. Real game save/reopen acceptance requires its own admission; synthetic coverage does not provide it.
5. Commit the journal only after readback and acceptance. Retain quarantined originals through the agreed rollback window. Final deletion is a separate reviewed irreversible step with fresh identity checks and authority. Failures preserve the journal and remaining files, with explicit partial status.
6. Measure actual volume allocation before and after a bounded isolated pilot, with an ordinary-copy control and recorded background-write/snapshot limits. Report observed deltas separately from logical and allocated candidate bytes. Do not claim live reclaimed space from the source implementation.

Rollback restores exact quarantined files and metadata under the same lock after checking canonical files and collision-free destinations. It never overwrites saves, profiles or a newly downloaded file. A failed or partial rollback retains evidence and stops that migration lane.

### Later prepared-asset conversion

Build a separate read-only manifest of catalogue-owned immutable prepared assets and matching imported sources. Match file contents, timestamps, mode, required extended attributes/ACLs and reference ownership. Exclude mutable profiles and saves entirely. Journal each proposed replacement and its independent preserved original. Clone into private same-volume staging, flush and verify content/metadata/source stability, then publish through a recoverable exchange. Do not delete preserved originals until identity, immutability, variant/catalogue stability and rollback acceptance pass. This operator and exact asset inventory are not implemented by the source changes.

## Acceptance evidence

Each production slice requires a passing characterization baseline before edits, a discriminating failing check, then passing implementation checks. Private receipts retain baseline revision and file hashes, commands, logs and corrections. A staging coverage gap was corrected by restoring both updater modules exactly to the base revision, adding and passing staging/startup baseline coverage, rerunning the failing new-behavior tests and reapplying the candidate.

Required independent review covers deletion, archive preservation, profile activation boundaries, native copying and this migration proposal. Record the exact reviewed file hashes/revision, findings and resolutions. Packaged backend verification is separate from source checks and notarized updater installation. No source or packaged check establishes actual gameplay, save/reopen acceptance, successful migration or reclaimed space. Existing engine-resource rules and variant identity semantics are unchanged; this filesystem optimization does not establish new engine compatibility claims.
