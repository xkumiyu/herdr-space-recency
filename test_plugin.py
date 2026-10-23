import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from plugin import Context, EVENTS, run, PLUGIN_ID
from recency import save_state


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="recency-unit-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.endpoint = str(root / "api.sock")
        self.environment = patch.dict(os.environ, {
            "HERDR_ENV": "1", "HERDR_SOCKET_PATH": self.endpoint,
            "HERDR_PLUGIN_STATE_DIR": str(root / "state"),
            "HERDR_PLUGIN_CONFIG_DIR": str(root / "config"),
            "HERDR_CONFIG_PATH": str(root / "herdr.toml"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.context = Context()

    def server(self, reply):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.endpoint)
        listener.listen(1)
        self.requests = []

        def respond():
            with listener, listener.accept()[0] as connection:
                with connection.makefile("rb") as stream:
                    self.requests.append(json.loads(stream.readline()))
                connection.sendall(reply)

        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)

    def test_subscription_ack_and_buffered_focus(self):
        focus = b'{"event":"workspace_focused","data":{"workspace_id":"w2"}}\n'
        self.server(b'{"id":"recency-events","result":{"type":"subscription_started"}}\n' + focus)
        connection, buffer = self.context.subscribe()
        self.addCleanup(connection.close)
        self.assertEqual(buffer, focus)
        self.assertEqual(self.requests[0]["params"]["subscriptions"], [{"type": event} for event in EVENTS])
        self.assertNotIn("pane.agent_status_changed", EVENTS)  # requires a pane_id filter

    def test_subscription_rejection_is_not_readiness(self):
        self.server(b'{"id":"recency-events","error":{"code":"invalid_request"}}\n')
        with self.assertRaises(ValueError):
            self.context.subscribe()

    def test_rpc_errors_are_reported(self):
        self.server(b'{"id":"space-recency","error":{"code":"not_found"}}\n')
        with self.assertRaises(RuntimeError):
            self.context.rpc("workspace.list")

    def test_reconnect_discards_focus_events_from_before_event_loss(self):
        save_state(self.context.timestamps, {"w1": 0, "w2": 0})
        pairs = [socket.socketpair(), socket.socketpair()]
        for pair in pairs:
            for connection in pair:
                self.addCleanup(connection.close)
        buffered = (b'{"event":"workspace_focused","data":{"workspace_id":"w1"}}\n'
                    b'{"event":"events_lost"}\n')
        subscriptions = iter([(pairs[0][0], buffered), (pairs[1][0], b"")])
        reports = []

        def rpc(method, params=None):
            if method == "plugin.list":
                return {"plugins": [{"plugin_id": PLUGIN_ID, "enabled": True}]}
            if method == "session.snapshot":
                return {"snapshot": {"workspaces": [
                    {"workspace_id": "w1", "label": "old", "focused": False},
                    {"workspace_id": "w2", "label": "active", "focused": True},
                ]}}
            if method == "workspace.report_metadata":
                reports.append(params)
                if len(reports) == 4:
                    self.context.stop_file.touch()
                return {}
            self.fail(method)

        with patch.object(self.context, "subscribe", side_effect=lambda: next(subscriptions)), \
             patch.object(self.context, "rpc", side_effect=rpc), \
             patch("plugin.time.time", return_value=1_000_000):
            run(self.context)
        self.assertEqual(reports[2]["workspace_id"], "w1")
        self.assertEqual(reports[2]["tokens"]["recency_stale"], "old")

    def test_scopes_are_separate_per_session(self):
        with patch.dict(os.environ, {"HERDR_SOCKET_PATH": self.endpoint + "-other"}):
            other = Context()
        self.assertNotEqual(self.context.timestamps, other.timestamps)
        self.assertEqual(self.context.timestamps, Context().timestamps)

    def test_colors_retry_when_sidebar_is_added_after_daemon_start(self):
        self.context.config.parent.mkdir(parents=True)
        self.context.config.write_text("refresh_seconds = 1\n")
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        checks = 0
        reloads = []

        def rpc(method, params=None):
            nonlocal checks
            if method == "plugin.list":
                checks += 1
                return {"plugins": [{"plugin_id": PLUGIN_ID, "enabled": checks < 3}]}
            if method == "session.snapshot":
                return {"snapshot": {"workspaces": [{"workspace_id": "w1", "label": "home"}]}}
            if method == "server.reload_config":
                reloads.append(method)
            return {}

        with patch.object(self.context, "subscribe", return_value=(reader, b'{"event":"workspace_updated"}\n')), \
             patch.object(self.context, "rpc", side_effect=rpc), \
             patch("plugin.sync_sidebar", side_effect=[False, True]):
            run(self.context)
        self.assertEqual(reloads, ["server.reload_config"])

    def test_lock_prevents_duplicate_daemons(self):
        import fcntl
        self.assertFalse(self.context.running())
        with (self.context.directory / "daemon.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertTrue(self.context.running())
        self.assertFalse(self.context.running())

    def test_outside_herdr_is_rejected(self):
        with patch.dict(os.environ, {"HERDR_ENV": ""}):
            with self.assertRaises(ValueError):
                Context()


if __name__ == "__main__":
    unittest.main()
