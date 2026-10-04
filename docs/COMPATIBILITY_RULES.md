# Compatibility rules

The launcher keeps a user-supplied map archive as an independent, read-only
original. An Original variant copies the archive's install roots into a
complete, separately prepared game profile without the stock profile's saves.
A compatibility edition is a different prepared profile with a different
content identity. The rule engine in `src/smr_launcher/rules.py` changes only
that disposable prepared copy.

Each rule declares a stable ID and positive version, provenance and reason,
exact source archive and game executable SHA-256 hashes, and one or more file
patches. Each patch has a path under `UserMaps/` or `CustomAssets/`, a full
file preimage hash, a unique old byte sequence, replacement bytes, and a full
expected output hash. Rule selection requires exact archive and game matches;
multiple versions of one rule and overlapping or case-colliding paths fail.
The engine validates every patch before writing any. It leaves an already
patched file alone when its output hash matches, so the same rule is
repeatable. The prepared edition ID includes the ordered rule fingerprint
and output asset hash.

No real repair rule is registered yet. A static inspection result or synthetic
fixture does not prove gameplay compatibility. A future real rule should be
added only after a specific failure is reproduced, the narrow byte change is
reviewed, and the result is tested through loading, gameplay, manual save,
quit, reload, and continued play on the named Steam build. The verification
record must identify the archive hash, game hash, rule versions, prepared
output hash, observations, and test date. Any changed input invalidates it.

The synthetic fixtures in `tests/test_rules.py` and
`tests/test_variants.py` cover exact applicability, unexpected inputs,
idempotence, overlap, original preservation, and separate compatibility
identity. They contain no downloaded map or game content.
