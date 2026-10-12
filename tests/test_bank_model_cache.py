"""Pure byte reuse with invented admission seals and fresh recipe contexts."""

from dataclasses import replace
import hashlib
import os
import unittest
from unittest.mock import patch

from smr_launcher import bank_model
from smr_launcher.rules import RuleError
from smr_launcher.variants import assets_manifest_hash
from test_bank_model import synthetic_model
import test_recipe_service as recipe_fixtures


def seals(source, output):
    # These admissions belong only to invented test data, never real assets.
    return patch.multiple(
        bank_model,
        SOURCE_SHA256=hashlib.sha256(source).hexdigest(),
        SOURCE_SIZE=len(source),
        OUTPUT_SHA256=hashlib.sha256(output).hexdigest(),
        OUTPUT_SIZE=len(output),
    )


class BankConversionCacheTests(unittest.TestCase):
    def setUp(self):
        bank_model._convert_reviewed_bank.cache_clear()
        self.addCleanup(bank_model._convert_reviewed_bank.cache_clear)
        self.source, _ = synthetic_model()
        self.expected, _ = synthetic_model(modern=True)

    def test_exact_bytes_reuse_and_warm_cache_keeps_input_refusals(self):
        with (
            seals(self.source, self.expected),
            patch.object(
                bank_model, "_convert_supported_layout", wraps=bank_model._convert_supported_layout
            ) as convert,
        ):
            for _ in range(2):
                self.assertEqual(bank_model.convert_legacy_bank(self.source), self.expected)
            self.assertEqual(convert.call_count, 1)
            for value in (
                bytearray(self.source),
                memoryview(self.source),
                self.source[:-1],
                b"X" + self.source[1:],
            ):
                with self.subTest(kind=type(value)), self.assertRaises(ValueError):
                    bank_model.convert_legacy_bank(value)
            self.assertEqual(convert.call_count, 1)

    def test_warm_cache_cannot_override_any_changed_contract_field(self):
        with seals(self.source, self.expected):
            self.assertEqual(bank_model.convert_legacy_bank(self.source), self.expected)
            changes = {
                "SOURCE_SHA256": "0" * 64,
                "SOURCE_SIZE": len(self.source) + 1,
                "SOURCE_VERSION": 0x04020201,
                "OUTPUT_SHA256": "0" * 64,
                "OUTPUT_SIZE": len(self.expected) + 1,
                "OUTPUT_VERSION": 0x0A000101,
            }
            for name, value in changes.items():
                with self.subTest(field=name), patch.object(bank_model, name, value):
                    with self.assertRaises(ValueError):
                        bank_model.convert_legacy_bank(self.source)
            self.assertEqual(bank_model.convert_legacy_bank(self.source), self.expected)

    def test_conversion_and_output_refusals_are_not_cached(self):
        for refused in (ValueError("injected parser refusal"), b"bad"):
            with self.subTest(refusal=refused):
                bank_model._convert_reviewed_bank.cache_clear()
                with (
                    seals(self.source, self.expected),
                    patch.object(
                        bank_model,
                        "_convert_supported_layout",
                        side_effect=[refused, self.expected],
                    ) as convert,
                ):
                    with self.assertRaises(ValueError):
                        bank_model.convert_legacy_bank(self.source)
                    self.assertEqual(bank_model.convert_legacy_bank(self.source), self.expected)
                    self.assertEqual(convert.call_count, 2)

    def test_success_cache_is_bounded_and_evicted_bytes_reconvert(self):
        with patch.object(
            bank_model, "_convert_supported_layout", wraps=bank_model._convert_supported_layout
        ) as convert:
            for digit in b"012345678":
                source = self.source.replace(b"checker.dds", b"checke" + bytes([digit]) + b".dds")
                expected = self.expected.replace(
                    b"checker.dds", b"checke" + bytes([digit]) + b".dds"
                )
                with seals(source, expected):
                    self.assertEqual(bank_model.convert_legacy_bank(source), expected)
            self.assertEqual(bank_model._convert_reviewed_bank.cache_info().currsize, 8)
            first_source = self.source.replace(b"checker.dds", b"checke0.dds")
            first_expected = self.expected.replace(b"checker.dds", b"checke0.dds")
            with seals(first_source, first_expected):
                self.assertEqual(bank_model.convert_legacy_bank(first_source), first_expected)
            self.assertEqual(convert.call_count, 10)

    def test_recipe_context_and_provider_checks_remain_fresh_after_conversion_hit(self):
        fixture = recipe_fixtures.SyntheticRecipeServiceTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bank_path = fixture.source / "UserMaps/Synthetic/Warehouse/invented_bank.nif"
        bank_path.write_bytes(self.source)
        recipe = replace(
            fixture._recipe(),
            assets_sha256=assets_manifest_hash(fixture.source),
            legacy_bank_path="UserMaps/Synthetic/Warehouse/invented_bank.nif",
        )
        with (
            seals(self.source, self.expected),
            patch.object(
                bank_model, "_convert_supported_layout", wraps=bank_model._convert_supported_layout
            ) as convert,
        ):
            rules, before = fixture.resolve(recipe)
            self.assertEqual(rules[0].patches[-1].new, self.expected)
            info = bank_path.stat()
            os.utime(bank_path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            _, metadata = fixture.resolve(recipe)
            self.assertNotEqual(before.source_files_sha256, metadata.source_files_sha256)
            stock = fixture.stock / "RRT_Depots.xml"
            info = stock.stat()
            os.utime(stock, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            _, stock_metadata = fixture.resolve(recipe)
            self.assertNotEqual(metadata.stock_files_sha256, stock_metadata.stock_files_sha256)
            self.assertEqual(convert.call_count, 1)
            duplicate = fixture.stock / bank_path.name
            duplicate.write_bytes(self.source)
            with self.assertRaisesRegex(RuleError, "missing, ambiguous"):
                fixture.resolve(recipe)
            duplicate.unlink()
            stock.write_bytes(b"<changed-stock/>")
            with self.assertRaisesRegex(RuleError, "missing, ambiguous"):
                fixture.resolve(recipe)
            self.assertEqual(convert.call_count, 1)
