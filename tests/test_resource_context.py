"""Independent synthetic/adversarial contract expectations, no installed assets."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from smr_launcher import resource_context as rc
from smr_launcher import resource_identity as ri

ANCESTRY = "875991ac34dce2109647ae94f440a9de8e8bf07da13aa6159015ce71d45a025b"
EVIDENCE = "a" * 64
POLICY = rc.ordinary_policy({"bounded-review": EVIDENCE})


def rehash(snapshot):
    # Independent spelling of the established fingerprint identity contract.
    payload = {"schema": 1, "algorithm": ri.ALGORITHM,
               "roots": [{"role": r["role"], "entries": r["entries"]} for r in snapshot["roots"]]}
    snapshot["identity"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()
    return snapshot


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.roots = self.make_roots(self.base / "base")

    def make_roots(self, parent):
        roots = {}
        for role, name in zip(rc.EXPECTED_ROLES, ("Assets", "CustomAssets", "UserMaps")):
            root = parent / name
            root.mkdir(parents=True)
            roots[role] = root
        return roots

    def put(self, role="installed_assets", name="xml/Goods.XML", data=b"stock", ns=1000000000):
        path = self.roots[role] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, ns=(ns, ns))
        return path

    def snapshot(self):
        return rc.capture_resources(self.roots)

    def context(self, names=("goods.xml", "missing.nif"), snapshot=None, **options):
        args = dict(managers={"base": snapshot or self.snapshot()}, policy=POLICY,
                    inventory_sha256=ANCESTRY)
        args.update(options)
        return rc.resource_context(names, **args)

    def query(self, context, name="goods.xml"):
        return next(q for q in context["binding"]["queries"] if q["requested_name"] == name)

    def ids(self, snapshot, manager="base"):
        return {f'{r["role"]}:{e["path"]}': rc.candidate_id(manager, r, e)
                for r in snapshot["roots"] for e in r["entries"] if e["kind"] == "file"}

    def stamps(self, snapshot, values, manager="base"):
        ids = self.ids(snapshot, manager)
        return {ids[path]: {"filetime": value, "evidence_sha256": EVIDENCE}
                for path, value in values.items()}

    def test_complete_roles_even_for_empty_roots(self):
        self.assertEqual(self.query(self.context())["state"], "absent")
        for role in rc.EXPECTED_ROLES:
            incomplete = dict(self.roots)
            incomplete.pop(role)
            with self.assertRaisesRegex(rc.ResourceContextError, "Complete"):
                rc.capture_resources(incomplete)
            receipt = self.snapshot()
            receipt["roots"] = [r for r in receipt["roots"] if r["role"] != role]
            with self.assertRaises(rc.ResourceContextError):
                self.context(snapshot=receipt)

    def test_supplied_corrupt_receipts_never_pass(self):
        self.put()
        original = self.snapshot()
        corruptions = [lambda s: s.update(schema=True), lambda s: s.update(algorithm="other"),
                       lambda s: s["totals"].update(files=999),
                       lambda s: s["limits"].update(max_depth=False),
                       lambda s: s["roots"][0].update(location="relative/CustomAssets"),
                       lambda s: s["roots"][1]["entries"][-1].update(size=-1),
                       lambda s: s["roots"][1]["entries"][-1].update(sha256="bad"),
                       lambda s: s["roots"][1]["entries"][-1].update(kind="symlink"),
                       lambda s: s["roots"][1]["entries"][-1].update(path="../escape"),
                       lambda s: s["roots"][1]["entries"].pop(1),
                       lambda s: s["roots"][1]["entries"].append(s["roots"][1]["entries"][-1]),
                       lambda s: s["roots"][0].update(role="installed_assets"),
                       lambda s: s["roots"][0]["entries"][0].update(mtime_ns=True)]
        for mutate in corruptions:
            with self.subTest(mutate=mutate):
                receipt = copy.deepcopy(original)
                mutate(receipt)
                # Even a forged recomputed digest cannot rescue malformed entries.
                rehash(receipt)
                with self.assertRaises(rc.ResourceContextError):
                    self.context(snapshot=receipt)
        receipt = copy.deepcopy(original)
        receipt["identity"] = "0" * 64
        with self.assertRaisesRegex(rc.ResourceContextError, "digest"):
            self.context(snapshot=receipt)
        receipt = copy.deepcopy(original)
        receipt["roots"][1]["entries"][-1]["mtime_ns"] += 1
        with self.assertRaisesRegex(rc.ResourceContextError, "digest"):
            self.context(snapshot=receipt)

    def test_case_basename_and_path_rules(self):
        self.put()
        receipt = self.context(("Goods.XML", "elsewhere/GooDS.xml", r"elsewhere\GOODS.XML"))
        chosen = {q["model_candidate_id"] for q in receipt["binding"]["queries"]}
        self.assertEqual(len(chosen), 1)
        self.assertNotIn(None, chosen)
        self.assertTrue(all(q["normalized_basename"] == "goods.xml" for q in receipt["binding"]["queries"]))
        for name in ("", "/goods.xml", "../goods.xml", "a//goods.xml", "C:\\goods.xml", "*.xml", "a\x00.xml"):
            with self.subTest(name=name), self.assertRaises(rc.ResourceContextError):
                self.context((name,))

    def test_mixed_case_duplicates_do_not_assume_filesystem_time_conversion(self):
        self.put()
        self.put("user_maps", "scenario/gOoDs.xml", b"map", ns=2000000000)
        query = self.query(self.context())
        self.assertEqual(len(query["managers"]["base"]["candidates"]), 2)
        self.assertIsNone(query["model_candidate_id"])
        self.assertIn("comparable-stored-filetime-unknown-no-ns-conversion", query["blocking_unknowns"])

    def test_older_equal_newer_follow_stored_unsigned_time_only(self):
        self.put(ns=999999999999)
        self.put("user_maps", "Goods.xml", b"map", ns=1)
        snapshot = self.snapshot()
        ids = self.ids(snapshot)
        for incoming, expected in ((49, "installed_assets:xml/Goods.XML"), (51, "user_maps:Goods.xml"),
                                   (2**64 - 1, "user_maps:Goods.xml")):
            stamps = self.stamps(snapshot, {"installed_assets:xml/Goods.XML": 50, "user_maps:Goods.xml": incoming})
            query = self.query(self.context(snapshot=snapshot, stored_timestamps=stamps))
            self.assertEqual(query["model_candidate_id"], ids[expected])
            self.assertIsNone(query["runtime_selected_provider"])
        stamps = self.stamps(snapshot, {"installed_assets:xml/Goods.XML": 50, "user_maps:Goods.xml": 50})
        self.assertIn("equal-filetime-enumeration-unknown", self.query(
            self.context(snapshot=snapshot, stored_timestamps=stamps))["blocking_unknowns"])
        # Explicit evidence can put UserMaps earlier; never infer sorted walk or role order.
        order = [ids["user_maps:Goods.xml"], ids["installed_assets:xml/Goods.XML"]]
        enum = {"base": {"candidate_ids": order, "resource_identity": snapshot["identity"],
                         "evidence_sha256": EVIDENCE}}
        self.assertEqual(self.query(self.context(snapshot=snapshot, stored_timestamps=stamps,
                                                enumeration=enum))["model_candidate_id"], order[0])

    def test_stale_or_incomplete_selection_evidence_rejected(self):
        self.put()
        snapshot = self.snapshot()
        cid = next(iter(self.ids(snapshot).values()))
        for stamp in ({"filetime": -1, "evidence_sha256": EVIDENCE},
                      {"filetime": 2**64, "evidence_sha256": EVIDENCE},
                      {"filetime": True, "evidence_sha256": EVIDENCE},
                      {"filetime": 10, "evidence_sha256": "bad"}):
            with self.assertRaises(rc.ResourceContextError):
                self.context(snapshot=snapshot, stored_timestamps={cid: stamp})
        with self.assertRaises(rc.ResourceContextError):
            self.context(snapshot=snapshot, stored_timestamps={"unknown": {"filetime": 1, "evidence_sha256": EVIDENCE}})
        for ids, digest in (([], snapshot["identity"]), ([cid], "0" * 64), ([cid, cid], snapshot["identity"])):
            with self.assertRaises(rc.ResourceContextError):
                self.context(snapshot=snapshot, enumeration={"base": {"candidate_ids": ids,
                    "resource_identity": digest, "evidence_sha256": EVIDENCE}})

    def test_unicode_fullpath_crc_unknowns_block_claim(self):
        self.put()
        for field, value in (("full_path_names", None), ("full_path_names", True),
                             ("ordinary_crc_names", None), ("ordinary_crc_names", False)):
            policy = copy.deepcopy(POLICY)
            policy[field] = value
            self.assertEqual(self.query(self.context(policy=policy))["state"], "unresolved")
        self.assertEqual(self.query(self.context(("GÖODS.xml",)), "GÖODS.xml")["state"], "unresolved")
        self.put("user_maps", "İ.xml")
        self.assertEqual(self.query(self.context())["state"], "unresolved")

    def test_external_reverse_order_never_compares_cross_manager_mtimes(self):
        self.put(ns=999999999999)
        base = self.snapshot()
        roots = self.make_roots(self.base / "external")
        (roots["installed_assets"] / "GOODS.xml").write_bytes(b"external")
        os.utime(roots["installed_assets"] / "GOODS.xml", ns=(1, 1))
        external = rc.capture_resources(roots)
        roots2 = self.make_roots(self.base / "second")
        (roots2["custom_assets"] / "goods.xml").write_bytes(b"last")
        second = rc.capture_resources(roots2)
        managers = {"base": base, "first": external, "last": second}
        receipt = self.context(managers=managers, external_order={"managers": ["first", "last"], "evidence_sha256": EVIDENCE})
        query = self.query(receipt)
        self.assertEqual(query["model_candidate_id"], next(iter(self.ids(second, "last").values())))
        self.assertEqual(self.query(self.context(managers=managers))["state"], "unresolved")
        with self.assertRaises(rc.ResourceContextError):
            self.context(managers=managers, external_order={"managers": ["last"], "evidence_sha256": EVIDENCE})
        absent = rc.capture_resources(self.make_roots(self.base / "empty-external"))
        receipt = self.context(managers={"base": base, "empty": absent},
                               external_order={"managers": ["empty"], "evidence_sha256": EVIDENCE})
        self.assertEqual(self.query(receipt)["model_candidate_id"], next(iter(self.ids(base).values())))

    def test_context_binding_byte_mtime_directory_path_provider_and_negative_changes(self):
        file = self.put()
        initial = self.context()
        initial_snapshot = initial["binding"]["manager_snapshots"]["base"]
        self.assertEqual(initial["context_id"], self.context(snapshot=initial_snapshot,
                         provenance={"logging": "changed", "compiler": "different"})["context_id"])
        self.assertEqual(initial["context_id"], self.context(("missing.nif", "goods.xml", "goods.xml"),
                                                           snapshot=initial_snapshot)["context_id"])
        file.write_bytes(b"STOCK")
        os.utime(file, ns=(1000000000, 1000000000))
        changed_bytes = self.context()
        self.assertNotEqual(initial["context_id"], changed_bytes["context_id"])
        os.utime(file, ns=(1000000001, 1000000001))
        changed_time = self.context()
        self.assertNotEqual(changed_bytes["context_id"], changed_time["context_id"])
        directory = self.roots["installed_assets"] / "xml"
        os.utime(directory, ns=(2, 2))
        changed_directory = self.context()
        self.assertNotEqual(changed_time["context_id"], changed_directory["context_id"])
        self.put("user_maps", "missing.nif")
        appearing = self.context()
        self.assertNotEqual(changed_directory["context_id"], appearing["context_id"])
        self.assertEqual(self.query(appearing, "missing.nif")["state"], "model-candidate")
        clone = self.make_roots(self.base / "copy")
        for role in rc.EXPECTED_ROLES:
            shutil.copytree(self.roots[role], clone[role], dirs_exist_ok=True, copy_function=shutil.copy2)
        cloned = rc.capture_resources(clone)
        current = self.snapshot()
        self.assertEqual(current["identity"], cloned["identity"])
        self.assertNotEqual(self.context(snapshot=current)["context_id"], self.context(snapshot=cloned)["context_id"])
        renamed = copy.deepcopy(current)
        root = next(r for r in renamed["roots"] if r["role"] == "installed_assets")
        next(e for e in root["entries"] if e["kind"] == "file")["path"] = "xml/renamed.xml"
        self.assertNotEqual(self.context(snapshot=current)["context_id"], self.context(snapshot=rehash(renamed))["context_id"])
        extra = rc.capture_resources(self.make_roots(self.base / "new-provider"))
        self.assertNotEqual(self.context(snapshot=current)["context_id"], self.context(
            managers={"base": current, "new": extra}, external_order={"managers": ["new"], "evidence_sha256": EVIDENCE})["context_id"])

    def test_fpk_unknowns_include_negative_queries_and_container_changes(self):
        self.put()
        archive = self.put(name="stock.FPK", data=b"synthetic container", ns=10)
        initial = self.context()
        for name in ("goods.xml", "missing.nif"):
            query = self.query(initial, name)
            self.assertEqual(query["state"], "unresolved")
            self.assertIn("packed-members-unknown", query["blocking_unknowns"])
            self.assertEqual(len(query["managers"]["base"]["potential_packed_providers"]), 1)
        archive.write_bytes(b"changed container!!")
        os.utime(archive, ns=(10, 10))
        byte_change = self.context()
        self.assertNotEqual(initial["context_id"], byte_change["context_id"])
        os.utime(archive, ns=(11, 11))
        self.assertNotEqual(byte_change["context_id"], self.context()["context_id"])

    def test_unsafe_symlinks_rejected_by_existing_secure_capture(self):
        target = self.put()
        (self.roots["user_maps"] / "linked.xml").symlink_to(target)
        with self.assertRaises(ri.ResourceFingerprintError):
            self.snapshot()
        (self.roots["user_maps"] / "linked.xml").unlink()
        parent = self.base / "linked-parent"
        parent.symlink_to(self.roots["user_maps"].parent, target_is_directory=True)
        roots = dict(self.roots, user_maps=parent / "UserMaps")
        with self.assertRaises(ri.ResourceFingerprintError):
            rc.capture_resources(roots)

    def test_no_scans_in_resolution_and_bounded_cost(self):
        self.put()
        with patch.object(ri.os, "read", wraps=ri.os.read) as read:
            snapshot = self.snapshot()
        self.assertGreater(read.call_count, 0)
        with patch.object(ri.os, "read", side_effect=AssertionError("no disk reads")), \
             patch.object(ri, "fingerprint_resources", side_effect=AssertionError("no rescans")):
            receipt = self.context(tuple(f"missing-{i}.nif" for i in range(100)), snapshot=snapshot)
        self.assertEqual(receipt["cost"]["file_reads_during_resolution"], 0)
        self.assertEqual(receipt["cost"]["model_calls_during_resolution"], 0)
        self.assertEqual(receipt["cost"]["index_file_visits"], 1)
        self.assertEqual(receipt["cost"]["query_manager_evaluations"], 100)
        with self.assertRaises(rc.ResourceContextError):
            self.context(tuple(f"n{i}" for i in range(rc.MAX_QUERIES + 1)), snapshot=snapshot)
        with self.assertRaises(ri.ResourceFingerprintError):
            rc.capture_resources(self.roots, max_total_bytes=1)
        with patch.object(ri, "fingerprint_resources", side_effect=AssertionError("reject before scan")):
            for option, value in (("max_entries", rc.MAX_ENTRIES + 1),
                                  ("max_total_bytes", rc.MAX_BYTES + 1),
                                  ("max_depth", ri.DEFAULT_MAX_DEPTH + 1),
                                  ("max_file_bytes", ri.DEFAULT_MAX_FILE_BYTES + 1)):
                with self.assertRaisesRegex(rc.ResourceContextError, "ceilings"):
                    rc.capture_resources(self.roots, **{option: value})
        with patch.object(rc, "MAX_ENTRIES", 3), self.assertRaises(rc.ResourceContextError):
            self.context(snapshot=snapshot)

    def test_expansion_limit_and_explicit_empty_external_order(self):
        self.put(name="bundle.FPK")
        snapshot = self.snapshot()
        # Three root entries + one FPK; six potential archive references exceed
        # the patched result expansion budget of five, independent of capture.
        with patch.object(rc, "MAX_ENTRIES", 5), self.assertRaisesRegex(
                rc.ResourceContextError, "expansion"):
            self.context(tuple(f"missing-{i}" for i in range(6)), snapshot=snapshot)
        with self.assertRaises(rc.ResourceContextError):
            self.context(snapshot=snapshot, external_order={"managers": ["base"], "evidence_sha256": EVIDENCE})
        context = self.context(snapshot=snapshot, external_order={"managers": [], "evidence_sha256": EVIDENCE})
        self.assertEqual(self.query(context)["state"], "unresolved")
        self.assertEqual(context["binding"]["external_order"]["managers"], [])

    def test_policy_version_and_source_evidence_required_and_identity_sensitive(self):
        receipt = self.context()
        for field, value in (("version", "unversioned"), ("source_evidence_hashes", {}), ("source_evidence_hashes", None),
                             ("source_evidence_hashes", {"source": "bad"})):
            policy = copy.deepcopy(POLICY)
            policy[field] = value
            with self.assertRaises(rc.ResourceContextError):
                self.context(policy=policy)
        self.assertNotEqual(receipt["context_id"], self.context(
            policy=rc.ordinary_policy({"bounded-review": "b" * 64}))["context_id"])
        self.assertNotEqual(receipt["context_id"], self.context(inventory_sha256="b" * 64)["context_id"])
        self.assertEqual(receipt["binding"]["proof_level"], "offline-policy-model-no-engine-challenge")


if __name__ == "__main__":
    unittest.main()
