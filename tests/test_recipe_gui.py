import hashlib
import json
from queue import Queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from smr_launcher import recipe_service
from smr_launcher.application import LauncherApplication
from smr_launcher.gui import LauncherWindow


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RecipeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.recipe_id = "synthetic-recipe-v1"
        self.recipe = recipe_service._IdentityRecipe(
            self.recipe_id, "a" * 64, "b" * 64, "c" * 64,
            "UserMaps/Synthetic/RRT_Scenario.xml", "d" * 64,
            "UserMaps/Synthetic/RRT_Industries.xml", "e" * 64, "f" * 64,
            "1" * 64, "2" * 64, (("Old Mill", 0, "Built-in Factory"),),
        )
        payload = dict(schema=1, recipe="original", archive=self.recipe.archive_sha256,
                       game=self.recipe.game_sha256, assets=self.recipe.assets_sha256)
        self.original_variant = sha(json.dumps(payload, sort_keys=True,
                                               separators=(",", ":")).encode())
        self.choice = recipe_service.RecipeChoice(
            self.recipe_id, "Synthetic compatibility v1",
            "Old Mill becomes Built-in Factory. Experimental; later gameplay still needs testing.",
        )
        self.record = SimpleNamespace(
            archive_sha256=self.recipe.archive_sha256,
            game_executable_sha256=self.recipe.game_sha256,
            variant_id=self.original_variant,
            scenarios=(self.recipe.scenario_path,),
        )
        self.app = SimpleNamespace(
            installation=SimpleNamespace(executable_sha256=self.recipe.game_sha256),
            catalogue=Mock(return_value=(self.record,)),
        )

    def choices(self, record=None, *, catalogue=None, game=None):
        self.app.catalogue.return_value = (self.record,) if catalogue is None else catalogue
        if game is not None:
            self.app.installation.executable_sha256 = game
        with patch.object(recipe_service, "_RECIPES", {self.recipe_id: self.recipe}), \
             patch.object(recipe_service, "_CHOICES", {self.recipe_id: self.choice}):
            return LauncherApplication.compatibility_recipes(
                self.app, self.record if record is None else record)

    def test_only_current_exact_original_matching_archive_game_and_scenario_is_offered(self):
        self.assertEqual(self.choices(), (self.choice,))

        derivative = SimpleNamespace(**{**vars(self.record), "variant_id": "9" * 64})
        wrong_game = SimpleNamespace(**{**vars(self.record), "game_executable_sha256": "8" * 64})
        wrong_scenario = SimpleNamespace(**{**vars(self.record), "scenarios": ("UserMaps/Elsewhere.xml",)})
        stale = SimpleNamespace(**{**vars(self.record), "stale_generation": 1})
        for label, record, catalogue, game in (
            ("derivative", derivative, (derivative,), self.recipe.game_sha256),
            ("wrong game on map", wrong_game, (wrong_game,), self.recipe.game_sha256),
            ("wrong scenario", wrong_scenario, (wrong_scenario,), self.recipe.game_sha256),
            ("stale catalogue row", stale, (self.record,), self.recipe.game_sha256),
            ("wrong installed game", self.record, (self.record,), "7" * 64),
        ):
            with self.subTest(label=label):
                self.assertEqual(self.choices(record, catalogue=catalogue, game=game), ())


class RecipeGuiTests(unittest.TestCase):
    def test_chooser_starts_on_first_title_and_create_uses_selected_recipe(self):
        choices = (
            recipe_service.RecipeChoice("recipe-one", "First reviewed edition", "First alias details."),
            recipe_service.RecipeChoice("recipe-two", "Second reviewed edition", "Second alias details."),
        )
        window = LauncherWindow.__new__(LauncherWindow)
        window.app = Mock()
        window.app.compatibility_recipes.return_value = choices
        window.busy = False
        window._submit = Mock()
        chooser_index = [-1]

        def current(index=None):
            if index is not None:
                chooser_index[0] = index
            return chooser_index[0]

        with patch("smr_launcher.gui.ttk") as ttk, patch("smr_launcher.gui.tk") as tk, \
             patch("smr_launcher.gui.tr", side_effect=lambda value: value):
            chooser = ttk.Combobox.return_value
            chooser.current.side_effect = current
            window._compatibility_tab(Mock(), object(), Mock())

            combo_args, combo_kwargs = ttk.Combobox.call_args
            self.assertNotIn("textvariable", combo_kwargs)
            self.assertEqual(combo_kwargs["values"], (choices[0].title, choices[1].title))
            chooser.current.assert_any_call(0)
            self.assertEqual(chooser_index[0], 0)

            insert = tk.Text.return_value.insert
            self.assertIn("First alias details.", insert.call_args.args[1])

            selection_handler = chooser.bind.call_args.args[1]
            chooser.current(1)
            selection_handler()
            self.assertIn("Second alias details.", insert.call_args.args[1])

            create_button = next(
                call for call in ttk.Button.call_args_list
                if call.kwargs.get("text") == "Create Compatibility Edition"
            )
            create_button.kwargs["command"]()

        _label, operation, _finished = window._submit.call_args.args
        operation()
        window.app.create_recipe_edition.assert_called_once_with(window.app.compatibility_recipes.call_args.args[0],
                                                                 "recipe-two")

    def test_compatibility_tab_discloses_aliases_and_remaining_test_limits(self):
        choice = recipe_service.RecipeChoice(
            "synthetic-recipe-v1", "Synthetic compatibility v1",
            "Old Mill becomes Built-in Factory. Models and production stay in place. "
            "Trains and extended play still need testing. This edition remains experimental.",
        )
        window = LauncherWindow.__new__(LauncherWindow)
        window.app = Mock()
        window.app.compatibility_recipes.return_value = (choice,)
        record, details = object(), Mock()
        with patch("smr_launcher.gui.ttk") as ttk, patch("smr_launcher.gui.tk") as tk, \
             patch("smr_launcher.gui.tr", side_effect=lambda s: s):
            ttk.Combobox.return_value.current.return_value = 0
            window._compatibility_tab(Mock(), record, details)

        displayed = tk.Text.return_value.insert.call_args.args[1]
        self.assertIn("Old Mill", displayed)
        self.assertIn("Built-in Factory", displayed)
        self.assertIn("extended play still need testing", displayed)
        self.assertIn("remains experimental", displayed)
        self.assertIn("separate edition with its own saves", displayed)
        self.assertIn("Your original map and saves are kept", displayed)

    def test_create_recipe_dispatches_named_api_only_when_not_busy(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.app = Mock()
        window.busy = False
        window._submit = Mock()
        record, details = object(), Mock()

        window._create_recipe(record, "synthetic-recipe-v1", details)
        window._submit.assert_called_once()
        label, operation, _finished = window._submit.call_args.args
        self.assertIn("preparing a compatibility edition", label)
        operation()
        window.app.create_recipe_edition.assert_called_once_with(record, "synthetic-recipe-v1")

        window.busy = True
        window._submit.reset_mock()
        window._create_recipe(record, "synthetic-recipe-v1", details)
        window._submit.assert_not_called()

    def test_failed_submission_keeps_details_open_and_success_clears_filters_without_play(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.app = Mock()
        window.busy = False
        window.results = Queue()
        window.status = Mock()
        window.root = Mock()
        window.closed = False
        window.bulk_active = False
        window.close_when_idle = False
        window.speech_process = None
        window._update_download_rows = Mock()
        window._controls = Mock()
        window._log = Mock()
        window._submit = LauncherWindow._submit.__get__(window, LauncherWindow)
        details = Mock()
        details.winfo_exists.return_value = True
        record = object()
        window.search = Mock()
        window.map_filter = Mock()
        window._show = Mock()
        window.status.set.reset_mock()
        window.app.create_recipe_edition.side_effect = RuntimeError("synthetic preparation failure")

        class ImmediateThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()

        with patch("smr_launcher.gui.Thread", ImmediateThread), \
             patch("smr_launcher.gui.messagebox.showerror") as showerror:
            window._create_recipe(record, "synthetic-recipe-v1", details)
            self.assertTrue(window.busy)
            window._poll()

        details.destroy.assert_not_called()
        self.assertIn("synthetic preparation failure", window.status.set.call_args.args[0])
        showerror.assert_called_once()
        window.search.reset_mock()
        window.map_filter.reset_mock()
        window._show.reset_mock()

        window.app.create_recipe_edition.side_effect = None
        edition = SimpleNamespace(profile_id="compiled-profile")
        window.app.create_recipe_edition.return_value = edition
        window.busy = False
        window._submit = Mock()
        window._create_recipe(record, "synthetic-recipe-v1", details)
        _label, operation, finished = window._submit.call_args.args
        self.assertEqual(operation(), edition)
        finished(edition)

        details.destroy.assert_called_once_with()
        self.assertEqual(window.selected_profile, "compiled-profile")
        window.search.set.assert_called_once_with("")
        window.map_filter.set.assert_called_once_with("All maps")
        window._show.assert_called_once_with("maps")
        self.assertIn("gameplay remains unverified", window.status.set.call_args.args[0])
        window.app.play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
