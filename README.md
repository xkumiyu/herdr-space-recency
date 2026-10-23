# Herdr Space Recency

Find Spaces you may no longer need by fading their names as time passes since
last use. Space and branch names fade together; focusing a Space restores its
normal color.

## How it works

| Time since last use | Appearance (defaults) |
| --- | --- |
| Less than 24 hours | Normal theme color |
| 24 hours to less than 7 days | Muted gray |
| 7 days or more | Darker gray |

The currently focused Space stays at normal brightness. Spaces with agents that
are working, waiting for input, or showing an unacknowledged completion also
keep their normal color, so activity remains visible.

The plugin preserves Git status colors, state icons, and the Agents list.
It does not rename or automatically close Spaces. Worktrees grouped under a
parent project keep Herdr's compact layout without a duplicate branch row.

Tracking begins when the plugin first runs. Usage history is saved across
plugin and Herdr restarts.

## Requirements

- Herdr 0.9.5 or later
- Python 3.11 or later
- Git
- Linux or macOS

No additional Python packages or build step are required.

## Installation

Run the following commands in a terminal inside Herdr. You do not need to clone
the repository yourself; Herdr downloads and manages the plugin files.

```sh
herdr plugin install xkumiyu/herdr-space-recency
herdr plugin action invoke herdr-space-recency.setup
```

The `setup` action starts the plugin, configures the Spaces sidebar, and reloads
Herdr. No manual configuration editing is needed. Running it again is safe.
The plugin starts automatically on subsequent Herdr startups.

Setup replaces an existing `[ui.sidebar.spaces]` table and preserves other
settings. Before the first replacement, it saves the original configuration
alongside the file as `config.toml.before-space-recency-setup`. If you use a
custom Spaces layout, you can restore it from this backup when removing the
plugin. Nonstandard inline or dotted-key Spaces definitions must first be
converted to a `[ui.sidebar.spaces]` table.

By default, setup edits `~/.config/herdr/config.toml`; it respects
`HERDR_CONFIG_PATH` and `XDG_CONFIG_HOME`. To configure the sidebar manually
instead, start the plugin, copy [sidebar.toml](sidebar.toml) into the Herdr
configuration (replacing any existing Spaces table), then run
`herdr config check` and `herdr server reload-config`.

## Customize timing and colors

Find the plugin's configuration directory:

```sh
herdr plugin config-dir herdr-space-recency
```

Create a `config.toml` file in that directory. Use any of the following settings;
omitted settings keep their defaults:

```toml
fade_after_hours = 24
dim_after_hours = 168
refresh_seconds = 60
faded_color = "#A0A0A0"
dim_color = "#606060"
branch_color = ""
branch_faded_color = ""
branch_dim_color = ""
```

- `fade_after_hours`: hours until the first fade.
- `dim_after_hours`: hours until the darker color applies.
- `refresh_seconds`: how often to check elapsed time and configuration changes.
- `faded_color` and `dim_color`: text colors for both Space and branch names.
  The defaults suit dark backgrounds; choose colors that remain readable with
  your theme. Normal names use the theme's text color.
- `branch_color`, `branch_faded_color`, and `branch_dim_color`: optional branch
  colors for normal, faded, and dim states. Omitted or empty values inherit the
  corresponding Space color. To distinguish branches with muted blues, for
  example, set these to `#7F91A8`, `#64748A`, and `#485568` respectively.

For example, set `fade_after_hours = 6` and `dim_after_hours = 72` to fade after
six hours and dim after three days. Fractional hours are supported. Thresholds
must satisfy `0 < fade_after_hours < dim_after_hours`, and the refresh interval
must be at least one second. Colors must use `#RRGGBB` notation.

Changes take effect on the next refresh without restarting Herdr. Focus changes
are handled through events. Invalid settings are logged, and the previous valid
settings remain active.

Set colors in the plugin configuration rather than editing the sidebar block.
The plugin regenerates the marked block when colors change or the plugin
restarts, and reloads Herdr automatically. Other configuration remains untouched.
Before the first automatic update, it saves a backup alongside the Herdr
configuration as `config.toml.before-space-recency-colors`.

## Manage the plugin

```sh
herdr plugin action invoke herdr-space-recency.status
herdr plugin action invoke herdr-space-recency.stop
herdr plugin action invoke herdr-space-recency.start
```

Run `setup` after installing and `start` after re-enabling the plugin. Duplicate starts
are prevented automatically. Stopping restores normal colors for existing
names. To restore Herdr's standard display permanently, follow the removal
steps below; new Spaces and renames are not tracked while the plugin is stopped.

## Remove the plugin

1. Stop it:

   ```sh
   herdr plugin action invoke herdr-space-recency.stop
   ```

2. Remove the sidebar block, including its marker comments, from your Herdr
   configuration. Restore your previous `[ui.sidebar.spaces]` table if you had
   one, using `config.toml.before-space-recency-setup` if needed. Otherwise,
   removing the block restores Herdr's default layout.

3. Check, reload, and uninstall:

   ```sh
   herdr config check
   herdr server reload-config
   herdr plugin uninstall herdr-space-recency
   ```

## Troubleshooting and limitations

- **Existing Spaces do not fade immediately:** the plugin cannot recover usage
  history from before installation. It measures time since a Space was last
  viewed, rather than its age since creation.
- **Branch names update late in some split layouts:** when panes in the first
  tab use different directories, branch detection relies on Herdr's saved
  session and may wait for the next session save. Single-pane layouts and
  first-tab panes sharing a directory use live information. Detached HEAD
  states show no branch name, matching Herdr.
- **Names are blank after a restart:** the sidebar needs display data from the
  running plugin. Check `status` and try `start`. The reported `state_dir`
  contains `daemon.log` and usage history in `last-used.json`. If needed,
  restore the standard sidebar using the removal steps above.
- **Startup reports corrupt history:** move `last-used.json` aside before
  restarting. The plugin does not overwrite corrupt history automatically.
- **Remote Spaces:** run the plugin on each Herdr server where you want fading.
- **Multiple clients:** clients on the same server share history. The plugin
  cannot independently protect every client's currently viewed Space because
  Herdr's focus events do not identify clients.
- **Forced termination:** the last display remains until the plugin starts
  again. Disabling or unlinking normally stops it within the refresh interval.

## Development

To run a local checkout, use `herdr plugin link .` from the repository root
instead of `plugin install`, then follow the remaining installation steps.
Herdr runs the linked files directly, so keep the checkout in place. Use
`herdr plugin unlink herdr-space-recency` to unregister it without removing
the checkout. Unlink a development checkout before installing from GitHub.

Run the checks from the repository root:

```sh
python3 -m unittest -v
python3 smoke.py
```

The smoke test uses a dedicated Herdr server and temporary configuration. It
checks fading, branch updates, color changes, focus restoration, renaming,
persistence, and plugin lifecycle behavior without connecting to your regular
session.

GitHub Actions runs unit tests on Linux and macOS with Python 3.11 and 3.14,
plus the smoke test on Linux with Herdr 0.9.5. Dependabot checks GitHub Actions
dependencies weekly.
