# How an archive becomes host configuration

Use **files-only recovery** when the goal is to put reviewed configuration back,
not to install packages, clone projects or activate applications. This is an
option on the existing `restore` and `preview` commands, using the same category
payloads. It is not a second backup format.

## Save the configuration and its helpers

```sh
imprint save --only services,scripts \
  --include-file .local/share/my-helper/watch.py \
  -o /existing/private-backups/config.tar.zst
imprint verify /existing/private-backups/config.tar.zst
```

`services` already captures local user units and their drop-in directories,
including standalone drop-ins for packaged units. `scripts` normally captures
text scripts in `~/bin` and `~/.local/bin`. Repeated `--include-file` arguments
add exact reviewed helper scripts elsewhere in `.local/share` or
`.hermes/scripts`. They require the scripts category. There are no recursive
helper globs, imports or automatic dependency execution.

Included helpers must be bounded regular UTF-8 scripts with a shebang. Symlink
components, traversal, special files and special permission bits are rejected.
A conservative quoted-credential assignment check rejects obvious inline
credentials. This is a heuristic, **not a secret-free guarantee**. Inspect the
source yourself. The scripts metadata records the original path, hash, mode,
and selected archived files containing a literal reference to that helper.
Unresolved/dynamic references and sibling data files need manual review.

Including a `.hermes/scripts` helper never includes cron jobs, sessions,
credentials or Signal account state, and never enables its schedule. Recovering
a monitor's code is not permission to activate monitoring.

New saves have a manifest integrity index covering every archived leaf except
the manifest itself: SHA-256 and size for regular files, safe original mode,
and link-target records. The existing manifest supplies capture time, source
home, hostname and producer version. System metadata also records its source
root. Archives are written to unique private mode-0600 temporary files before
rename. The index is verified on read, including noninteractive restore. Missing,
extra and changed payloads are refused. Tar extraction still uses Python's safe
`data` filter; indexed modes restore permissions that filter may normalize.

Hashes detect corruption, not authenticity. Anyone able to rewrite the manifest
can replace its hashes. Use a trusted installed Imprint to inspect an archive;
do not execute an untrusted archive's embedded tool or generated recipe.
Older archives remain inspectable but have no content-integrity claim and cannot
use files-only recovery. Make a new save with this version first.

## Preview and recover exact files

Create a disposable target home first. Never use a production home for tests.

```sh
mkdir -p /tmp/recovery-home
imprint preview config.tar.zst --only services,scripts --files-only \
  --target-home /tmp/recovery-home \
  --file .local/share/my-helper/watch.py \
  --file .config/systemd/user/my-helper.service.d/override.conf --json

imprint restore config.tar.zst --only services,scripts --files-only \
  --target-home /tmp/recovery-home \
  --file .local/share/my-helper/watch.py \
  --file .config/systemd/user/my-helper.service.d/override.conf
```

`--only` is mandatory in files-only mode. Repeat `--file` for exact relative
paths; without it all regular payload files in the selected categories are
considered. Missing requested paths and empty recipe-only selections fail.
This does not materialize Git recipes, plugin trees/overlays, symlink recipes,
themes' installation steps, or dconf exports.

The mapping is literal:

- `categories/services/files/.config/systemd/user/demo.service` becomes
  `TARGET_HOME/.config/systemd/user/demo.service`.
- `categories/scripts/files/.local/share/demo/watch.py` becomes
  `TARGET_HOME/.local/share/demo/watch.py`.
- The archive's source home is replaced at the existing home-path boundaries
  in bounded UTF-8 text. `%h`, shell variables and unrelated hostnames are not
  resolved or evaluated. Binary and oversized payloads are copied unchanged.
  Review rewritten strings in the preview; device names, IPs, usernames,
  hostnames, absolute paths outside the source home and dependencies are manual.

The preview reports source and destination paths, original and desired modes,
post-rewrite SHA-256, changed status and text unified diffs. Binary differences
are compared by bytes, not just size. Output may expose private configuration;
keep saved previews private. Preview is advisory, not a signed transaction or a
lock on other configuration writers. Quiesce competing writers before recovery.

Files-only restore verifies the archive, plans the full selection, refuses
symlink targets/ancestors, validates Python syntax, JSON and Bash/sh syntax,
and backs up every changed original before replacing any target. Writes use
no-follow directory descriptors, private temporary files, fsync and atomic
rename. A failed write triggers file rollback; the report states whether rollback
succeeded or needs intervention. JSON/Python/Bash validation does not establish
that application settings or dependencies are correct. Lua, QML, YAML, service
semantics and hardware behavior require their application-specific validators.

The report contains the undo directory. Restore replaced files and remove files
that were new with:

```sh
imprint undo --target-home /tmp/recovery-home --undo-dir /path/from/report
```

Omitting `--undo-dir` chooses the newest undo directory for the explicit target
home. A recovery journal is bound to its recorded home/system-root targets.
Original file bytes and modes return; created empty parent directories remain.
Ownership, ACLs, xattrs, SELinux labels and timestamps are not a recovery contract.
Treat undo directories as trusted private state. This is not a crash-proof
multi-file filesystem transaction or protection against hostile concurrent
writers. A killed process may need explicit undo.

## System files and privileges

System payloads live under `categories/system/etc/RELATIVE_PATH` and map to
`SYSTEM_ROOT/etc/RELATIVE_PATH`, not to the target home. Prefix exact selectors
with `etc/`. Both restore and undo require `--allow-system` for system writes.

```sh
mkdir -p /tmp/recovery-root
imprint restore config.tar.zst --only system --files-only \
  --system-root /tmp/recovery-root --allow-system \
  --file etc/systemd/logind.conf.d/local-policy.conf
```

Use `preview` with the same target arguments first. The root must already exist;
symlink ancestors are refused. No privileges are needed for a writable alternate
root. Writing real `/etc` requires appropriate privileges; files-only mode never
silently invokes sudo. If running explicitly as root, set the intended
`--target-home` so recovery history does not accidentally land in `/root`.
Undo system files with the identical `--system-root` and `--allow-system`.

`save --system-root DIR --only system` captures the allowlisted system category
from that source root and queries its enabled units offline. It does not capture
arbitrary `/usr/local`, boot disks or files outside the existing system allowlist.

The older **recipe restore** `--only system --allow-system` remains distinct:
it stages a reviewable root script, uses `sudo -n` for `/`, installs files as
0644 and performs enablement. It does not offer the files-only mode/rollback
contract. Its `--system-root` isolates only system operations, not other selected
categories. Prefer files-only for selective recovery; never interpret
`--system-root` alone as a sandbox for every recipe.

## Installed is not activated

Files-only performs **no** package, Git, desktop, service or scheduling commands.
Its report explicitly leaves activation pending. After reviewing files and
checking dependencies, separate the operations:

```sh
systemd-analyze --user verify ~/.config/systemd/user/demo.service
systemctl --user daemon-reload
systemctl --user enable demo.service
systemctl --user is-enabled demo.service
# Only with approval to run it now:
systemctl --user start demo.service
systemctl --user is-active demo.service
```

Daemon-reload rereads definitions; it does not restart running processes.
Enable changes future startup, not current activity. Start runs a stopped unit;
restart interrupts an existing process. An already-running daemon that needs
new settings is **restart-required**, even after daemon-reload succeeds.
An offline `systemctl --root=ROOT enable demo.service` changes target enablement
without running it. System policy may need a separate approved daemon restart,
logout/login, desktop reload or reboot; Imprint cannot infer successful runtime
behavior from an installed file.

Ordinary services-category recipe restore still reloads user systemd and enables
recorded enabled units, but no longer adds `--now`. Other recipe categories can
still have activation side effects (plugin installers, themes, fonts, dconf).
Final automatic Hyprland reload/shell restart have been removed. For guaranteed
no activation, use files-only rather than a recipe restore.

## Shell settings: never a whole stale snapshot

`--files-only` refuses `.config/omarchy/shell.json`. Ordinary bar restore requires
one or more explicit scalar JSON pointers:

```sh
imprint restore config.tar.zst --only bar --shell-key /idle/enabled
```

It captures a fresh live `config-edit snapshot`, copies that fresh base, changes
only the named archived scalar settings, and calls `config-edit apply BASE EDITED`
without a layout override. It verifies both a new live snapshot and saved disk
configuration against the edited result. A refused or unavailable snapshot is a
failure, never permission for direct disk overwrite. Live conflicts are not
force-retried. Object/array replacement and bar/plugin layout/membership pointers
are refused. Layout recovery stays a separate explicit operation through the
supported Omarchy bar/plugin APIs; no sorting or archived-layout opt-in is hidden
inside Imprint. Read host shell instructions first.

Legacy undo containing shell.json is refused rather than restoring it wholesale.
Recover the desired individual settings from that backup through a fresh live
config edit. Generated plans route regular payload copies through files-only
recovery and exclude shell.json; old legacy plans should be regenerated.

## Verified scope and remaining limits

The CLI regressions run on a disposable Arch VM with separate synthetic homes
and system roots, including save/verify/preview/restore/undo, mode preservation,
corruption, syntax failures, symlink-target refusal and a real RLIMIT_FSIZE write
failure followed by rollback. Discovery/Omarchy command fixtures are explicitly
not real graphical shell tests. The opt-in `tests/vm_service_smoke.py` additionally
uses real user systemd: validate unit, daemon-reload, enable while still inactive,
start the recovered helper, check its journal receipt, then stop/disable and undo.
The legacy suite also exercises real offline `systemctl --root` enablement.

No full Omarchy GUI session, reboot, bootloader, encrypted-disk unlock, account
recovery or bare-metal restoration is certified. No production configuration or
private snapshot payload belongs in this public repository.
