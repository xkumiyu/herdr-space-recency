import subprocess
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from presentation import branch_name, render_sidebar, workspace_branches
from recency import Settings, TOKENS, Tracker


class PresentationTests(unittest.TestCase):
    def test_template_has_all_state_tokens_and_default_colors(self):
        rows = tomllib.loads(render_sidebar())["ui"]["sidebar"]["spaces"]["rows"]
        tokens = {item["token"] if isinstance(item, dict) else item for row in rows for item in row}
        self.assertTrue({"$" + token for token in TOKENS} <= tokens)
        self.assertTrue({"state_icon", "git_status"} <= tokens)
        for row in rows:
            self.assertEqual([item["fg"] for item in row if isinstance(item, dict)],
                             ["#A0A0A0", "#606060"])

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

    def test_grouped_worktree_name_matches_native_and_preserves_custom_names(self):
        from unittest.mock import patch
        from plugin import normalize
        from types import SimpleNamespace
        from unittest.mock import Mock
        child = {"workspace_id": "w1", "label": "checkout", "worktree": {
            "repo_key": "/repo/.git", "checkout_path": "/repo/checkout", "is_linked_worktree": True,
        }}
        parent = {"workspace_id": "w0", "label": "repo", "worktree": {
            "repo_key": "/repo/.git", "checkout_path": "/repo", "is_linked_worktree": False,
        }}
        cases = [
            (None, "checkout", "worktree/feature", "feature"),
            (None, "checkout", "topic/feature", "topic/feature"),
            ("review", "review", "worktree/feature", "review"),
            ("checkout", "checkout", "worktree/feature", "checkout"),
            (None, "just-renamed", "worktree/feature", "just-renamed"),
            (None, "checkout", None, "checkout"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.json"
            for custom_name, label, branch, expected in cases:
                with self.subTest(custom_name=custom_name, label=label, branch=branch):
                    session.write_text(json.dumps({"workspaces": [{
                        "id": "w1", "custom_name": custom_name, "identity_cwd": "/repo/checkout",
                    }]}))
                    with patch("presentation.branch_name", return_value=branch):
                        result = workspace_branches([{**child, "label": label}, parent], session)
                    self.assertEqual(result[0]["label"], expected)
                    self.assertEqual(child["label"], "checkout")  # No change to the source workspace.
                    self.assertIsNone(result[0]["branch"])
                    for hours, tier in [(0, "fresh"), (24, "faded"), (168, "stale")]:
                        tokens = Tracker({"w1": 100}).update(result, 100 + hours * 3600, Settings())["w1"]
                        self.assertEqual(tokens["recency_" + tier], expected)
                        self.assertTrue(all(tokens["recency_branch_" + t] is None
                                            for t in ("fresh", "faded", "stale")))
                    context = SimpleNamespace(workspaces=lambda: result, report=Mock())
                    normalize(context)
                    self.assertEqual(context.report.call_args_list[0].args[1]["recency_fresh"], expected)

    def test_unconfirmed_custom_name_and_ungrouped_worktree_keep_api_label(self):
        from unittest.mock import patch
        child = {"workspace_id": "w1", "label": "checkout", "worktree": {
            "repo_key": "/repo/.git", "checkout_path": "/repo/checkout", "is_linked_worktree": True,
        }}
        parent = {"workspace_id": "w0", "label": "repo", "worktree": {
            "repo_key": "/repo/.git", "checkout_path": "/repo", "is_linked_worktree": False,
        }}
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.json"
            for contents in (None, {"workspaces": []}, {"workspaces": [{"id": "w1"}]}):
                if contents is not None:
                    session.write_text(json.dumps(contents))
                with patch("presentation.branch_name", return_value="worktree/feature"):
                    result = workspace_branches([child, parent], session)
                self.assertEqual(result[0]["label"], "checkout")
            session.write_text(json.dumps({"workspaces": [{"id": "w1", "custom_name": None}]}))
            with patch("presentation.branch_name", return_value="worktree/feature"):
                result = workspace_branches([child], session)
            self.assertEqual(result[0]["label"], "checkout")
            self.assertEqual(result[0]["branch"], "worktree/feature")

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
