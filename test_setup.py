import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from presentation import setup_sidebar, render_sidebar
from plugin import setup
from presentation import write_config


class SetupTests(unittest.TestCase):
    def test_failed_reload_restores_config_and_stops_only_new_daemon(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            original = '[terminal]\ndefault_shell = "/bin/sh"\n'
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
            setup_sidebar(path)
            self.assertEqual(path.read_text(), render_sidebar())
            modified = path.stat().st_mtime_ns
            setup_sidebar(path)
            self.assertEqual(path.stat().st_mtime_ns, modified)

    def test_existing_spaces_are_never_changed_or_reloaded(self):
        contents = [
            '[ui.sidebar.spaces] # custom\nrows = [["workspace"]]\n',
            render_sidebar().replace("#A0A0A0", "#123456"),
            "# >>> herdr-space-recency sidebar\n" + render_sidebar()
            + "# <<< herdr-space-recency sidebar\n",
            'ui.sidebar.spaces.rows = [["workspace"]]\n',
            'ui = { sidebar = { spaces = { rows = [] } } }\n',
            '[ui.sidebar.spaces]\n',
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            for original in contents:
                with self.subTest(original=original):
                    path.write_text(original)
                    modified = path.stat().st_mtime_ns
                    context = SimpleNamespace(herdr_config=path, config=path.with_name("plugin.toml"),
                                              running=Mock(return_value=True), rpc=Mock())
                    with patch("plugin.start"), patch("builtins.print") as output:
                        setup(context)
                    self.assertEqual(path.read_text(), original)
                    self.assertEqual(path.stat().st_mtime_ns, modified)
                    context.rpc.assert_not_called()
                    self.assertIn("Existing", str(output.call_args_list))
                    self.assertFalse(path.with_name(path.name + ".before-space-recency-setup").exists())

    def test_insertion_preserves_other_settings_symlink_permissions_and_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            original = '[terminal]\ndefault_shell = "/bin/sh"\n\n[ui.sidebar.agents]\nrows = [["agent"]]\n'
            path.write_text(original)
            path.chmod(0o600)
            link = path.with_name("link.toml")
            link.symlink_to(path)
            self.assertTrue(setup_sidebar(link))
            self.assertEqual(path.read_text(), original + "\n" + render_sidebar())
            backup = path.with_name(path.name + ".before-space-recency-setup")
            self.assertEqual(backup.read_text(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(link.is_symlink())
            self.assertFalse(setup_sidebar(link))
            self.assertEqual(backup.read_text(), original)

    def test_first_setup_reloads_after_insertion(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            context = SimpleNamespace(herdr_config=path, config=path.with_name("plugin.toml"),
                                      running=Mock(return_value=False), rpc=Mock())
            with patch("plugin.start"):
                setup(context)
            context.rpc.assert_called_once_with("server.reload_config")
            self.assertEqual(path.read_text(), render_sidebar())

    def test_header_inside_multiline_string_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            original = 'note = """\n[ui.sidebar.spaces]\nnot a table\n"""\n'
            path.write_text(original)
            setup_sidebar(path)
            self.assertEqual(tomllib.loads(path.read_text())["note"], tomllib.loads(original)["note"])

    def test_markers_inside_strings_are_preserved_during_setup(self):
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
                    for _ in range(2):
                        setup_sidebar(path)
                        parsed = tomllib.loads(path.read_text())
                        self.assertEqual(parsed["note"], tomllib.loads(original)["note"])
                        self.assertEqual(parsed["ui"]["sidebar"]["spaces"],
                                         tomllib.loads(render_sidebar())["ui"]["sidebar"]["spaces"])

    def test_invalid_config_or_template_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text("invalid = [")
            with self.assertRaises(ValueError):
                setup_sidebar(path)
            self.assertEqual(path.read_text(), "invalid = [")
            path.write_text('[terminal]\ndefault_shell = "/bin/sh"\n')
            original = path.read_text()
            with patch("presentation.render_sidebar", return_value="invalid = ["):
                with self.assertRaises(ValueError):
                    setup_sidebar(path)
            self.assertEqual(path.read_text(), original)

    def test_concurrent_edit_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('note = "original"\n')
            original = path.read_text()
            path.write_text('note = "concurrent"\n')
            with self.assertRaises(ValueError):
                write_config(path, original, original + render_sidebar(), ".backup")
            self.assertEqual(path.read_text(), 'note = "concurrent"\n')


if __name__ == "__main__":
    unittest.main()
