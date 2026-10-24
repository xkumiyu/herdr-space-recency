# Herdr Space Recency

Fade Space and branch names by time since last use: normal before 24 hours,
muted gray after 24 hours, and darker gray after 7 days. Focused Spaces and
Spaces with working, blocked, or unacknowledged completed agents stay bright.

Tracking starts on first run; history survives restarts. Space names, Git status
colors, state icons, and the Agents list are preserved. Nothing is renamed or
closed automatically. Grouped worktrees follow Herdr's branch-based names;
custom names are respected. The API omits the custom-name flag, so recognizing
automatic names or a rename to the same text as the automatic label can wait
for the session save.

Requires **Herdr 0.9.5+, Python 3.11+, Git, and Linux or macOS**. No extra packages.

## Install

Run inside Herdr:

```sh
herdr plugin install xkumiyu/herdr-space-recency
herdr plugin action invoke herdr-space-recency.setup
```

Setup starts the plugin and inserts [sidebar.toml](sidebar.toml) only if Spaces
settings are absent, then reloads Herdr. Existing layouts and colors are kept,
including previously generated blocks. If needed, adapt your existing rows
using the template. The plugin starts automatically with Herdr afterward.

Herdr's config defaults to `~/.config/herdr/config.toml`; `HERDR_CONFIG_PATH`
and `XDG_CONFIG_HOME` are respected. Before insertion into an existing file,
setup saves `config.toml.before-space-recency-setup` alongside it.

## Configure

**Timing:** create `config.toml` in the directory printed by:

```sh
herdr plugin config-dir herdr-space-recency
```

Defaults (also in [config.example.toml](config.example.toml)):

```toml
fade_after_hours = 24
dim_after_hours = 168
refresh_seconds = 60
```

Fractional hours are allowed; require `0 < fade_after_hours < dim_after_hours`
and `refresh_seconds >= 1`. Timing changes apply on the next refresh; invalid
settings are logged and the previous valid settings remain active.

**Colors and layout:** edit `[ui.sidebar.spaces]` in **Herdr's config.toml**.
See [sidebar.toml](sidebar.toml) for the six Space/branch state tokens and default
colors. The plugin does not write or reload Herdr config during startup or
periodic updates. After editing Herdr config, run:

```sh
herdr config check
herdr server reload-config
```

**Upgrading:** existing Herdr colors are preserved. The old plugin settings
`faded_color`, `dim_color`, `branch_color`, `branch_faded_color`, and
`branch_dim_color` are ignored and can be removed.

## Start, stop, and troubleshoot

```sh
herdr plugin action invoke herdr-space-recency.status
herdr plugin action invoke herdr-space-recency.stop
herdr plugin action invoke herdr-space-recency.start
```

Use `start` after re-enabling the plugin; duplicate starts are safe. Normal stop
restores normal display tokens for existing Spaces, keeping the sidebar layout.
**Spaces created while stopped have blank names** because no display tokens
are supplied. Existing tokens can remain, but names and branches stop updating.
Starting the plugin supplies tokens again; it does not switch the sidebar back
to Herdr's default layout automatically.

For blank names after a restart, check `status` and try `start`. The reported
`state_dir` contains `daemon.log` and `last-used.json`. If startup reports corrupt
history, move `last-used.json` aside before restarting.

Branch updates can lag in split layouts with different working directories.
Detached HEAD has no branch name. Run the plugin on each Herdr server; clients
on one server share history and cannot protect their focus independently.

## Remove

Stop the plugin, then remove its `[ui.sidebar.spaces]` table from Herdr config. Restore your previous Spaces layout if needed, using an earlier
`config.toml.before-space-recency-setup` backup. Removing the block without a
replacement restores Herdr's default layout. Then run:

```sh
herdr config check
herdr server reload-config
herdr plugin uninstall herdr-space-recency
```

## Development

Use `herdr plugin link .` instead of install, then invoke setup. Herdr runs the
checkout directly; keep it in place. Before installing from GitHub, run
`herdr plugin unlink herdr-space-recency`.

```sh
python3 -m unittest -v
python3 smoke.py
```

Smoke uses a disposable Herdr session to check setup, config preservation,
name/branch tokens, fading, focus, persistence, and plugin lifecycle. CI runs
unit tests on Linux/macOS with Python 3.11/3.14 and smoke on Linux with Herdr 0.9.5.
