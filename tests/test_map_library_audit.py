"""Synthetic loader distinctions and conservative resolution regression fixtures."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/research/audit_map_library.py"
SPEC = importlib.util.spec_from_file_location("map_library_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def records(text):
    return list(ET.fromstring("<root>" + text + "</root>"))


class MapLibraryAuditTests(unittest.TestCase):
    def test_goods_registry_excludes_scenario_only_names_and_prunes(self):
        base = records("<Good><szName>A</szName></Good><Good><szName>B</szName></Good>")
        other = records("<Good><szName>B</szName></Good><Good><szName>C</szName></Good>")
        self.assertEqual(audit.goods_registry(base, other), {"B"})
        self.assertEqual(audit.goods_registry(base, []), {"A", "B"})
        self.assertEqual(set(audit.merge_records(base, other)), {"B", "C"})

    def test_present_child_replaces_vector_but_empty_container_inherits(self):
        base = records("<Industry><szName>A</szName><bInCity>0</bInCity><Production>"
                       "<Resource><Output>B</Output></Resource></Production></Industry>")
        empty = records("<Industry><szName>A</szName><Production/></Industry>")
        pair = audit.merge_records(base, empty)["A"]
        self.assertEqual(audit.field(pair, "bInCity"), "0")
        self.assertEqual(audit.sequence(pair, "./Production/Resource")[0].findtext("Output"), "B")
        replacement = records("<Industry><szName>A</szName><Production>"
                              "<Resource><Output>C</Output></Resource></Production></Industry>")
        pair = audit.merge_records(base, replacement)["A"]
        self.assertEqual([n.findtext("Output") for n in audit.sequence(pair, "./Production/Resource")], ["C"])

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        stock, root = base / "stock", base / "map"
        stock.mkdir(); root.mkdir()
        for name in ("Goods", "Industries", "TrainCars", "Depots", "Bridges"):
            (stock / ("rrt_" + name.lower() + ".xml")).write_text("<RRT" + name + "/>")
        (stock / "rrt_goods.xml").write_text("<RRTGoods><Good><szName>Stock</szName></Good></RRTGoods>")
        (root / "scenario.xml").write_text("<RRTScenario><GoodsXMLFile>RRT_Goods.xml</GoodsXMLFile>"
                                           "<IndustriesXMLFile>custom.xml</IndustriesXMLFile></RRTScenario>")
        return stock, root

    def test_global_map_override_and_missing_references_without_mutation(self):
        stock, root = self.fixture()
        (root / "RRT_Goods.xml").write_text("<RRTGoods><Good><szName>Custom</szName></Good></RRTGoods>")
        (root / "custom.xml").write_text("<RRTIndustries><Industries><RRTIndustry><szName>New</szName>"
            "<bInCity>0</bInCity><Production><Resource><Input>NONE</Input><Output>Lost</Output>"
            "</Resource></Production></RRTIndustry></Industries></RRTIndustries>")
        before = {p: p.read_bytes() for p in root.iterdir()}
        result = audit.Scanner(stock, ("Allowed",)).scan(root, ["scenario.xml"])
        self.assertIn("production_good_not_registered", result["categories"])
        self.assertIn("rural_annex_not_defined", result["categories"])
        missing = [f["good"] for f in result["findings"] if f["code"] == "registered_good_without_car"]
        self.assertEqual(missing, ["Custom"])
        self.assertEqual(before, {p: p.read_bytes() for p in root.iterdir()})
        self.assertEqual([f["good"] for f in result["findings"] if f["code"] == "production_good_not_registered"], ["Lost"])

    def test_duplicate_xml_is_unresolved_and_skips_dependent_inference(self):
        stock, root = self.fixture()
        for directory in ("a", "b"):
            (root / directory).mkdir()
            (root / directory / "custom.xml").write_text("<RRTIndustries/>")
        result = audit.Scanner(stock, ()).scan(root, ["scenario.xml"])
        self.assertIn("duplicate_xml_basename", result["categories"])
        self.assertTrue(result["scenarios"][0]["resources"]["industries"]["unresolved"])
        self.assertNotIn("industry_capacity", result["scenarios"][0])

    def test_capacity_and_scenario_scope_are_separate(self):
        stock, root = self.fixture()
        (root / "custom.xml").write_text("<RRTIndustries><Industries>"
            "<RRTIndustry><szName>A</szName></RRTIndustry><RRTIndustry><szName>B</szName></RRTIndustry>"
            "</Industries></RRTIndustries>")
        result = audit.Scanner(stock, ("A",)).scan(root, ["scenario.xml"])
        findings = [f for f in result["findings"] if f["code"] == "industry_alias_capacity_exceeded"]
        self.assertEqual(findings[0]["scenario"], "scenario.xml")
        self.assertEqual(findings[0]["capacity"], 1)

    def test_new_industry_defaults_rural_and_unknown_bridge_is_not_effective(self):
        stock, root = self.fixture()
        (root / "custom.xml").write_text("<RRTIndustries><Industries><RRTIndustry>"
            "<szName>New</szName></RRTIndustry></Industries></RRTIndustries>")
        (root / "RRT_Bridges.xml").write_text("<RRTBridges><Bridge><szName>Custom</szName>"
            "<nSpanStart>100</nSpanStart><nSpanEnd>0</nSpanEnd></Bridge></RRTBridges>")
        result = audit.Scanner(stock, (), ("Allowed",)).scan(root, ["scenario.xml"])
        self.assertIn("rural_annex_not_defined", result["categories"])
        self.assertIn("bridge_outside_registry", result["categories"])
        self.assertNotIn("bridge_reversed_range", result["categories"])

    def test_duplicate_definition_and_wrong_root_do_not_claim_closure(self):
        stock, root = self.fixture()
        (root / "custom.xml").write_text("<RRTIndustries><Industries>"
            "<RRTIndustry><szName>A</szName></RRTIndustry><RRTIndustry><szName>A</szName></RRTIndustry>"
            "</Industries></RRTIndustries>")
        result = audit.Scanner(stock, ()).scan(root, ["scenario.xml"])
        self.assertIn("definition_key_review", result["categories"])
        self.assertTrue(result["scenarios"][0]["resources"]["industries"]["unresolved"])
        self.assertNotIn("industry_capacity", result["scenarios"][0])
        (root / "custom.xml").write_text("<Wrong><Industries/></Wrong>")
        result = audit.Scanner(stock, ()).scan(root, ["scenario.xml"])
        self.assertIn("unexpected_dependency_root", result["categories"])

    def test_boolean_parser_and_missing_terminal(self):
        self.assertFalse(audit.engine_bool("FALSE"))
        self.assertFalse(audit.engine_bool(""))
        self.assertTrue(audit.engine_bool("true"))
        self.assertTrue(audit.engine_bool(" -2rest"))
        stock, root = self.fixture()
        (root / "custom.xml").write_text("<RRTIndustries/>")
        result = audit.Scanner(stock, ()).scan(root, ["scenario.xml"])
        self.assertIn("standard_terminal_not_defined", result["categories"])

    def test_findings_are_scoped_to_the_scenario_that_uses_the_dependency(self):
        stock, root = self.fixture()
        (stock / "rrt_industries.xml").write_text(
            "<RRTIndustries><Industries><RRTIndustry><szName>Stock</szName>"
            "</RRTIndustry></Industries></RRTIndustries>")
        (root / "good.xml").write_text(
            "<RRTIndustries><Industries><RRTIndustry><szName>Allowed</szName>"
            "</RRTIndustry></Industries></RRTIndustries>")
        (root / "bad.xml").write_text(
            "<RRTIndustries><Industries><RRTIndustry><szName>Outside</szName>"
            "</RRTIndustry></Industries></RRTIndustries>")
        for scenario, dependency in (("one.xml", "good.xml"), ("two.xml", "bad.xml")):
            (root / scenario).write_text(
                "<RRTScenario><IndustriesXMLFile>" + dependency + "</IndustriesXMLFile></RRTScenario>")

        result = audit.Scanner(stock, ("Allowed",)).scan(root, ["one.xml", "two.xml"])

        outside = [f for f in result["findings"] if f["code"] == "industry_outside_registry"]
        self.assertEqual([(f["scenario"], f["name"]) for f in outside], [("two.xml", "Outside")])
        rows = {row["scenario"]: row for row in result["scenarios"]}
        self.assertFalse(rows["one.xml"]["resources"]["industries"]["unresolved"])
        self.assertFalse(rows["two.xml"]["resources"]["industries"]["unresolved"])

    def test_cars_and_tenders_prune_their_own_subtrees(self):
        base = records(
            "<RRTGoodCars><TrainCar><szName>Coal Car</szName><szGood>Coal</szGood></TrainCar>"
            "<TrainCar><szName>Mail Car</szName><szGood>Mail</szGood></TrainCar></RRTGoodCars>"
            "<RRTTenderCars><TenderCar><szName>Coal Tender</szName></TenderCar>"
            "<TenderCar><szName>Mail Tender</szName></TenderCar></RRTTenderCars>")
        override = records(
            "<RRTGoodCars><TrainCar><szName>Coal Car</szName><szGood>Coal</szGood></TrainCar>"
            "</RRTGoodCars><RRTTenderCars><TenderCar><szName>Mail Tender</szName>"
            "</TenderCar></RRTTenderCars>")

        cars = audit.merge_records(base[0].findall("./TrainCar"), override[0].findall("./TrainCar"))
        tenders = audit.merge_records(base[1].findall("./TenderCar"), override[1].findall("./TenderCar"))

        self.assertEqual(set(cars), {"Coal Car"})
        self.assertEqual(set(tenders), {"Mail Tender"})

    def test_case_only_car_good_match_is_consistent_in_both_directions(self):
        stock, root = self.fixture()
        (stock / "rrt_goods.xml").write_text(
            "<RRTGoods><Good><szName>Iron</szName></Good></RRTGoods>")
        (root / "RRT_Goods.xml").write_text(
            "<RRTGoods><Good><szName>Iron</szName></Good></RRTGoods>")
        (root / "custom.xml").write_text("<RRTIndustries><Industries/></RRTIndustries>")
        (root / "RRT_TrainCars.xml").write_text(
            "<RRTTrainCars><RRTGoodCars><TrainCar><szName>Iron Car</szName>"
            "<szGood>IRON</szGood></TrainCar></RRTGoodCars></RRTTrainCars>")

        result = audit.Scanner(stock, ()).scan(root, ["scenario.xml"])

        codes = [f["code"] for f in result["findings"]]
        self.assertNotIn("car_good_not_registered", codes)
        case_review = [f for f in result["findings"] if f["code"] == "car_good_case_review"]
        self.assertEqual(len(case_review), 1)
        self.assertEqual(case_review[0]["good"], "IRON")
        self.assertEqual(case_review[0]["casefold_matches"], ["Iron"])
        self.assertNotIn("registered_good_without_car", codes)

    def test_duplicate_car_matches_are_reported_case_insensitively(self):
        stock, root = self.fixture()
        (root / "RRT_Goods.xml").write_text(
            "<RRTGoods><Good><szName>Iron</szName></Good></RRTGoods>")
        (root / "RRT_TrainCars.xml").write_text(
            "<RRTTrainCars><RRTGoodCars>"
            "<TrainCar><szName>Iron Car</szName><szGood>Iron</szGood></TrainCar>"
            "<TrainCar><szName>Alternate Iron Car</szName><szGood>IRON</szGood></TrainCar>"
            "</RRTGoodCars></RRTTrainCars>")

        result = audit.Scanner(stock, ()).scan(root, ["scenario.xml"])

        duplicate = [f for f in result["findings"] if f["code"] == "duplicate_car_good_review"]
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0]["good"], "iron")
        self.assertEqual(set(duplicate[0]["cars"]), {"Iron Car", "Alternate Iron Car"})

    def test_reference_whitespace_is_flagged_but_trimmed_for_resolution(self):
        stock, root = self.fixture()
        (root / "scenario.xml").write_text(
            "<RRTScenario><GoodsXMLFile> RRT_Goods.xml </GoodsXMLFile>"
            "<IndustriesXMLFile> custom.xml </IndustriesXMLFile></RRTScenario>")
        (root / "custom.xml").write_text("<RRTIndustries><Industries/></RRTIndustries>")

        result = audit.Scanner(stock, ()).scan(root, ["scenario.xml"])

        whitespace = [f for f in result["findings"] if f["code"] == "xml_reference_whitespace_review"]
        unresolved = [f for f in result["findings"] if f["code"] == "xml_reference_unresolved"]
        self.assertEqual({f["field"] for f in whitespace}, {"GoodsXMLFile", "IndustriesXMLFile"})
        self.assertEqual(unresolved, [])
        self.assertEqual(result["scenarios"][0]["resources"]["industries"]["scenario"]["path"], str(root / "custom.xml"))

    def test_terminal_missing_from_pruned_depots_is_reported_per_scenario(self):
        stock, root = self.fixture()
        (stock / "rrt_depots.xml").write_text(
            "<RRTDepots><Depot><szName>Terminal</szName></Depot></RRTDepots>")
        (root / "with-terminal.xml").write_text(
            "<RRTScenario><DepotsXMLFile>with.xml</DepotsXMLFile></RRTScenario>")
        (root / "without-terminal.xml").write_text(
            "<RRTScenario><DepotsXMLFile>without.xml</DepotsXMLFile></RRTScenario>")
        (root / "with.xml").write_text(
            "<RRTDepots><Depot><szName>Terminal</szName></Depot></RRTDepots>")
        (root / "without.xml").write_text(
            "<RRTDepots><Depot><szName>Freight</szName></Depot></RRTDepots>")

        result = audit.Scanner(stock, ()).scan(root, ["with-terminal.xml", "without-terminal.xml"])

        missing = [f for f in result["findings"] if f["code"] == "standard_terminal_not_defined"]
        self.assertEqual([f["scenario"] for f in missing], ["without-terminal.xml"])

    def test_valid_utf16_xml_parses_and_invalid_utf16_is_localized(self):
        stock, root = self.fixture()
        valid = '<?xml version="1.0" encoding="UTF-16"?><RRTIndustries><Industries/></RRTIndustries>'
        (root / "valid-utf16.xml").write_bytes(valid.encode("utf-16"))
        invalid = b"\xff\xfe<\x00R\x00R\x00T\x00Industries\x00>\x00"
        (root / "invalid-utf16.xml").write_bytes(invalid)
        (root / "valid.xml").write_text(
            "<RRTScenario><IndustriesXMLFile>valid-utf16.xml</IndustriesXMLFile></RRTScenario>")
        (root / "invalid.xml").write_text(
            "<RRTScenario><IndustriesXMLFile>invalid-utf16.xml</IndustriesXMLFile></RRTScenario>")

        result = audit.Scanner(stock, ()).scan(root, ["valid.xml", "invalid.xml"])

        unreadable = [f for f in result["findings"] if f["code"] in ("xml_unreadable", "xml_dependency_unreadable")]
        self.assertTrue(any(f.get("file") == "invalid-utf16.xml" for f in unreadable))
        self.assertTrue(any(f.get("scenario") == "invalid.xml" for f in unreadable))
        rows = {row["scenario"]: row for row in result["scenarios"]}
        self.assertFalse(rows["valid.xml"]["resources"]["industries"]["unresolved"])
        self.assertTrue(rows["invalid.xml"]["resources"]["industries"]["unresolved"])


if __name__ == "__main__":
    unittest.main()
