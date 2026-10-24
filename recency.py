"""Workspace recency policy and durable timestamps; no Herdr side effects."""

import json
import math
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

TIERS = ("fresh", "faded", "stale")
TOKENS = tuple(f"recency_{prefix}{tier}" for prefix in ("", "branch_") for tier in TIERS)


@dataclass(frozen=True)
class Settings:
    fade_after_hours: float = 24
    dim_after_hours: float = 168
    refresh_seconds: float = 60

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field.name} must be a finite positive number")
        if self.fade_after_hours >= self.dim_after_hours:
            raise ValueError("fade_after_hours must be smaller than dim_after_hours")
        if self.refresh_seconds < 1:
            raise ValueError("refresh_seconds must be at least 1")

    @classmethod
    def load(cls, path):
        try:
            with Path(path).open("rb") as stream:
                values = tomllib.load(stream)
        except FileNotFoundError:
            return cls()
        # Retired color keys are ignored so existing installations keep running.
        for key in ("faded_color", "dim_color", "branch_color",
                    "branch_faded_color", "branch_dim_color"):
            values.pop(key, None)
        unknown = values.keys() - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        return cls(**values)


def load_state(path):
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or any(
        not isinstance(key, str) or isinstance(value, bool)
        or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0
        for key, value in data.items()
    ):
        raise ValueError(f"Invalid timestamp state: {path}")
    return data


def save_state(path, data):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(data, stream, sort_keys=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class Tracker:
    def __init__(self, last_used):
        self.last_used = dict(last_used)
        self.focused = set()

    def update(self, workspaces, now, settings, focused_event=None):
        ids = {workspace["workspace_id"] for workspace in workspaces}
        self.last_used = {key: value for key, value in self.last_used.items() if key in ids}
        focused = {workspace["workspace_id"] for workspace in workspaces if workspace.get("focused")}
        for key in (self.focused | focused | {focused_event}) & ids:
            self.last_used[key] = now
        self.focused = focused
        reports = {}
        for workspace in workspaces:
            key = workspace["workspace_id"]
            age = max(0, now - self.last_used.setdefault(key, now)) / 3600
            protected = key in focused or workspace.get("agent_status") in {"working", "blocked", "done"}
            tier = "fresh" if protected or age < settings.fade_after_hours else (
                "faded" if age < settings.dim_after_hours else "stale"
            )
            reports[key] = {token: workspace["label"] if token == f"recency_{tier}" else None for token in TOKENS}
            reports[key][f"recency_branch_{tier}"] = workspace.get("branch") or None
        return reports
