import subprocess
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from presentation import branch_name, render_sidebar, sync_sidebar, workspace_branches
from recency import Settings


class PresentationTests(unittest.TestCase):
    def test_branch_colors_are_independent_and_optional(self):
        settings = Settings(branch_color="#7F91A8", branch_faded_color="#64748A", branch_dim_color="#485568")
        rows = tomllib.loads(render_sidebar(settings))["ui"]["sidebar"]["spaces"]["rows"]
        self.assertEqual([item["fg"] for item in rows[1] if isinstance(item, dict)],
                         ["#7F91A8", "#64748A", "#485568"])
        self.assertEqual([item["fg"] for item in rows[0] if isinstance(item, dict)],
                         ["#A0A0A0", "#606060"])
        for field in ("branch_color", "branch_faded_color", "branch_dim_color"):
            with self.assertRaises(ValueError):
                Settings(**{field: "red"})
        self.assertIn('"$recency_branch_fresh"', render_sidebar(Settings()))

    def test_colors_update_names_and_branches_without_other_config_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            prefix = '[theme]\nname = "terminal"\n\n# user comment\n'
            suffix = '\n[ui.sidebar.agents]\nrows = [["agent"]]\n'
            path.write_text(prefix + render_sidebar(Settings()) + suffix)
            settings = Settings(faded_color="#606060", dim_color="#445566")
            self.assertTrue(sync_sidebar(path, settings))
            contents = path.read_text()
            self.assertTrue(contents.startswith(prefix))
            self.assertTrue(contents.endswith(suffix))
            rows = tomllib.loads(contents)["ui"]["sidebar"]["spaces"]["rows"]
            for row in rows:
                styles = [item for item in row if isinstance(item, dict)]
                self.assertEqual([item["fg"] for item in styles], ["#606060", "#445566"])
            modified = path.stat().st_mtime_ns
            sync_sidebar(path, settings)
            self.assertEqual(path.stat().st_mtime_ns, modified)

    def test_unmanaged_and_invalid_config_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            for content in ['[ui]\naccent = "cyan"\n', '[ui]\ninvalid = [\n']:
                path.write_text(content)
                if "invalid" in content:
                    with self.assertRaises(ValueError):
                        sync_sidebar(path, Settings())
                else:
                    self.assertFalse(sync_sidebar(path, Settings()))
                self.assertEqual(path.read_text(), content)

    def test_color_sync_keeps_symlink_and_private_file_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "actual.toml"
            target.write_text(render_sidebar(Settings()))
            target.chmod(0o600)
            link = root / "config.toml"
            link.symlink_to(target)
            sync_sidebar(link, Settings(faded_color="#BBBBBB"))
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / "actual.toml.before-space-recency-colors").stat().st_mode & 0o777, 0o600)

    def test_branch_from_git_and_non_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(branch_name(root))
            subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
            self.assertEqual(branch_name(root), "main")
            nested = root / "nested"
            nested.mkdir()
            self.assertEqual(branch_name(nested), "main")
            subprocess.run(["git", "-C", str(root), "symbolic-ref", "HEAD", "refs/heads/next"], check=True)
            self.assertEqual(branch_name(root), "next")

    def test_branch_uses_saved_workspace_identity_not_active_pane(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.json"
            session.write_text(json.dumps({"workspaces": [{"id": "w1", "identity_cwd": "/identity"}]}))
            workspace = {"workspace_id": "w1", "label": "repo", "worktree": {"checkout_path": "/other"}}
            with patch("presentation.branch_name", return_value="topic") as git:
                result = workspace_branches([workspace], session)
        self.assertEqual(result[0]["branch"], "topic")
        git.assert_called_once_with("/identity")

    def test_missing_session_and_null_worktree_hide_branch_without_crashing(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = {"workspace_id": "w1", "label": "home", "worktree": None}
            result = workspace_branches([workspace], Path(temporary) / "missing.json")
            self.assertIsNone(result[0]["branch"])

    def test_branch_uses_root_panes_current_cwd_after_cd(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.json"
            session.write_text(json.dumps({"workspaces": [{
                "id": "w1", "identity_cwd": "/initial", "tabs": [{
                    "root_pane": 7, "panes": {"7": {"cwd": "/current"}, "9": {"cwd": "/other"}},
                }],
            }]}))
            with patch("presentation.branch_name", return_value="current") as git:
                workspace_branches([{"workspace_id": "w1", "label": "repo"}], session)
            git.assert_called_once_with("/current")

    def test_linked_worktree_omits_redundant_branch_like_native_sidebar(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            workspace = {
                "workspace_id": "w1", "label": "feature-example",
                "worktree": {"repo_key": "/repo/.git", "checkout_path": "/repo/.worktrees/feature-example", "is_linked_worktree": True},
            }
            parent = {"workspace_id": "w0", "label": "repo", "worktree": {
                "repo_key": "/repo/.git", "checkout_path": "/repo", "is_linked_worktree": False,
            }}
            with patch("presentation.branch_name", return_value="feature/report-drive-sync"):
                result = workspace_branches([workspace, parent], Path(temporary) / "missing.json")
            self.assertIsNone(result[0]["branch"])
            self.assertIsNotNone(result[1]["branch"])

    def test_linked_worktree_without_open_parent_keeps_branch(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            workspace = {"workspace_id": "w1", "label": "review", "worktree": {
                "repo_key": "/repo/.git", "checkout_path": "/repo/worktree", "is_linked_worktree": True,
            }}
            with patch("presentation.branch_name", return_value="feature/review"):
                result = workspace_branches([workspace], Path(temporary) / "missing.json")
            self.assertEqual(result[0]["branch"], "feature/review")

    def test_new_space_uses_live_root_cwd_before_session_is_saved(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            workspaces = [{"workspace_id": "w1", "label": "new", "worktree": None}]
            tabs = [{"workspace_id": "w1", "tab_id": "first"}, {"workspace_id": "w1", "tab_id": "active"}]
            panes = [
                {"workspace_id": "w1", "tab_id": "first", "cwd": "/root"},
                {"workspace_id": "w1", "tab_id": "active", "cwd": "/other"},
            ]
            with patch("presentation.branch_name", return_value="main") as git:
                result = workspace_branches(workspaces, Path(temporary) / "missing.json", tabs=tabs, panes=panes)
            self.assertEqual(result[0]["branch"], "main")
            git.assert_called_once_with("/root")

    def test_live_cwd_takes_precedence_over_stale_saved_cwd(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.json"
            session.write_text(json.dumps({"workspaces": [{"id": "w1", "identity_cwd": "/old"}]}))
            tabs = [{"workspace_id": "w1", "tab_id": "first"}]
            panes = [{"workspace_id": "w1", "tab_id": "first", "cwd": "/new"}]
            with patch("presentation.branch_name", return_value="new") as git:
                workspace_branches([{"workspace_id": "w1", "label": "repo"}], session, tabs=tabs, panes=panes)
            git.assert_called_once_with("/new")

    def test_ambiguous_split_uses_saved_root_instead_of_guessing(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.json"
            session.write_text(json.dumps({"workspaces": [{"id": "w1", "identity_cwd": "/root"}]}))
            tabs = [{"workspace_id": "w1", "tab_id": "first"}]
            panes = [{"workspace_id": "w1", "tab_id": "first", "cwd": cwd} for cwd in ("/root", "/other")]
            with patch("presentation.branch_name", return_value="main") as git:
                workspace_branches([{"workspace_id": "w1", "label": "repo"}], session, tabs=tabs, panes=panes)
            git.assert_called_once_with("/root")


if __name__ == "__main__":
    unittest.main()
