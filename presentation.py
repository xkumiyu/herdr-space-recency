"""Space-name/branch presentation and one-time sidebar installation."""

import os
import json
import subprocess
import tempfile
import tomllib
from pathlib import Path

def render_sidebar():
    return Path(__file__).with_name("sidebar.toml").read_text()


def write_config(path, before, after, backup_suffix):
    """Validate, back up, and atomically replace a configuration file."""
    tomllib.loads(after)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    mode = path.stat().st_mode & 0o777 if exists else 0o600
    # Keep one pre-update copy. Further changes never overwrite the backup.
    if exists:
        backup = path.with_name(path.name + backup_suffix)
        try:
            with backup.open("x") as stream:
                os.chmod(backup, mode)
                stream.write(before)
        except FileExistsError:
            pass
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, mode)
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() != exists or (exists and path.read_text() != before):
            raise ValueError("Herdr config changed during setup; run setup again")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def setup_sidebar(path):
    """Insert defaults only when Spaces settings are absent; return whether inserted."""
    path = Path(path).resolve()
    before = path.read_text() if path.exists() else ""
    parsed = tomllib.loads(before)
    if "spaces" in parsed.get("ui", {}).get("sidebar", {}):
        return False
    after = before + ("\n" if before else "") + render_sidebar()
    write_config(path, before, after, ".before-space-recency-setup")
    return True


def branch_name(cwd):
    """Match Herdr's branch token: symbolic branch only, empty when detached."""
    if not cwd:
        return None
    try:
        result = subprocess.run(["git", "-C", str(cwd), "symbolic-ref", "--quiet", "--short", "HEAD"],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def workspace_branches(workspaces, session_path, *, tabs=(), panes=()):
    """Resolve sidebar names and branches without changing workspace identities."""
    # The public API omits root-pane identity. Follow the saved first tab's root
    # CWD, like Herdr; identity_cwd is only the fallback, not updated on cd.
    try:
        saved = json.loads(Path(session_path).read_text())
        cwds = {}
        custom_names = {workspace["id"]: workspace["custom_name"]
                        for workspace in saved["workspaces"] if "custom_name" in workspace}
        for workspace in saved["workspaces"]:
            tab = next(iter(workspace.get("tabs", [])), {})
            pane = tab.get("panes", {}).get(str(tab.get("root_pane")), {})
            cwds[workspace["id"]] = pane.get("cwd") or workspace.get("identity_cwd")
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        cwds = {}
        custom_names = {}
    first_tabs = {}
    for tab in tabs:
        first_tabs.setdefault(tab["workspace_id"], tab["tab_id"])
    live_cwds = {}
    for pane in panes:
        if pane.get("tab_id") == first_tabs.get(pane["workspace_id"]):
            live_cwds.setdefault(pane["workspace_id"], set()).add(pane.get("cwd"))
    # A sole pane (or all panes in the same cwd) identifies the root exactly.
    # Mixed-cwd splits still need the saved root identity; never guess by focus.
    for workspace_id, candidates in live_cwds.items():
        if len(candidates) == 1 and (cwd := next(iter(candidates))):
            cwds[workspace_id] = cwd
    branches = {}
    result = []
    parent_repos = {
        provenance["repo_key"] for workspace in workspaces
        if (provenance := workspace.get("worktree"))
        and provenance.get("repo_key") and not provenance.get("is_linked_worktree")
    }
    for workspace in workspaces:
        provenance = workspace.get("worktree") or {}
        grouped = provenance.get("is_linked_worktree") and provenance.get("repo_key") in parent_repos
        cwd = cwds.get(workspace["workspace_id"]) or (workspace.get("worktree") or {}).get("checkout_path")
        if cwd not in branches:
            branches[cwd] = branch_name(cwd)
        branch = branches[cwd]
        label = workspace["label"]
        # Confirm saved automatic naming and keep differing live names during save lag.
        # ponytail: custom-name flags can lag; use a live API flag if Herdr exposes one.
        if (grouped and workspace["workspace_id"] in custom_names
                and custom_names[workspace["workspace_id"]] is None
                and label == Path(provenance["checkout_path"]).name and branch is not None):
            label = branch.removeprefix("worktree/")
        result.append({**workspace, "label": label, "branch": None if grouped else branch})
    return result
