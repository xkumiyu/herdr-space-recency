#!/usr/bin/env python3
"""Unix-only Herdr plugin runtime. Python 3.11+, standard library only."""

import argparse
import fcntl
import hashlib
import json
import os
import select
import socket
import subprocess
import sys
import time
from pathlib import Path

from recency import Settings, TOKENS, Tracker, load_state, save_state
from presentation import workspace_branches, sync_sidebar, setup_sidebar, write_config

PLUGIN_ID = "herdr-space-recency"
SOURCE = "space-recency"
MAX_MESSAGE = 4 * 1024 * 1024
EVENTS = (
    "workspace.focused", "workspace.created", "workspace.closed", "workspace.renamed",
    "workspace.updated", "pane.agent_detected", "pane.exited",
)


class Context:
    def __init__(self):
        if os.environ.get("HERDR_ENV") != "1" or not os.environ.get("HERDR_SOCKET_PATH"):
            raise ValueError("Run from a Herdr pane or a plugin action (HERDR_ENV / HERDR_SOCKET_PATH missing)")
        self.endpoint = os.environ["HERDR_SOCKET_PATH"]
        self.session_file = Path(self.endpoint).parent / "session.json"
        home = Path.home()
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        state_root = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
        self.config = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR", config_root / "herdr/plugins/config" / PLUGIN_ID)) / "config.toml"
        self.herdr_config = Path(os.environ.get("HERDR_CONFIG_PATH", config_root / "herdr/config.toml"))
        root = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR", state_root / "herdr/plugins" / PLUGIN_ID))
        scope = hashlib.sha256(os.path.realpath(self.endpoint).encode()).hexdigest()[:16]
        self.directory = root / scope
        self.directory.mkdir(parents=True, exist_ok=True)
        self.timestamps = self.directory / "last-used.json"
        self.stop_file = self.directory / "stop"
        self.ready = self.directory / "ready"

    def connect(self):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(5)
        try:
            connection.connect(self.endpoint)
        except BaseException:
            connection.close()
            raise
        return connection

    def rpc(self, method, params=None):
        with self.connect() as connection:
            connection.sendall((json.dumps({"id": "space-recency", "method": method, "params": params or {}}) + "\n").encode())
            with connection.makefile("rb") as stream:
                line = stream.readline(MAX_MESSAGE + 1)
            if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                raise ValueError("Incomplete or oversized Herdr API response")
            response = json.loads(line)
            if "error" in response:
                raise RuntimeError(f"{method}: {response['error']}")
            return response["result"]

    def running(self):
        with (self.directory / "daemon.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    def report(self, workspace_id, tokens):
        self.rpc("workspace.report_metadata", {"workspace_id": workspace_id, "source": SOURCE, "tokens": tokens})

    def workspaces(self):
        snapshot = self.rpc("session.snapshot")["snapshot"]
        return workspace_branches(snapshot["workspaces"], self.session_file,
                                  tabs=snapshot.get("tabs", []), panes=snapshot.get("panes", []))

    def subscribe(self):
        connection = self.connect()
        try:
            connection.sendall((json.dumps({"id": "recency-events", "method": "events.subscribe", "params": {
                "subscriptions": [{"type": event} for event in EVENTS]
            }}) + "\n").encode())
            buffer = b""
            while b"\n" not in buffer:
                chunk = connection.recv(65536)
                if not chunk or len(buffer) + len(chunk) > MAX_MESSAGE:
                    raise ValueError("Incomplete or oversized subscription response")
                buffer += chunk
            line, buffer = buffer.split(b"\n", 1)
            response = json.loads(line)
            if response.get("result", {}).get("type") != "subscription_started":
                raise ValueError(f"Subscription rejected: {response}")
            return connection, buffer
        except BaseException:
            connection.close()
            raise


def log(error):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')}: {error}", file=sys.stderr, flush=True)


def normalize(context):
    """Leave names visible even while stopped; the user's row configuration stays put."""
    for workspace in context.workspaces():
        tokens = {
            token: workspace["label"] if token == "recency_fresh" else None for token in TOKENS
        }
        tokens["recency_branch_fresh"] = workspace["branch"]
        context.report(workspace["workspace_id"], tokens)


def run(context):
    with (context.directory / "daemon.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        context.stop_file.unlink(missing_ok=True)
        context.ready.unlink(missing_ok=True)
        tracker = Tracker(load_state(context.timestamps))
        settings = Settings.load(context.config)
        sent = {}
        events = None
        buffer = b""
        deadline = 0
        focused = set()
        retry_seconds = 1
        applied_colors = None
        try:
            while not context.stop_file.exists():
                if events is None:
                    try:
                        events, buffer = context.subscribe()
                        focused.clear()
                        tracker.focused.clear()
                        sent.clear()  # Metadata does not survive a server restart.
                        deadline = 0
                        retry_seconds = 1
                    except (OSError, ValueError) as error:
                        log(error)
                        if events is not None:
                            events.close()
                            events = None
                        retry_at = time.monotonic() + retry_seconds
                        while not context.stop_file.exists() and time.monotonic() < retry_at:
                            time.sleep(min(1, max(0, retry_at - time.monotonic())))
                        retry_seconds = min(30, retry_seconds * 2)
                        continue
                if time.monotonic() >= deadline:
                    try:
                        plugins = context.rpc("plugin.list")["plugins"]
                        if not any(plugin["plugin_id"] == PLUGIN_ID and plugin["enabled"] for plugin in plugins):
                            break
                        try:
                            settings = Settings.load(context.config)
                        except ValueError as error:
                            log(f"Keeping previous settings: {error}")
                        colors = (settings.faded_color, settings.dim_color, settings.branch_color,
                                  settings.branch_faded_color, settings.branch_dim_color)
                        if colors != applied_colors:
                            if sync_sidebar(context.herdr_config, settings):
                                context.rpc("server.reload_config")
                                applied_colors = colors
                        workspaces = context.workspaces()
                        now = time.time()
                        reports = tracker.update(workspaces, now, settings)
                        for workspace_id in focused:
                            if workspace_id in tracker.last_used:
                                tracker.last_used[workspace_id] = now
                        if focused:
                            reports = tracker.update(workspaces, now, settings)
                            focused.clear()
                        save_state(context.timestamps, tracker.last_used)
                        for workspace_id, tokens in reports.items():
                            if context.stop_file.exists():
                                break
                            if sent.get(workspace_id) != tokens:
                                context.report(workspace_id, tokens)
                                sent[workspace_id] = tokens
                        sent = {key: value for key, value in sent.items() if key in reports}
                        context.ready.touch()
                        deadline = time.monotonic() + settings.refresh_seconds
                    except (OSError, ValueError, KeyError, RuntimeError) as error:
                        log(error)
                        events.close()
                        events = None
                        time.sleep(1)
                        continue
                try:
                    if not buffer or b"\n" not in buffer:
                        if not select.select([events], [], [], min(1, max(0, deadline - time.monotonic())))[0]:
                            continue
                        chunk = events.recv(65536)
                        if not chunk:
                            raise ConnectionError("Herdr event stream closed; reconnecting")
                        buffer += chunk
                        if len(buffer) > MAX_MESSAGE:
                            raise ValueError("Oversized Herdr event")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        event = json.loads(line)
                        if "error" in event:
                            raise RuntimeError(f"Event subscription: {event['error']}")
                        if event.get("event") == "events_lost":
                            raise ConnectionError("Herdr events lost; resynchronizing")
                        if event.get("event") in {"workspace.focused", "workspace_focused"}:
                            focused.add(event["data"]["workspace_id"])
                        if "event" in event:
                            deadline = min(deadline, time.monotonic() + 0.1)
                except (OSError, ValueError, KeyError, RuntimeError) as error:
                    log(error)
                    events.close()
                    events = None
        finally:
            if events is not None:
                events.close()
            context.ready.unlink(missing_ok=True)
            try:
                normalize(context)
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                log(f"Could not restore normal names: {error}")


def start(context):
    Settings.load(context.config)
    if context.running():
        print("Space Recency is already running")
        return
    context.ready.unlink(missing_ok=True)
    # Herdr startup hooks must finish; only the child owns the event subscription.
    with (context.directory / "daemon.log").open("a") as output:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run"],
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 start_new_session=True, close_fds=True)
    for _ in range(100):
        if context.ready.exists() and context.running():
            print(f"Space Recency started; log: {context.directory / 'daemon.log'}")
            return
        if child.poll() is not None:
            raise RuntimeError(f"Daemon exited; see {context.directory / 'daemon.log'}")
        time.sleep(0.05)
    context.stop_file.touch()
    raise RuntimeError(f"Daemon not ready; requested stop. See {context.directory / 'daemon.log'}")


def setup(context):
    settings = Settings.load(context.config)
    path = context.herdr_config.resolve()
    before = path.read_text() if path.exists() else ""
    already_running = context.running()
    installed = None
    try:
        start(context)
        setup_sidebar(path, settings)
        installed = path.read_text()
        context.rpc("server.reload_config")
    except (OSError, ValueError, KeyError, RuntimeError):
        if not already_running:
            try:
                stop(context)
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                log(f"Could not stop after failed setup: {error}")
        if installed is not None and installed != before:
            try:
                write_config(path, installed, before, ".before-space-recency-setup")
                context.rpc("server.reload_config")
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                log(f"Could not restore config after failed setup: {error}")
        raise
    print(f"Space Recency setup complete: {context.herdr_config}")


def stop(context):
    if context.running():
        context.stop_file.touch()
        for _ in range(200):
            if not context.running():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("Stop requested; daemon is still finishing an API call. Try status shortly.")
    normalize(context)
    print("Space Recency stopped; current names restored to normal")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup", "start", "stop", "status", "run"))
    args = parser.parse_args()
    try:
        context = Context()
        if args.command == "status":
            print(json.dumps({"running": context.running(), "ready": context.ready.exists(), "state_dir": str(context.directory)}))
        else:
            {"setup": setup, "start": start, "stop": stop, "run": run}[args.command](context)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        parser.exit(1, f"space-recency: {error}\n")


if __name__ == "__main__":
    main()
