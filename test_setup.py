import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from presentation import setup_sidebar, render_sidebar
from plugin import setup
from recency import Settings


class SetupTests(unittest.TestCase):
    def test_failed_reload_restores_config_and_stops_only_new_daemon(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            original = '[ui.sidebar.spaces]\nrows = [["workspace"]]\n'
            for running in (False, True):
                with self.subTest(running=running):
                    path.write_text(original)
                    context = SimpleNamespace(herdr_config=path, config=path.with_name("plugin.toml"),
                                              running=Mock(return_value=running),
                                              rpc=Mock(side_effect=[RuntimeError("reload failed"), {}]))
                    with patch("plugin.start"), patch("plugin.stop") as stop:
                        with self.assertRaisesRegex(RuntimeError, "reload failed"):
                            setup(context)
                        self.assertEqual(stop.call_count, int(not running))
                    self.assertEqual(path.read_text(), original)

    def test_creates_missing_config_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested/config.toml"
            setup_sidebar(path, Settings())
            self.assertEqual(path.read_text(), render_sidebar(Settings()))
            modified = path.stat().st_mtime_ns
            setup_sidebar(path, Settings())
            self.assertEqual(path.stat().st_mtime_ns, modified)

    def test_replaces_spaces_only_and_preserves_original_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            prefix = '[terminal]\ndefault_shell = "/bin/sh"\n\n'
            suffix = '[ui.sidebar.agents]\nrows = [["agent"]]\n'
            original = prefix + '[ui.sidebar.spaces] # custom\nrows = [["workspace"]]\n\n' + suffix
            path.write_text(original)
            path.chmod(0o600)
            link = path.with_name("link.toml")
            link.symlink_to(path)
            setup_sidebar(link, Settings())
            backup = path.with_name("config.toml.before-space-recency-setup")
            self.assertEqual(backup.read_text(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertTrue(link.is_symlink())
            self.assertTrue(path.read_text().startswith(prefix))
            self.assertTrue(path.read_text().endswith(suffix))
            setup_sidebar(link, Settings(faded_color="#123456"))
            self.assertEqual(backup.read_text(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_header_inside_multiline_string_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            original = 'note = """\n[ui.sidebar.spaces]\nnot a table\n"""\n'
            path.write_text(original)
            setup_sidebar(path, Settings())
            self.assertEqual(tomllib.loads(path.read_text())["note"], tomllib.loads(original)["note"])

    def test_markers_inside_strings_are_preserved_during_setup_and_color_sync(self):
        start = "# >>> herdr-space-recency sidebar"
        end = "# <<< herdr-space-recency sidebar"
        for quote in ('"""', "'''", '"', "'"):
            for body in (start, end, f"{start}\nkeep this text\n{end}"):
                if len(quote) == 1 and "\n" in body:
                    continue
                with self.subTest(quote=quote, body=body), tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "config.toml"
                    value = f"\n{body}\n" if len(quote) == 3 else body
                    original = f"note = {quote}{value}{quote}\n"
                    path.write_text(original)
                    for settings in (Settings(), Settings(faded_color="#123456")):
                        setup_sidebar(path, settings)
                        parsed = tomllib.loads(path.read_text())
                        self.assertEqual(parsed["note"], tomllib.loads(original)["note"])
                        self.assertEqual(parsed["ui"]["sidebar"]["spaces"],
                                         tomllib.loads(render_sidebar(settings))["ui"]["sidebar"]["spaces"])

    def test_invalid_unsupported_and_broken_markers_are_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            for original in ('invalid = [', 'ui.sidebar.spaces.rows = [["workspace"]]\n',
                             '# >>> herdr-space-recency sidebar\n'):
                with self.subTest(original=original):
                    path.write_text(original)
                    with self.assertRaises(ValueError):
                        setup_sidebar(path, Settings())
                    self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()
