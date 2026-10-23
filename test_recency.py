import tempfile
import unittest
from pathlib import Path

from recency import Settings, Tracker, load_state, save_state


class RecencyTests(unittest.TestCase):
    def test_thresholds_and_custom_hours(self):
        settings = Settings(fade_after_hours=2, dim_after_hours=5)
        for hours, expected in [(0, "fresh"), (1.99, "fresh"), (2, "faded"), (5, "stale")]:
            tracker = Tracker({"w1": 100})
            reports = tracker.update([self.workspace()], 100 + hours * 3600, settings)
            self.assertEqual(reports["w1"][f"recency_{expected}"], "project")
            self.assertEqual(sum(value is not None for value in reports["w1"].values()), 1)

    @staticmethod
    def workspace(**changes):
        return dict(workspace_id="w1", label="project", focused=False, agent_status="idle", **changes)

    def test_active_and_attention_spaces_stay_fresh(self):
        for status in ["working", "blocked", "done"]:
            ws = self.workspace()
            ws["agent_status"] = status
            result = Tracker({"w1": 0}).update([ws], 1_000_000, Settings())
            self.assertEqual(result["w1"]["recency_fresh"], "project")
        ws = self.workspace()
        ws["focused"] = True
        tracker = Tracker({"w1": 0})
        self.assertEqual(tracker.update([ws], 1_000_000, Settings())["w1"]["recency_fresh"], "project")
        self.assertEqual(tracker.last_used["w1"], 1_000_000)

    def test_focus_events_and_focus_out_reset_age(self):
        tracker = Tracker({"w1": 0})
        tracker.update([self.workspace()], 1_000_000, Settings(), focused_event="w1")
        self.assertEqual(tracker.last_used["w1"], 1_000_000)
        ws = self.workspace()
        ws["focused"] = True
        tracker.update([ws], 1_000_100, Settings())
        tracker.update([self.workspace()], 1_000_200, Settings())
        self.assertEqual(tracker.last_used["w1"], 1_000_200)

    def test_new_closed_renamed_and_clock_rollback(self):
        tracker = Tracker({"gone": 1, "w1": 1000})
        reports = tracker.update([self.workspace(), dict(workspace_id="w2", label="new")], 100, Settings())
        self.assertNotIn("gone", tracker.last_used)
        self.assertEqual(tracker.last_used["w2"], 100)
        self.assertEqual(reports["w1"]["recency_fresh"], "project")
        ws = self.workspace()
        ws["label"] = "renamed"
        self.assertEqual(tracker.update([ws], 100, Settings())["w1"]["recency_fresh"], "renamed")

    def test_persistence_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(load_state(path), {})
            save_state(path, {"w1": 123.5})
            self.assertEqual(load_state(path), {"w1": 123.5})
            for bad in ['{', '[]', '{"w1": -1}', '{"w1": "yesterday"}', '{"w1": NaN}']:
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    load_state(path)
                self.assertEqual(path.read_text(), bad)

    def test_config_validation_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            self.assertEqual(Settings.load(path).fade_after_hours, 24)
            path.write_text("fade_after_hours = 0.5\ndim_after_hours = 2\nrefresh_seconds = 5\n")
            self.assertEqual(Settings.load(path).dim_after_hours, 2)
            for bad in ["fade_after_hours = -1", "fade_after_hours = 200", "dim_after_hours = inf", "refresh_seconds = 0", "fade_after_hours = true", "fade_after_hour = 5"]:
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    Settings.load(path)

    def test_configurable_colors_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('faded_color = "#abcdef"\ndim_color = "#123456"\n')
            settings = Settings.load(path)
            self.assertEqual(settings.faded_color, "#abcdef")
            self.assertEqual(settings.dim_color, "#123456")
            for bad in ['"red"', '"#xyzxyz"', '"#12345"', 'true', '42']:
                path.write_text(f"faded_color = {bad}\n")
                with self.assertRaises(ValueError):
                    Settings.load(path)

    def test_branch_follows_name_tier_and_clears_when_missing(self):
        workspace = self.workspace()
        workspace["branch"] = "main"
        for hours, expected in [(0, "fresh"), (24, "faded"), (168, "stale")]:
            tracker = Tracker({"w1": 100})
            tokens = tracker.update([workspace], 100 + hours * 3600, Settings())["w1"]
            self.assertEqual(tokens[f"recency_branch_{expected}"], "main")
            self.assertEqual(tokens[f"recency_{expected}"], "project")
            self.assertEqual(sum(value is not None for value in tokens.values()), 2)
        del workspace["branch"]
        tokens = tracker.update([workspace], 100 + 200 * 3600, Settings())["w1"]
        self.assertTrue(all(tokens[f"recency_branch_{tier}"] is None for tier in ("fresh", "faded", "stale")))


if __name__ == "__main__":
    unittest.main()
