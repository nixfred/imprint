# Imprint

Stamp one Omarchy machine onto another.

Imprint writes a single archive of the desktop personality — theme, bar,
plugins, bindings, extra packages, user scripts, services — then restores
it onto a new box or an existing one. You space-select what travels.

```
imprint
imprint save
imprint restore ~/imprints/imprint-dex-20260906-1712.tar.zst
```

## Why this exists

Omarchy’s own story is: install from the ISO, then keep `~/.config` however
you like. Stow is the documented backup. Snapshots restore root, not home.
Atmos export covers *settings* (gaps, theme name, bindings), not plugins,
packages, or the bar layout.

Other people already built nearby tools. None of them is this:

| Tool | What it is | What it is not |
| --- | --- | --- |
| [OmaVault](https://github.com/mutahir/omavault) | File-level config snapshots | Does not reinstall plugins from git or extra packages |
| [Config Sync](https://github.com/gladimdim/omarchy-config-sync-plugin) | Private git repo of configs | Needs a git remote; not a single file you hand someone |
| [Time Machine](https://github.com/jankeesvw/omarchy-time-machine) | restic of `$HOME` | Whole-home backup, not a selectable desktop recipe |
| [Omarchy Guard](https://github.com/duclucky/omarchy-guard) / [Rollback](https://github.com/TadejPolajnar/omarchy-rollback) | Undo after an update | Same machine, not a new one |
| [Session restore](https://github.com/wbarakat/omarchy-session-restore) | Open windows across reboot | Not configs |
| [Atmos](https://github.com/csfh/atmos) import/export | Readable settings Markdown | No plugins, no packages, no bar widgets |
| [Discussion #5588](https://github.com/basecamp/omarchy/discussions/5588) | Proposed `omarchy backup` | Not shipped |

Imprint is the missing piece: **one file, category picker, plugin reinstall
from git, extra packages, `$HOME` rewrite, AI-readable brief.**

## Install

```bash
git clone https://github.com/nixfred/imprint.git
cd imprint
./install.sh
```

That links `imprint` into `~/.local/bin`. Omarchy already has `gum`, `python3`,
`tar`, and `zstd`.

## Use

Run `imprint` for the menu. The OMARCHY wordmark sits at the top. Space
toggles a category, Enter confirms.

```
imprint save                         # picker, writes ~/imprints/imprint-$host-$time.tar.zst
imprint save --only look,bar,plugins
imprint save --all -o /mnt/usb/dex.imprint.tar.zst

imprint restore FILE                 # picker of what is in that file
imprint restore FILE --only bar,plugins --dry-run
imprint restore FILE --only identity --confirm-hostname dex

imprint info FILE                    # machine brief; feed this to an agent
imprint diff FILE                    # what drifted since the imprint
imprint verify FILE
imprint undo                         # last restore’s safety copy
imprint list
```

The archive is self-contained. On a fresh Omarchy install you can extract it
and run `tool/imprint restore .` without installing anything first.

## What it carries

On by default (safe to take to another machine):

- **Look** — theme name, font, gaps, branding
- **Hyprland** — bindings, autostart, extra Lua (not monitors/input)
- **Bar and shell** — `shell.json` layout, idle, disabled plugins
- **Plugins** — git URLs plus local/clone trees, without `.git` / `node_modules`
- **Themes** — git remotes via `omarchy theme install`; local themes copied
- **Hooks and menu**
- **Terminals**
- **Defaults** — MIME, browser, editor, agent
- **Packages** — extra pacman/AUR on top of the Omarchy ISO lists
- **Scripts** — text scripts in `~/bin` and `~/.local/bin` (not 20MB binaries)
- **User services** — units you added under `~/.config/systemd/user`
- **CLI configs** — starship, git, btop, lazygit, XCompose, bashrc

Off unless you turn them on:

- **Wallpaper overlays** (often hundreds of megabytes)
- **Web apps**, **Neovim**
- **Monitors**, **pointer/keyboard** (host-bound)
- **Identity** (hostname only, extra confirm on restore)
- **Secrets** (SSH public keys and `config`; private keys stay put)

Never copied: LUKS/TPM, rclone tokens, Tesla auth, browser profiles, mail,
passwordless sudo, sshd.

On restore, `/home/olduser` inside text files becomes the new `$HOME`. Git
remotes of the form `git@github.com:...` are saved as `https://` so the new
box does not need your SSH key. Plugins land before `shell.json`, so the bar
has somewhere to put them. A live shell uses `omarchy shell config-edit apply
--allow-layout-change`; if the shell is not up, the file is copied.

Every restore first copies overwritten files to
`~/.local/state/imprint/undo-<time>/`. `imprint undo` puts them back.

## What else it can do

- **Dry-run restore** — print the plan, change nothing
- **Diff** — see what drifted after you lived on the new box
- **Brief** — Markdown an agent can follow without the archive
- **Verify** — schema and category folders
- **Undo** — last restore is reversible
- **Self-extracting tool** — the archive carries `imprint` itself

It will not clone disk encryption, lock policy, or another machine’s TPM.

## Archive layout

```
manifest.json          # kind=omarchy-imprint, host, omarchy version, categories
BRIEF.md               # human/agent readable
tool/imprint
tool/imprint-engine.py
categories/<id>/meta.json
categories/<id>/files/...
categories/plugins/trees/<plugin-id>/
```

## Tests

```bash
python3 tests/test_engine.py
```
