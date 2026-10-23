"""Space-name/branch presentation; only the marked sidebar block is managed."""

import os
import json
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

START = "# >>> herdr-space-recency sidebar"
END = "# <<< herdr-space-recency sidebar"


def render_sidebar(settings):
    template = Path(__file__).with_name("sidebar.toml").read_text()
    colors = {
        "$recency_faded": settings.faded_color,
        "$recency_stale": settings.dim_color,
        "$recency_branch_faded": settings.branch_faded_color or settings.faded_color,
        "$recency_branch_stale": settings.branch_dim_color or settings.dim_color,
    }
    rendered = re.sub(r'\{ token = "([^"]+)", fg = "#[0-9A-Fa-f]{6}" \}',
                      lambda match: f'{{ token = "{match[1]}", fg = "{colors[match[1]]}" }}', template)
    if settings.branch_color:
        rendered = rendered.replace('"$recency_branch_fresh"',
                                    f'{{ token = "$recency_branch_fresh", fg = "{settings.branch_color}" }}')
    return rendered


def sync_sidebar(path, settings):
    """Return whether this file has our block; write only when colors differ."""
    path = Path(path).resolve()
    try:
        before = path.read_text()
    except FileNotFoundError:
        return False
    tomllib.loads(before)
    markers = []
    offset = 0
    for line in before.splitlines(keepends=True):
        if line.strip() in (START, END):
            try:
                # An unfinished TOML value means this is data, not our marker.
                tomllib.loads(before[:offset])
            except ValueError:
                pass
            else:
                markers.append((line.strip(), offset, offset + len(line.rstrip("\r\n"))))
        offset += len(line)
    if not markers:
        return False
    if [marker[0] for marker in markers] != [START, END]:
        raise ValueError("Invalid Space Recency sidebar markers; config left unchanged")
    start, end = markers[0][1], markers[1][2]
    after = before[:start] + render_sidebar(settings).rstrip("\n") + before[end:]
    if after == before:
        return True
    write_config(path, before, after, ".before-space-recency-colors")
    return True


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
            raise ValueError("Herdr config changed during color update; retrying later")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def setup_sidebar(path, settings):
    """Install the managed sidebar, preserving unrelated configuration."""
    path = Path(path).resolve()
    if sync_sidebar(path, settings):
        return
    before = path.read_text() if path.exists() else ""
    parsed = tomllib.loads(before)
    headers = []
    offset = 0
    for line in before.splitlines(keepends=True):
        if re.fullmatch(r'\s*\[.+\]\s*(?:#.*)?', line.rstrip("\n")):
            try:
                # A header-looking line inside a multiline string is data.
                tomllib.loads(before[:offset])
                table = tomllib.loads(line)
            except ValueError:
                pass
            else:
                headers.append((offset, table))
        offset += len(line)
    target = {"ui": {"sidebar": {"spaces": {}}}}
    for index, (begin, table) in enumerate(headers):
        if table == target:
            end = headers[index + 1][0] if index + 1 < len(headers) else len(before)
            after = before[:begin] + render_sidebar(settings) + "\n" + before[end:]
            break
    else:
        if "spaces" in parsed.get("ui", {}).get("sidebar", {}):
            raise ValueError("Use a [ui.sidebar.spaces] table before running setup; config left unchanged")
        after = before + ("\n" if before else "") + render_sidebar(settings)
    write_config(path, before, after, ".before-space-recency-setup")


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
    # The public API omits root-pane identity. Follow the saved first tab's root
    # CWD, like Herdr; identity_cwd is only the fallback, not updated on cd.
    try:
        saved = json.loads(Path(session_path).read_text())
        cwds = {}
        for workspace in saved["workspaces"]:
            tab = next(iter(workspace.get("tabs", [])), {})
            pane = tab.get("panes", {}).get(str(tab.get("root_pane")), {})
            cwds[workspace["id"]] = pane.get("cwd") or workspace.get("identity_cwd")
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        cwds = {}
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
        if provenance.get("is_linked_worktree") and provenance.get("repo_key") in parent_repos:
            result.append({**workspace, "branch": None})
            continue
        cwd = cwds.get(workspace["workspace_id"]) or (workspace.get("worktree") or {}).get("checkout_path")
        if cwd not in branches:
            branches[cwd] = branch_name(cwd)
        result.append({**workspace, "branch": branches[cwd]})
    return result
