"""Independent synthetic inspection controls; no game assets or real maps."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import os
import stat
import struct
import tarfile
import tempfile
import unittest
import uuid

from smr_launcher import map_inspect, map_inspection


class MapInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.map = self.root / "map"
        self.folder = self.map / "UserMaps/Example"
        self.folder.mkdir(parents=True)
        self.game = self.root / "installation/Example.app"
        executable = self.game / "Contents/MacOS/Sid Meiers Railroads"
        executable.parent.mkdir(parents=True)
        # Synthetic Mach-O identity, no commercial instructions or data.
        binary = struct.pack("<8I", 0xFEEDFACF, 0x01000007, 3, 2, 1, 24, 0, 0)
        binary += struct.pack("<II", 0x1B, 24)
        binary += uuid.UUID("f07e6445-a31f-35b5-b2e5-7c1943e4947f").bytes
        executable.write_bytes(binary)
        self.stock = self.game.parent / "SMRailroadsData/assets"
        self.stock.mkdir(parents=True)
        self.put(
            self.stock / "rrt_industries.xml",
            "<RRTIndustries><Industries><RRTIndustry><szName>FactoryA</szName>"
            "<bInCity>1</bInCity></RRTIndustry></Industries></RRTIndustries>",
        )
        self.put(self.stock / "rrt_events.xml", self.events("Stock"))
        self.put(
            self.folder / "cities.xml",
            "<RRTCities><City><szName>CityKey</szName></City></RRTCities>",
        )
        self.put(
            self.folder / "events.xml",
            "<RRTEvents><Event><szTitle>Pulse</szTitle></Event></RRTEvents>",
        )
        self.put(self.folder / "industries.xml", self.industry("FactoryA"))
        self.scenario = self.folder / "RRT_Scenario_Example.xml"
        self.put(
            self.scenario,
            "<RRTScenario><IndustriesXMLFile>industries.xml</IndustriesXMLFile>"
            "<CityXMLFile>cities.xml</CityXMLFile><EventsXMLFile>events.xml</EventsXMLFile>"
            "</RRTScenario>",
        )

    @staticmethod
    def put(path, text):
        path.write_bytes(text.encode())

    @staticmethod
    def industry(name, *, placed=True, city="CityKey", city_flag="1"):
        location = (
            f"<Locations><Location><InCity>{city}</InCity></Location></Locations>" if placed else ""
        )
        return (
            f"<RRTIndustries><Industries><RRTIndustry><szName>{name}</szName>"
            f"<bInCity>{city_flag}</bInCity>{location}</RRTIndustry></Industries></RRTIndustries>"
        )

    @staticmethod
    def events(kind):
        return (
            "<RRTEvents><Event><szTitle>Pulse</szTitle><Effects><EventEffect>"
            f"<szType>{kind}</szType></EventEffect></Effects></Event></RRTEvents>"
        )

    def inspect(self, **kwargs):
        # Only the native mechanical boundary is substituted for these model
        # tests. Actual unknown-build admission is tested without substitution.
        with patch.object(
            map_inspection,
            "extract_supported_industry_registry",
            return_value=("FactoryA", "FactoryB"),
        ):
            return map_inspection.inspect_map(self.map, self.game, **kwargs)

    @staticmethod
    def facts(root):
        return {
            str(p.relative_to(root)): (
                hashlib.sha256(p.read_bytes()).hexdigest(),
                stat.S_IMODE(p.stat().st_mode),
                p.stat().st_mtime_ns,
            )
            for p in root.rglob("*")
            if p.is_file()
        }

    @staticmethod
    def findings(report, rule):
        return [f for f in report["findings"] if f["rule_id"] == rule]

    def test_success_schema_preservation_and_determinism(self):
        before = self.facts(self.root)
        a, b = self.inspect(), self.inspect()
        self.assertEqual(self.facts(self.root), before)
        self.assertEqual(a["schema_version"], 1)
        self.assertEqual(a["status"], "completed")
        self.assertEqual(a["runtime_acceptance"], "not_tested")
        self.assertEqual(
            a["preservation"], dict(input_unchanged=True, game_unchanged=True, stock_unchanged=True)
        )
        self.assertEqual(a["input"]["content_identity"], b["input"]["content_identity"])
        a.pop("observation")
        b.pop("observation")
        self.assertEqual(a, b)
        self.assertIn("save_references", {r["domain"] for r in a["coverage"]})
        self.assertTrue(all(f["category"] != "confirmed_invalid_condition" for f in a["findings"]))

    def test_scoped_inspection_excludes_save_data_and_binds_authored_changes(self):
        from smr_launcher import inspection_io

        scopes = ("CustomAssets", "UserMaps")
        saves = self.map / "Saves"
        saves.mkdir()
        (saves / "existing.sav").write_bytes(b"synthetic save")
        before = map_inspection.input_binding(self.map, self.game, scopes=scopes)
        (saves / "future.sav").write_bytes(b"synthetic future save")
        self.assertEqual(before, map_inspection.input_binding(self.map, self.game, scopes=scopes))
        report = self.inspect(scopes=scopes)
        self.assertEqual(report["input"]["scopes"], list(scopes))
        self.assertFalse(any(row["path"].startswith("Saves/") for row in report["input"]["files"]))
        self.put(self.folder / "industries.xml", self.industry("FactoryB"))
        self.assertNotEqual(
            before, map_inspection.input_binding(self.map, self.game, scopes=scopes)
        )
        (self.map / "CustomAssets").symlink_to(saves)
        with self.assertRaises(inspection_io.InspectionError):
            self.inspect(scopes=scopes)

    def test_mechanical_inspection_without_selected_installation_is_explicit(self):
        report = map_inspection.inspect_map(self.map, None)
        self.assertEqual(report["game"]["build_status"], "missing")
        self.assertIsNone(report["game"]["location"])
        self.assertIsNone(report["preservation"]["game_unchanged"])
        self.assertFalse(self.findings(report, "industry_registry"))

    def test_unknown_build_withholds_native_rules_but_keeps_inventory(self):
        self.put(self.folder / "industries.xml", self.industry("UnknownFactory"))
        (self.folder / "old.nif").write_bytes(
            b"NetImmerse File Format, Version 4.0.0.2\n" + bytes((2, 0, 0, 4))
        )
        report = map_inspection.inspect_map(self.map, self.game)
        self.assertEqual(report["game"]["build_status"], "unsupported")
        self.assertTrue(report["input"]["files"])
        self.assertFalse(self.findings(report, "industry_registry"))
        headers = self.findings(report, "model_header")
        self.assertEqual(headers[0]["observed"]["version"], "4.0.0.2")
        self.assertNotIn("legacy_collision_path", headers[0]["observed"])
        self.assertEqual(headers[0]["evidence_ids"], [])
        self.assertEqual(headers[0]["category"], "unimplemented_check")
        self.assertTrue(any(c["status"] == "unsupported" for c in report["coverage"]))

    def test_industry_case_placement_and_unused_counterexample(self):
        for name, placed, category in (
            ("FactoryA", True, None),
            ("factorya", True, "compatibility_risk"),
            (" FactoryA ", True, "compatibility_risk"),
            (" FactoryA ", False, "heuristic_lead"),
            ("UnknownFactory", True, "compatibility_risk"),
            ("UnknownFactory", False, "heuristic_lead"),
        ):
            with self.subTest(name=name, placed=placed):
                self.put(self.folder / "industries.xml", self.industry(name, placed=placed))
                rows = self.findings(self.inspect(), "industry_registry")
                self.assertEqual(
                    [x["category"] for x in rows], [] if category is None else [category]
                )
                if rows:
                    self.assertTrue(rows[0]["preconditions"])
                    self.assertTrue(rows[0]["evidence_ids"])
                    self.assertEqual(rows[0]["observed"]["keys"], [name])
                    self.assertIn("construction", rows[0]["limits"][0])

    def test_city_comparison_is_ascii_case_insensitive_and_rural_is_distinct(self):
        for city, flag, risk in (
            ("cityKEY", "1", False),
            ("", "1", True),
            ("Absent", "1", True),
            ("", "0", False),
        ):
            with self.subTest(city=city, flag=flag):
                self.put(
                    self.folder / "industries.xml",
                    self.industry("FactoryA", city=city, city_flag=flag),
                )
                self.assertEqual(bool(self.findings(self.inspect(), "city_owner")), risk)

    def test_selected_city_file_overrides_empty_default_candidate_for_observation(self):
        # Independent discriminator from actual scenario field spelling. A
        # similarly named field must not accidentally bind the city namespace.
        self.put(self.stock / "rrt_cities.xml", "<RRTCities/>")
        selected = self.inspect()
        self.assertFalse(self.findings(selected, "city_owner"))
        self.put(self.scenario, self.scenario.read_text().replace("CityXMLFile", "CitiesXMLFile"))
        unrecognized = self.inspect()
        self.assertTrue(self.findings(unrecognized, "city_owner"))

    def test_non_ascii_city_comparison_is_not_declared_unmatched(self):
        self.put(self.folder / "industries.xml", self.industry("FactoryA", city="Cité"))
        report = self.inspect()
        self.assertFalse(self.findings(report, "city_owner"))
        self.assertTrue(any("non-ASCII" in f["summary"] for f in report["findings"]))

    def test_duplicate_or_missing_city_keys_withhold_unmatched_conclusion(self):
        for names in ("<szName>CityKey</szName><szName>Other</szName>", ""):
            for owner in ("CityKey", "Absent"):
                with self.subTest(names=names, owner=owner):
                    self.put(
                        self.folder / "cities.xml", f"<RRTCities><City>{names}</City></RRTCities>"
                    )
                    self.put(self.folder / "industries.xml", self.industry("FactoryA", city=owner))
                    report = self.inspect()
                    self.assertFalse(self.findings(report, "city_owner"))
                    self.assertTrue(self.findings(report, "city_definition_keys"))
        self.put(self.folder / "industries.xml", self.industry("FactoryA", city=""))
        self.assertTrue(self.findings(self.inspect(), "city_owner"))

    def test_aggregate_candidate_edges_and_records_refuse_explicitly(self):
        with patch.object(map_inspection, "MAX_CANDIDATE_EDGES", 2):
            with self.assertRaisesRegex(ValueError, "candidate edge budget"):
                self.inspect()
        with patch.object(map_inspection, "MAX_REPORT_RECORDS", 2):
            with self.assertRaisesRegex(ValueError, "report record budget"):
                self.inspect()

    def test_event_cleanup_requires_base_recognized_effect_and_selected_child(self):
        cases = (
            ("Stock", "<RRTEvents><Event><szTitle>Pulse</szTitle></Event></RRTEvents>", False),
            (
                "Stock",
                "<RRTEvents><Event><szTitle>Pulse</szTitle><Effects/></Event></RRTEvents>",
                False,
            ),
            (
                "Stock",
                "<RRTEvents><Event><szTitle>Pulse</szTitle><Effects><Other/></Effects></Event></RRTEvents>",
                False,
            ),
            ("Stock", self.events("Good"), True),
            ("Stock", self.events("Unknown"), True),
            ("Unknown", self.events("Good"), False),
        )
        for base_kind, selected, risk in cases:
            with self.subTest(base=base_kind, selected=selected):
                self.put(self.stock / "rrt_events.xml", self.events(base_kind))
                self.put(self.folder / "events.xml", selected)
                self.assertEqual(
                    bool(self.findings(self.inspect(), "event_effect_replacement")), risk
                )

    def test_repeated_event_occurrences_remain_observable_and_title_case_is_exact(self):
        self.put(self.folder / "events.xml", self.events("Good").replace("Pulse", "pulse"))
        self.assertFalse(self.findings(self.inspect(), "event_effect_replacement"))
        repeated = self.events("Good").replace(
            "</RRTEvents>", self.events("Stock").removeprefix("<RRTEvents>")
        )
        self.put(self.folder / "events.xml", repeated)
        self.assertTrue(self.findings(self.inspect(), "event_effect_replacement"))

    def test_duplicate_reference_is_preserved_and_never_selects_first(self):
        self.put(
            self.scenario,
            "<RRTScenario><IndustriesXMLFile/><IndustriesXMLFile> industries.xml </IndustriesXMLFile></RRTScenario>",
        )
        report = self.inspect()
        refs = report["scenarios"][0]["reference_fields"]
        self.assertEqual([r["text"] for r in refs], [None, " industries.xml "])
        self.assertTrue(all(r["selection"] == "unresolved" for r in refs))
        self.assertFalse(self.findings(report, "industry_registry"))

    def test_map_and_stock_candidates_are_both_retained(self):
        self.put(self.stock / "industries.xml", self.industry("FactoryB"))
        report = self.inspect()
        ref = report["scenarios"][0]["reference_fields"][0]
        self.assertEqual({c["scope"] for c in ref["candidates"]}, {"map", "stock"})
        self.assertEqual(ref["selection"], "unresolved")
        self.assertFalse(self.findings(report, "industry_registry"))

    def test_reference_reasons_distinguish_uncertainty_from_missing_files(self):
        self.put(self.stock / "events.xml", self.events("Good"))
        self.put(
            self.scenario,
            "<RRTScenario><GoodsXMLFile>absent.xml</GoodsXMLFile><TrainsXMLFile/>"
            "<CityXMLFile> cities.xml </CityXMLFile><EventsXMLFile>events.xml</EventsXMLFile>"
            "<IndustriesXMLFile>industries.xml</IndustriesXMLFile>"
            "<ExtraXMLFile>one.xml</ExtraXMLFile><ExtraXMLFile>two.xml</ExtraXMLFile></RRTScenario>",
        )
        report = self.inspect()
        refs = report["scenarios"][0]["reference_fields"]
        self.assertEqual(
            [r["reason"] for r in refs],
            [
                "no_loose_candidate",
                "empty_reference",
                "unsupported_reference_text",
                "ambiguous_loose_candidates",
                "unique_loose_candidate",
                "duplicate_field",
                "duplicate_field",
            ],
        )
        self.assertTrue(all(r["native_selection"] == "unproven" for r in refs))
        self.assertEqual(
            report["resource_resolution"]["explicit_reference_counts"]["duplicate_field"], 2
        )
        ambiguous = refs[3]
        self.assertEqual({r["scope"] for r in ambiguous["candidates"]}, {"map", "stock"})

    def test_parser_failure_diagnostics_do_not_rewrite_or_claim_engine_invalidity(self):
        for data, reason in (
            (
                b'<?xml version="1.0" encoding="UTF-8"?><RRTScenario>caf\xe9</RRTScenario>',
                "invalid_utf8_bytes",
            ),
            (b"<RRTScenario>", "incomplete_or_empty_document"),
            (b"<RRTScenario>A & B</RRTScenario>", "xml_syntax_profile"),
            (b"<!DOCTYPE RRTScenario><RRTScenario/>", "declaration_profile"),
        ):
            with self.subTest(reason=reason):
                self.scenario.write_bytes(data)
                report = self.inspect()
                finding = self.findings(report, "xml_parser")[0]
                diagnostic = finding["observed"]["parser"]
                self.assertEqual(diagnostic["reason"], reason)
                self.assertEqual(diagnostic["engine_acceptance"], "unresolved")
                self.assertEqual(self.scenario.read_bytes(), data)
                self.assertEqual(finding["category"], "unimplemented_check")
                if reason != "declaration_profile":
                    self.assertIsInstance(diagnostic["line"], int)
                    self.assertIsInstance(diagnostic["column"], int)

    def test_parser_refusal_is_coverage_not_engine_invalidity(self):
        for payload in (
            "<bad",
            '<!DOCTYPE RRTScenario [<!ENTITY x "expanded">]><RRTScenario>&x;</RRTScenario>',
        ):
            with self.subTest(payload=payload):
                self.put(self.scenario, payload)
                report = self.inspect()
                self.assertEqual(report["scenarios"][0]["parse_status"], "unsupported")
                self.assertTrue(self.findings(report, "xml_parser"))
                self.assertFalse(
                    any(f["category"] == "confirmed_invalid_condition" for f in report["findings"])
                )

    def test_direct_scenario_directory_is_supported(self):
        with patch.object(
            map_inspection, "extract_supported_industry_registry", return_value=("FactoryA",)
        ):
            report = map_inspection.inspect_map(self.folder, self.game)
        self.assertEqual(report["scenarios"][0]["path"], "RRT_Scenario_Example.xml")

    def test_symlink_and_file_limit_refuse_without_following_source(self):
        (self.folder / "linked").symlink_to(self.stock)
        with self.assertRaises((OSError, ValueError, RuntimeError)):
            self.inspect()
        (self.folder / "linked").unlink()
        with patch.object(map_inspection, "MAX_FILE_BYTES", 8):
            with self.assertRaises((OSError, ValueError, RuntimeError)):
                self.inspect()

    def test_game_stock_and_source_linked_ancestors_refuse(self):
        alias = self.root / "alias"
        alias.symlink_to(self.game.parent)
        with self.assertRaises((OSError, ValueError, RuntimeError)):
            map_inspection.inspect_map(self.map, alias / self.game.name)
        stock = self.stock
        moved = self.stock.with_name("real-assets")
        stock.rename(moved)
        stock.symlink_to(moved)
        with self.assertRaises((OSError, ValueError, RuntimeError)):
            self.inspect()

    def test_fifo_is_refused_without_opening_a_blocking_stream(self):
        os.mkfifo(self.folder / "pipe")
        with self.assertRaises((OSError, ValueError, RuntimeError)):
            self.inspect()

    def test_non_ascii_resource_name_is_not_ascii_folded_into_a_candidate(self):
        self.put(self.folder / "\u212akelvin.xml", "<RRTIndustries/>")
        self.put(
            self.scenario,
            "<RRTScenario><IndustriesXMLFile>kelvin.xml</IndustriesXMLFile></RRTScenario>",
        )
        report = self.inspect()
        self.assertEqual(report["scenarios"][0]["reference_fields"][0]["candidates"], [])
        self.assertTrue(self.findings(report, "resource_name_profile"))

    def test_xml_depth_and_size_refusals_do_not_expand_entities(self):
        self.put(self.scenario, "<RRTScenario>" + "<x>" * 65 + "</x>" * 65 + "</RRTScenario>")
        self.assertEqual(self.inspect()["scenarios"][0]["parse_status"], "unsupported")
        with patch.object(map_inspection, "MAX_XML_BYTES", 4):
            report = self.inspect()
        self.assertEqual(report["scenarios"][0]["parse_status"], "unsupported")

    def test_archive_and_directory_content_projections_match(self):
        archive = self.root / "example.tar"
        with tarfile.open(archive, "w") as stream:
            for path in sorted(self.map.rglob("*")):
                if path.is_file():
                    stream.add(path, arcname=path.relative_to(self.map).as_posix(), recursive=False)
        workspace = self.root / "snapshots"
        workspace.mkdir()
        with patch.object(
            map_inspection,
            "extract_supported_industry_registry",
            return_value=("FactoryA", "FactoryB"),
        ):
            packed = map_inspection.inspect_map(archive, self.game, workspace_parent=workspace)
        loose = self.inspect()
        self.assertEqual(packed["input"]["content_identity"], loose["input"]["content_identity"])
        self.assertEqual(packed["findings"], loose["findings"])
        self.assertEqual(packed["scenarios"], loose["scenarios"])
        self.assertTrue(Path(packed["observation"]["archive_snapshot"]).is_dir())

    def test_unsafe_archive_is_refused_and_retained_snapshot_is_identified(self):
        from io import BytesIO

        archive = self.root / "unsafe.tar"
        with tarfile.open(archive, "w") as stream:
            info = tarfile.TarInfo("../outside")
            info.size = 1
            stream.addfile(info, BytesIO(b"x"))
        workspace = self.root / "snapshots"
        workspace.mkdir()
        with self.assertRaisesRegex(ValueError, "snapshot retained"):
            map_inspection.inspect_map(archive, self.game, workspace_parent=workspace)
        self.assertFalse((self.root / "outside").exists())
        self.assertEqual(len(list(workspace.iterdir())), 1)

    def test_later_archive_refusal_preserves_snapshot_in_cli_json_and_text(self):
        from smr_launcher import inspection_io

        archive = self.root / "example.tar"
        with tarfile.open(archive, "w") as output:
            output.add(self.scenario, arcname=self.scenario.relative_to(self.map).as_posix())
        workspace = self.root / "snapshots"
        workspace.mkdir()
        args = ["inspect", str(archive), "--game", str(self.game), "--scenario", "absent.xml"]
        for format_name in ("json", "text"):
            stream = StringIO()
            with patch.object(inspection_io, "PRIVATE_ROOT", workspace), redirect_stdout(stream):
                self.assertEqual(map_inspect.main(args + ["--format", format_name]), 2)
            if format_name == "json":
                report = json.loads(stream.getvalue())
                snapshot = Path(report["observation"]["archive_snapshot"])
                self.assertTrue(snapshot.is_dir())
                self.assertTrue((snapshot / "source.archive").is_file())
                self.assertIsNone(report["preservation"]["input_unchanged"])
                self.assertEqual(report["status"], "refused")
            else:
                self.assertIn("Archive snapshot retained: ", stream.getvalue())

    def test_report_write_failure_preserves_completed_inspection_context(self):
        from smr_launcher import inspection_io

        stream = StringIO()
        with (
            patch.object(inspection_io, "write_report", side_effect=OSError("write failed")),
            redirect_stdout(stream),
        ):
            code = map_inspect.main(
                [
                    "inspect",
                    str(self.map),
                    "--game",
                    str(self.game),
                    "--format",
                    "json",
                    "--report",
                    str(self.root / "new.json"),
                ]
            )
        report = json.loads(stream.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "refused")
        self.assertIsNotNone(report["input"])
        self.assertTrue(report["preservation"]["input_unchanged"])

    def test_cli_json_text_exclusive_private_report_and_stock_protection(self):
        args = ["inspect", str(self.map), "--game", str(self.game)]
        output = self.root / "report.json"
        stream = StringIO()
        with redirect_stdout(stream):
            self.assertEqual(
                map_inspect.main(args + ["--format", "json", "--report", str(output)]), 0
            )
        self.assertEqual(json.loads(stream.getvalue())["status"], "completed")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        before = output.read_bytes()
        for bad in (output, self.stock / "new-report.json", self.folder / "new-report.json"):
            with self.subTest(output=bad), redirect_stdout(StringIO()):
                self.assertEqual(
                    map_inspect.main(args + ["--format", "json", "--report", str(bad)]), 2
                )
        self.assertEqual(output.read_bytes(), before)
        self.assertFalse((self.stock / "new-report.json").exists())
        linked = self.root / "stock-alias"
        linked.symlink_to(self.stock)
        with redirect_stdout(StringIO()):
            self.assertEqual(map_inspect.main(args + ["--report", str(linked / "new.json")]), 2)
        stream = StringIO()
        with redirect_stdout(stream):
            self.assertEqual(map_inspect.main(args), 0)
        self.assertIn("not_tested", stream.getvalue())

    def test_cli_refusal_schema_and_git_output_do_not_create_files(self):
        repo_output = Path(__file__).resolve().parents[1] / "never-created-inspection.json"
        self.assertFalse(repo_output.exists())
        stream = StringIO()
        with redirect_stdout(stream):
            code = map_inspect.main(
                [
                    "inspect",
                    str(self.map),
                    "--game",
                    str(self.game),
                    "--format",
                    "json",
                    "--report",
                    str(repo_output),
                ]
            )
        report = json.loads(stream.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "refused")
        self.assertEqual(report["runtime_acceptance"], "not_tested")
        self.assertIsNone(report["preservation"]["input_unchanged"])
        self.assertFalse(repo_output.exists())

    def test_changed_map_or_executable_is_detected(self):
        original = map_inspection.parse_document
        executable = self.game / "Contents/MacOS/Sid Meiers Railroads"
        for target in (self.scenario, executable):
            before = target.read_bytes()
            changed = False

            def mutate(data, *args, **kwargs):
                nonlocal changed
                result = original(data, *args, **kwargs)
                if not changed:
                    changed = True
                    target.write_bytes(before + b" ")
                return result

            with (
                self.subTest(target=target),
                patch.object(map_inspection, "parse_document", side_effect=mutate),
            ):
                with self.assertRaises((OSError, ValueError, RuntimeError)):
                    self.inspect()
            target.write_bytes(before)

    def test_replaced_consumed_stock_is_detected_before_success(self):
        original = map_inspection.parse_document
        changed = False

        def mutate(data, *args, **kwargs):
            nonlocal changed
            result = original(data, *args, **kwargs)
            if not changed:
                changed = True
                self.put(self.stock / "rrt_events.xml", self.events("Good"))
            return result

        with patch.object(map_inspection, "parse_document", side_effect=mutate):
            with self.assertRaises((OSError, ValueError, RuntimeError)):
                self.inspect()
