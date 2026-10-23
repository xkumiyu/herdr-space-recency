"""Run against a disposable Herdr session; never targets the user's server."""

import json
import fcntl
import os
import shlex
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_ID = "herdr-space-recency"


def wait_for(check, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {check}")


def smoke():
    with tempfile.TemporaryDirectory(prefix="herdr-recency-") as temporary:
        base = Path(temporary)
        subprocess.run(["git", "init", "-b", "main", str(base)], check=True, capture_output=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith("HERDR_")}
        env.update(HERDR_CONFIG_PATH=str(base / "config.toml"), XDG_CONFIG_HOME=str(base / "config"),
                   XDG_STATE_HOME=str(base / "state"))
        initial_config = '[terminal]\ndefault_shell = "/bin/sh"\nshell_mode = "non_login"\n'
        original_config = initial_config + "\n" + (ROOT / "sidebar.toml").read_text()
        (base / "config.toml").write_text(initial_config)
        command = ["herdr", "--session", f"recency-smoke-{os.getpid()}"]

        def cli(*args, check=True):
            return subprocess.run([*command, *args], env=env, cwd=base, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=check)

        def api(*args):
            return json.loads(cli(*args).stdout)["result"]

        def spaces():
            return {ws["workspace_id"]: ws for ws in api("workspace", "list")["workspaces"]}

        def token(workspace_id, name):
            return spaces()[workspace_id].get("tokens", {}).get(name)

        def action(name):
            result = api("plugin", "action", "invoke", f"{PLUGIN_ID}.{name}")
            log_id = result["log"]["log_id"]

            def finished():
                logs = api("plugin", "log", "list", "--plugin", PLUGIN_ID, "--limit", "20")["logs"]
                for entry in logs:
                    if entry["log_id"] == log_id and entry["status"] != "running":
                        assert entry.get("exit_code") == 0, entry
                        return entry
                return None

            return wait_for(finished)

        with (base / "server.log").open("w") as output:
            server = subprocess.Popen([*command, "server"], env=env, cwd=base, stdout=output, stderr=output)
            try:
                wait_for(lambda: cli("workspace", "list", check=False).returncode == 0)
                cli("plugin", "link", str(ROOT))
                config_dir = Path(cli("plugin", "config-dir", PLUGIN_ID).stdout.strip())
                config_dir.mkdir(parents=True, exist_ok=True)
                (config_dir / "config.toml").write_text(
                    "fade_after_hours = 0.0006\ndim_after_hours = 0.0012\nrefresh_seconds = 1\n"
                )
                active_id = api("workspace", "create", "--cwd", str(base), "--label", "active", "--focus")["workspace"]["workspace_id"]
                created = api("workspace", "create", "--cwd", str(base), "--label", "old", "--no-focus")
                old_id = created["workspace"]["workspace_id"]
                old_pane = created["root_pane"]["pane_id"]

                action("setup")
                assert (base / "config.toml").read_text() == original_config
                assert (base / "config.toml.before-space-recency-setup").read_text() == initial_config
                action("setup")  # Repeating setup must preserve config and history.
                wait_for(lambda: token(old_id, "recency_fresh") == "old")
                assert token(old_id, "recency_branch_fresh") == "main"
                # Creating a Space must not wait for the session file or the
                # ordinary refresh interval before publishing its branch.
                late = api("workspace", "create", "--cwd", str(base), "--label", "new-now", "--no-focus")
                late_id = late["workspace"]["workspace_id"]
                wait_for(lambda: token(late_id, "recency_branch_fresh") == "main", seconds=3)
                cli("workspace", "close", late_id)
                wait_for(lambda: token(old_id, "recency_faded") == "old")
                assert token(old_id, "recency_branch_faded") == "main"
                wait_for(lambda: token(old_id, "recency_stale") == "old")
                assert token(old_id, "recency_branch_stale") == "main"
                cli("workspace", "focus", old_id)
                wait_for(lambda: token(old_id, "recency_fresh") == "old")
                cli("workspace", "rename", old_id, "renamed")
                wait_for(lambda: token(old_id, "recency_fresh") == "renamed")
                cli("workspace", "focus", active_id)
                action("start")  # Starting twice must leave one daemon running.
                wait_for(lambda: token(old_id, "recency_stale") == "renamed")
                action("stop")
                assert token(old_id, "recency_fresh") == "renamed"
                action("start")
                wait_for(lambda: token(old_id, "recency_stale") == "renamed")  # persisted age
                (config_dir / "config.toml").write_text(
                    'fade_after_hours = 100\ndim_after_hours = 200\nrefresh_seconds = 1\n'
                    'faded_color = "#B0B0B0"\ndim_color = "#505050"\n'
                )
                wait_for(lambda: token(old_id, "recency_fresh") == "renamed")
                styled_rows = tomllib.loads((base / "config.toml").read_text())["ui"]["sidebar"]["spaces"]["rows"]
                assert styled_rows[0][2]["fg"] == styled_rows[1][1]["fg"] == "#B0B0B0"
                assert styled_rows[0][3]["fg"] == styled_rows[1][2]["fg"] == "#505050"
                with (config_dir / "config.toml").open("a") as stream:
                    stream.write('branch_color = "#7F91A8"\nbranch_faded_color = "#64748A"\nbranch_dim_color = "#485568"\n')
                def branch_colors_applied():
                    rows = tomllib.loads((base / "config.toml").read_text())["ui"]["sidebar"]["spaces"]["rows"]
                    return [item["fg"] for item in rows[1] if isinstance(item, dict)] == ["#7F91A8", "#64748A", "#485568"]
                wait_for(branch_colors_applied)
                (config_dir / "config.toml").write_text(
                    "fade_after_hours = 0.0006\ndim_after_hours = 0.0012\nrefresh_seconds = 1\n"
                )
                wait_for(lambda: token(old_id, "recency_stale") == "renamed")
                subprocess.run(["git", "-C", str(base), "symbolic-ref", "HEAD", "refs/heads/topic"], check=True)
                wait_for(lambda: token(old_id, "recency_branch_stale") == "topic")
                other = base / "other-repository"
                subprocess.run(["git", "init", "-b", "other", str(other)], check=True, capture_output=True)
                shell_command = f"cd {shlex.quote(str(other))} && printf '\\033]7;file://localhost%s\\007' \"$PWD\""
                cli("pane", "run", old_pane, shell_command)
                wait_for(lambda: token(old_id, "recency_branch_stale") == "other")
                cli("server", "stop")
                server.wait(timeout=10)
                server = subprocess.Popen([*command, "server"], env=env, cwd=base, stdout=output, stderr=output)
                wait_for(lambda: cli("workspace", "list", check=False).returncode == 0)
                wait_for(lambda: token(old_id, "recency_stale") == "renamed")
                cli("plugin", "disable", PLUGIN_ID)
                wait_for(lambda: token(old_id, "recency_fresh") == "renamed")
                cli("plugin", "enable", PLUGIN_ID)
                action("start")
                wait_for(lambda: token(old_id, "recency_stale") == "renamed")
                cli("workspace", "close", old_id)
                wait_for(lambda: old_id not in spaces())
                action("stop")
                assert (base / "config.toml").read_text() == original_config
                print("PASS: name/branch fading, branch change, color reload, focus, persistence, server restart, disable, close, singleton, stop; other Herdr settings unchanged")
            except BaseException:
                print((base / "server.log").read_text()[-4000:])
                print("Workspace snapshot:", spaces())
                for log in base.rglob("daemon.log"):
                    print(log.read_text()[-4000:])
                for saved in base.rglob("last-used.json"):
                    print("Recorded timestamps:", saved.read_text(), "now:", time.time())
                raise
            finally:
                # A detached daemon outlives even a crashed test server.
                locks = list(base.rglob("daemon.lock"))
                for lock in locks:
                    (lock.parent / "stop").touch()

                def daemons_stopped():
                    for path in locks:
                        with path.open("a") as lock:
                            try:
                                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except BlockingIOError:
                                return False
                    return True

                try:
                    wait_for(daemons_stopped)
                finally:
                    if server.poll() is None:
                        try:
                            cli("server", "stop", check=False)
                            server.wait(timeout=10)
                        except (subprocess.SubprocessError, OSError):
                            server.terminate()
                            server.wait(timeout=5)


if __name__ == "__main__":
    smoke()
