# External audit — 2026-09-06

Produced by OpenAI Codex (codex-cli 0.153.4) against commit `3504d1c`, read-only.
Claims marked verified below were independently reproduced before acting on them.
Fixes landed in `3b19774`; the coverage gaps in section 2 are **not** yet addressed.

## 1. BUGS AND CODE OPINION

**Imprint is a selective desktop-config exporter with an unreliable restore path. I would not trust it as the only backup of dex.**

I audited commit `3504d1c`, both executables, every collector, and the installed Omarchy commands they invoke. The machine reports `dex`, user `pi`, Omarchy `4.0.2`, and Python `3.14.7`. This was a read-only audit: ten existing tests that require no filesystem writes passed, and I reproduced several defects with mutations mocked. I did not perform an end-to-end restore.

D-Bus and Docker socket access were denied. Service enablement below comes from actual unit files, symlinks, and `systemctl --root=/ list-unit-files`; it does **not** establish that those services are currently running.

**Defects, ranked by severity:**

1. **Critical — Archive metadata can direct recursive deletion outside the plugin directory.**  
   `plug["id"]` becomes a destination path without validation; `plug["tree"]` also becomes a source path without containment checks. The restore then recursively deletes the destination. I reproduced a metadata entry with `id="../../../Documents"` reaching a deletion target equivalent to `/home/pi/Documents`. Selecting “Plugins” is sufficient.  
   The tar extraction filter does not validate these JSON paths. Other categories also accept arbitrary home-relative payload destinations rather than enforcing category-specific destinations.  
   **Failure scenario:** a malformed or malicious archive deletes Documents or overwrites `.bashrc` through a category ostensibly carrying plugins or terminal settings.  
   [imprint-engine.py:1246](/home/pi/Projects/imprint/imprint-engine.py:1246), [imprint-engine.py:1264](/home/pi/Projects/imprint/imprint-engine.py:1264), [imprint-engine.py:1223](/home/pi/Projects/imprint/imprint-engine.py:1223).

2. **High — Git-backed customizations are discarded, and the saved commit is ignored.**  
   Any plugin with an origin URL gets **no payload**, even when `dirty=True`. Restore clones the URL without using the recorded commit or branch. Themes similarly record a commit but never restore it; their dirty state is not even recorded.  
   **Present on dex:** actual source modifications exist in `jankeesvw.tesla/Panel.qml`, `lgse.sandman/{BarWidget.qml,Panel.qml,…}`, and `nixfred.infomarchy/{InfoModel.qml,InfoView.qml,collector.ts,…}`. Those changes would not survive a new capture. Two other “dirty” plugins contain only Python caches and are not substantive losses.  
   **Failure scenario:** a locally repaired plugin returns to upstream behavior, or a non-default development branch is replaced by the repository’s default branch.  
   [imprint-engine.py:675](/home/pi/Projects/imprint/imprint-engine.py:675), [imprint-engine.py:680](/home/pi/Projects/imprint/imprint-engine.py:680), [imprint-engine.py:1253](/home/pi/Projects/imprint/imprint-engine.py:1253), [imprint-engine.py:717](/home/pi/Projects/imprint/imprint-engine.py:717), [imprint-engine.py:1327](/home/pi/Projects/imprint/imprint-engine.py:1327).

3. **High — Restoring a Git theme can delete the destination’s theme permanently.**  
   Imprint calls `omarchy theme install` without first backing up the existing Git theme. The installed command executes `rm -rf "$THEME_PATH"` **before** attempting the clone.  
   **Failure scenario:** restore onto an existing machine while offline. Its customized theme directory is deleted; cloning fails; Imprint has no undo copy. Installing each theme also immediately applies it, even when “Look” was not selected.  
   [imprint-engine.py:1317](/home/pi/Projects/imprint/imprint-engine.py:1317), [omarchy-theme-install:50](/usr/bin/omarchy-theme-install:50), [omarchy-theme-install:63](/usr/bin/omarchy-theme-install:63).

4. **High — Restoring over a plugin installed as a directory symlink crashes.**  
   `backup_existing()` handles an ordinary directory with `copytree`, but a directory symlink falls into `copy2(..., follow_symlinks=True)`. That tries to open a directory as a file and raises `IsADirectoryError`. Existing broken symlinks similarly fail when dereferenced.  
   **Present on dex:** `nixfred.workspace-names` links to `/home/pi/Projects/larry.workspace.name`; `nixfred.apple-notes` is another directory symlink. I verified the erroneous `copy2` dispatch and the source-open exception without writing anything.  
   **Failure scenario:** restoring plugins onto dex aborts after earlier categories/plugins have already changed.  
   [imprint-engine.py:1203](/home/pi/Projects/imprint/imprint-engine.py:1203), [imprint-engine.py:1250](/home/pi/Projects/imprint/imprint-engine.py:1250).

5. **High — Failure is routinely reported as success.**  
   Package, plugin, theme, service, hostname, and shell-config failures become strings in an actions list. The aggregate report remains `"ok": true`, and `cmd_restore()` returns zero. Final Hyprland reload and shell restart failures are ignored. I reproduced exit zero and `"ok": true` with a failed package action.  
   Capture has a related problem: `run_ok()` turns command failure into empty output. For example, unavailable user D-Bus produces an empty enabled-unit set, silently recording enabled custom services as disabled.  
   **Failure scenario:** an unattended restore finishes “successfully” with missing packages, refused bar configuration, and failed services.  
   [imprint-engine.py:223](/home/pi/Projects/imprint/imprint-engine.py:223), [imprint-engine.py:1304](/home/pi/Projects/imprint/imprint-engine.py:1304), [imprint-engine.py:1400](/home/pi/Projects/imprint/imprint-engine.py:1400), [imprint-engine.py:1506](/home/pi/Projects/imprint/imprint-engine.py:1506), [imprint-engine.py:1523](/home/pi/Projects/imprint/imprint-engine.py:1523).

6. **High — “Undo” is not a rollback, and it can overwrite its own originals.**  
   No record exists for paths that were initially absent, so undo leaves newly installed files and plugins behind. It does not reverse service enablement, package changes, hostname changes, or theme/font side effects. It writes restored `shell.json` directly rather than using the live config editor.  
   Worse, both `look` and `defaults` carry `.config/omarchy/defaults`. The second category backs up the **already restored** value over the original undo copy.  
   **Failure scenario:** browser default starts as A; archive contains B. Restore changes A→B, the second backup replaces A with B, and undo leaves B. Newly enabled services also remain enabled.  
   [imprint-engine.py:593](/home/pi/Projects/imprint/imprint-engine.py:593), [imprint-engine.py:767](/home/pi/Projects/imprint/imprint-engine.py:767), [imprint-engine.py:1203](/home/pi/Projects/imprint/imprint-engine.py:1203), [imprint-engine.py:1605](/home/pi/Projects/imprint/imprint-engine.py:1605).

7. **High — Script capture produces broken installed commands, including Imprint itself.**  
   File symlinks are dereferenced into standalone files, but their supporting repositories/modules are not collected.  
   **Present on dex:** `.local/bin/imprint` links to this repository. Capture converts it into a standalone wrapper, while neither adjacent `imprint-engine.py` location exists on a fresh target. Its startup therefore exits “cannot find imprint-engine.py.” The copy under the archive’s `tool/` directory does not repair the command installed on PATH.  
   `.local/bin/orrery` likewise derives its import path from its resolved source location; flattening that launcher loses its Python source tree. Atmos’s launcher points into an entirely omitted application installation.  
   [imprint-engine.py:399](/home/pi/Projects/imprint/imprint-engine.py:399), [imprint-engine.py:803](/home/pi/Projects/imprint/imprint-engine.py:803), [imprint:5](/home/pi/Projects/imprint/imprint:5), [install.sh:6](/home/pi/Projects/imprint/install.sh:6), [orrery:6](/home/pi/.local/bin/orrery:6), [atmos:2](/home/pi/.local/bin/atmos:2).

8. **High — “Secrets off” does not enforce a secret boundary.**  
   General collectors follow file symlinks and copy arbitrary readable content. Only the dedicated SSH collector excludes private-key filenames.  
   **Concrete constructed scenario:** a file symlink inside a local plugin points to `~/.ssh/id_ed25519` or `~/.env`. The plugin collector dereferences and archives it with Secrets off. Ordinary scripts/configs containing credentials are also copied without classification. I am **not claiming I found an exposed private key in the current archive**.  
   [imprint-engine.py:409](/home/pi/Projects/imprint/imprint-engine.py:409), [imprint-engine.py:689](/home/pi/Projects/imprint/imprint-engine.py:689), [imprint-engine.py:926](/home/pi/Projects/imprint/imprint-engine.py:926).

9. **Medium — Existing Git plugins silently remain unchanged.**  
   Omarchy’s installed `plugin add` rejects an already-used ID. Imprint treats any failure containing “already” as success. It neither updates nor checks the installed origin, commit, contents, or enabled state. Packed plugins’ recorded `enabled` value is never applied at all.  
   **Failure scenario:** restoring Plugins alone onto an existing machine leaves an old Git plugin installed and reports “from source”; restoring an enabled local clone onto a fresh machine installs its files without enabling it.  
   [imprint-engine.py:1257](/home/pi/Projects/imprint/imprint-engine.py:1257), [imprint-engine.py:1261](/home/pi/Projects/imprint/imprint-engine.py:1261), [omarchy-plugin-add:135](/usr/bin/omarchy-plugin-add:135).

10. **Medium — A failed live snapshot is incorrectly interpreted as “shell not running.”**  
    Any nonzero `config-edit snapshot` result falls back to directly overwriting `shell.json`. Missing command support, IPC permission failure, timeout, or another snapshot error does not prove that the shell is stopped.  
    **Failure scenario:** the shell is running but IPC fails; Imprint bypasses the concurrency protection and overwrites its configuration anyway.  
    [imprint-engine.py:1383](/home/pi/Projects/imprint/imprint-engine.py:1383), [imprint-engine.py:1402](/home/pi/Projects/imprint/imprint-engine.py:1402).

11. **Medium — Restore ordering ignores important command side effects.**  
    Themes install/apply before scripts and hooks are restored. `look` applies the font before terminal files are backed up/restored. Wallpaper overlays arrive after theme selection.  
    **Failure scenarios:** the archived theme hook never runs against the restored setup; font changes destroy the target’s original terminal setting before its undo copy is taken; newly restored wallpapers are never selected. On dex, font-set also writes an uncaptured fontconfig file and forces Foot’s font size to 9 before the terminal category later overwrites it.  
    [imprint-engine.py:1508](/home/pi/Projects/imprint/imprint-engine.py:1508), [imprint-engine.py:1336](/home/pi/Projects/imprint/imprint-engine.py:1336), [omarchy-font-set:43](/usr/bin/omarchy-font-set:43), [omarchy-theme-set:338](/usr/bin/omarchy-theme-set:338).

12. **Medium — `verify` and `diff` provide false confidence.**  
    `verify` checks the archive kind and category-path existence. It does not enforce the schema version, validate metadata, require payloads, or check file hashes. A synthetic schema `999` passed.  
    `diff` ignores packages, Git plugin versions, packed plugin trees, service enablement, and unexpected destination files. Binary/oversized-file comparison falls back to **size only**.  
    **Failure scenario:** delete plugin payloads while leaving the category directory; verification passes. Replace a wallpaper with different bytes of equal length; diff reports no change.  
    [imprint-engine.py:1127](/home/pi/Projects/imprint/imprint-engine.py:1127), [imprint-engine.py:1552](/home/pi/Projects/imprint/imprint-engine.py:1552), [imprint-engine.py:1574](/home/pi/Projects/imprint/imprint-engine.py:1574).

13. **Medium — Filesystem reconstruction has additional correctness failures.**  
    Nested directory symlinks are silently omitted by `os.walk(..., followlinks=False)`. Absolute broken file symlinks can be saved but are rejected by the restore’s `data` extraction filter; I reproduced `AbsoluteLinkError`. Destination **parent** symlinks remain followed even though leaf symlinks are handled. Home rewriting is plain substring replacement: `/home/pip/shared` becomes `/home/alicep/shared` when migrating `/home/pi`→`/home/alice`.  
    **Failure scenarios:** linked configuration subdirectories disappear; an otherwise valid backup becomes unreadable because it contains one dangling link; unrelated paths in configuration are corrupted.  
    [imprint-engine.py:393](/home/pi/Projects/imprint/imprint-engine.py:393), [imprint-engine.py:399](/home/pi/Projects/imprint/imprint-engine.py:399), [imprint-engine.py:444](/home/pi/Projects/imprint/imprint-engine.py:444), [imprint-engine.py:1124](/home/pi/Projects/imprint/imprint-engine.py:1124).

14. **Medium — Saves can overwrite previous backups or race on the same temporary file.**  
    Default names have minute resolution, and the final rename replaces an existing destination. The temporary filename is a predictable shared `FILE.partial`.  
    **Failure scenario:** two ordinary saves within one minute replace the first backup; concurrent saves to the same output unlink or rename each other’s partial archive. There is also no lock serializing concurrent restores.  
    [imprint-engine.py:230](/home/pi/Projects/imprint/imprint-engine.py:230), [imprint-engine.py:1111](/home/pi/Projects/imprint/imprint-engine.py:1111), [imprint-engine.py:1162](/home/pi/Projects/imprint/imprint-engine.py:1162).

15. **Medium — The wrapper drops accepted flags on the interactive restore path.**  
    `--confirm-hostname` is parsed, but only forwarded when both archive and `--only` are supplied. The picker path discards it. `--yes` is accepted and discarded rather than suppressing its prompts.  
    **Failure scenario:** `imprint restore FILE --confirm-hostname dex`, followed by selecting Identity, still skips the hostname change.  
    [imprint:375](/home/pi/Projects/imprint/imprint:375), [imprint:388](/home/pi/Projects/imprint/imprint:388), [imprint:247](/home/pi/Projects/imprint/imprint:247).

The package model also has a fundamental limitation: “foreign to the configured pacman repositories” is treated as “available from AUR,” and every `omarchy-*` name is treated as stock. Neither inference is generally valid. A locally built private package will be sent to `yay` without its build source. [imprint-engine.py:369](/home/pi/Projects/imprint/imprint-engine.py:369), [imprint-engine.py:377](/home/pi/Projects/imprint/imprint-engine.py:377).

## 2. COVERAGE AUDIT — WHAT WOULD NOT COME BACK

**Even selecting every category touches only 14 of dex’s 103 top-level `~/.config` entries. The other 89 are completely omitted. Several of the 14 are only partially captured.**

The captured entries are `hypr`, `omarchy`, `systemd`, `nvim`, four terminal directories, `git`, `btop`, `lazygit`, `starship.toml`, `mimeapps.list`, and `xdg-terminals.list`. There is no general collector for `/etc`, `/usr/local`, Projects, application data, or persistent user state. The collector dispatch and allowlists establish that boundary. [imprint-engine.py:588](/home/pi/Projects/imprint/imprint-engine.py:588), [imprint-engine.py:747](/home/pi/Projects/imprint/imprint-engine.py:747), [imprint-engine.py:850](/home/pi/Projects/imprint/imprint-engine.py:850), [imprint-engine.py:947](/home/pi/Projects/imprint/imprint-engine.py:947).

**The losses below are ordered by impact.**

1. **Personal work, repositories, and the machine’s accumulated knowledge. — Capture source recipes plus irreplaceable data.**

   I found **152 top-level project directories**, including **86 top-level Git checkouts**. A bounded recursive scan found **151 checkout roots**, including nested working copies and backup copies; that is not 151 distinct projects.

   Examples include Atmos, Plonk, Workspace Names, Omarchy forks, the shell-persistence repair, Flea branches, backup tooling, and numerous applications. None is inventoried by Imprint as a project. Some installed plugin URLs are recorded, but that does not recreate their working directories, branches, worktrees, or local changes.

   Local Git inspection found dirty work in `clipsync`, `flea-network`, `omarchy-server-status`, and `ram.plugin.omarchy`. Several checkouts are ahead of their **locally recorded** upstream; I did not fetch to establish remote publication status.

   Also omitted: `~/.claude/MEMORY`, authored agent instructions/skills/hooks, `~/.codex` configuration and personal resources, `~/AGENTS.md`, the canonical wish list, `~/Work`, and personal Pictures/Recordings/Videos. These cannot be regenerated by reinstalling applications.

   **Required:** repository URL, branch, commit, worktree relationships, patches, untracked authored files, and Git bundles for unpublished commits; separate application-consistent data backup for personal state. Preserve the canonical [wish list](/home/pi/Omarchy-To-Do-Wish-List.md), not another competing copy.

2. **Backup operation and system safety repairs. — Capture declarative system recipes and their source files.**

   `dex-backup.timer` is enabled, but its system service, timer, drop-ins, and `/usr/local/bin/dex-backup` are omitted. Installing `restic` does not configure a backup. The restored Backup Sizzle widget would have no corresponding backup job. [dex-backup.service:8](/etc/systemd/system/dex-backup.service:8), [dex-backup.timer:5](/etc/systemd/system/dex-backup.timer:5).

   **OMW-SUSPEND-LOCKUPS:** both installed containment policies are omitted:  
   `/etc/systemd/logind.conf.d/90-pi-stay-awake.conf` and `/etc/systemd/sleep.conf.d/90-pi-suspend-quarantine.conf`. Their source and explanation under `~/.local/share/lockup-repair` are also omitted. [lid policy:3](/etc/systemd/logind.conf.d/90-pi-stay-awake.conf:3), [sleep policy:4](/etc/systemd/sleep.conf.d/90-pi-suspend-quarantine.conf:4).

   **OMW-NO-LOCK:** shell configuration travels, but the persistent Stay Awake indicator does not. The installed idle command explicitly reads that file. Autologin and its custom session are also outside coverage. [omarchy-toggle-idle:8](/usr/bin/omarchy-toggle-idle:8), [autologin.conf:1](/etc/sddm.conf.d/autologin.conf:1).

   **Required:** record desired backup schedules, repository configuration, explicit idle/login policy, and system drop-ins. Keep backup credentials separate. Treat the suspend quarantine as a documented source-machine workaround requiring a target policy decision, rather than blindly applying it everywhere.

3. **Locally installed applications and development toolchains. — Capture installation/build recipes.**

   Missing application trees include `~/.local/share/atmos`, `com.danwahlin.agentarcade`, `aether`, `mirador`, and other local installations. Atmos’s captured launcher explicitly requires its omitted tree. [atmos.desktop:4](/home/pi/.local/share/applications/atmos.desktop:4).

   The script collector skips installed executables such as `librepods`, `librepods-ctl`, `mirador`, `strata`, `wacli`, and `go-chromecast`, recording only their path and size. That is an exclusion list, not a reinstall recipe. `~/go/bin` is not scanned at all; it contains Fabric, Amass, httpx, subfinder, and Tesla tools.

   Mise configuration specifies Node `26.8.1`, Claude `2.1.259`, Go, Rust, Codex, Gemini, Grok, Playwright, Wrangler, and other tools, but is omitted. So are Bun installations/global tools, Cargo/Rustup state, and project toolchain declarations such as `~/Work/.mise.toml`. [mise/config.toml:1](/home/pi/.config/mise/config.toml:1).

   **Required:** exact tool/provider/version inventories, build dependencies, source URLs, install commands, and verification commands. Rebuild compiled programs; preserve their authored source. I found no zsh/fish/asdf/nvm installation/configuration in the inspected locations, so those are portability gaps, not demonstrated losses on dex.

4. **User-service dependencies and complete service state. — Capture unit files plus dependency recipes and explicit state.**

   **OMW-SELECT-COPY:** the drop-in is collected, but the handler it executes is not:  
   `%h/.local/share/selection-clipboard/copy-selection.py`. That directly breaks the repaired behavior on a fresh target. [selection-copy.conf:9](/home/pi/.config/systemd/user/primary-mirror.service.d/selection-copy.conf:9).

   `rclone-rcd.service` requires omitted `~/.config/rclone/rcd.env`; `dexup.service` depends on omitted machine/brain repositories and runtime tools. [rclone-rcd.service:9](/home/pi/.config/systemd/user/rclone-rcd.service:9), [dexup.service:9](/home/pi/.config/systemd/user/dexup.service:9).

   The collector ignores enablement of packaged units unless they also have a top-level local unit file. Dex’s enabled `ydotool.service` is an example. `.path`, `.mount`, `.automount`, `.target`, and other unit types are excluded. Stock-prefixed units’ custom drop-ins are explicitly skipped. Disabled state is never enforced during restore. [imprint-engine.py:555](/home/pi/Projects/imprint/imprint-engine.py:555), [imprint-engine.py:825](/home/pi/Projects/imprint/imprint-engine.py:825), [imprint-engine.py:1427](/home/pi/Projects/imprint/imprint-engine.py:1427).

   **Required:** units, drop-ins, aliases, masks, enablement, dependencies, user lingering where needed, and separately specified desired runtime state. Inspect `ExecStart`, `EnvironmentFile`, and working directories for missing dependencies.

5. **Omarchy’s actual personality outside `shell.json`. — Capture selected files/state and explicit settings.**

   Missing on dex:

   - `~/.config/omarchy/shell.toml`: font sizing, opacity, menu scrim, and other visual overrides.
   - `workspace-names.json`: saved names and archived names.
   - `dock-settings.json`, `wallpaper-command-center.json`, `sandman.json`, `server-status.json`.
   - `auto-wallpaper/` and `omalaunch/`.
   - Persistent state: weather settings, radio favorites, Omagotchi settings/state, workspace layouts, power profiles, Hyprland toggles, and Stay Awake.
   - The selected wallpaper path. Saving theme name plus image directories does not preserve which image was selected.

   These are operational settings, not merely caches. The installed shell explicitly reads the omitted `shell.toml`, and Workspace Names explicitly reads the omitted JSON. [Color.qml:244](/usr/share/omarchy/shell/Commons/Color.qml:244), [shell.toml:1](/home/pi/.config/omarchy/shell.toml:1), [Workspace Names Service.qml:36](/home/pi/Projects/larry.workspace.name/Service.qml:36).

   **OMW-PLUGIN-ORDER:** the installed `shell.qml` differs in size from its packaged version and matches the shell-persistence repair checkout byte-for-byte. Imprint records only an Omarchy version string; it has no recipe for that installed source repair.

   **Required:** preserve semantic preferences and selected assets; record the Omarchy fork/patch provenance. Exclude generated theme caches, logs, and old repair screenshots by default. Clipboard contents, notification history, and similar private state need an explicit encrypted-data policy.

6. **Shell behavior and Git workflow. — Capture files and their dependencies.**

   `.bashrc` is copied, but its referenced `~/.config/bash/vic-aliases.sh` and `login-banner.sh` are not. A referenced Claude shell helper is also outside scope. [bashrc:17](/home/pi/.bashrc:17), [bashrc:31](/home/pi/.bashrc:31).

   Git config is copied, but `~/git-hooks/{commit-msg,pre-push}` is omitted; the configured credential helper points into omitted Mise shims. [git/config:33](/home/pi/.config/git/config:33).

   Also omitted: `~/.config/tmux`, `environment.d`, `~/.bash_logout`, `~/.ignore`, and the user avatar files `.face`/`.face.icon`.

   **Required:** include sourced shell files, environment declarations, Git hooks, tmux config, and avatar assets. Preserve histories only as optional private data.

7. **Networking, remote access, and firewall behavior. — Capture sanitized recipes; reauthenticate secrets.**

   Confirmed enabled system services include NetworkManager, Tailscale, SSH, UFW, Docker, CUPS, Avahi, Bluetooth, and others. No system-service state is collected.

   Rclone’s `omarchy-automounts.json` and `omarchy-mountfs.json` are omitted along with its credential-bearing configuration. This loses mount definitions as well as authentication.

   `/etc/ufw/{user.rules,user6.rules,after.rules,after6.rules,ufw.conf}` are modified relative to package backups. SSH’s local password-authentication policy is also omitted. [00-local-password-policy.conf:2](/etc/ssh/sshd_config.d/00-local-password-policy.conf:2).

   NetworkManager connection storage and Tailscale daemon state were inaccessible. **I cannot establish their exact contents.**

   **Required:** recreate named connections, mounts, firewall rules, SSH policy, Tailscale settings, and service enablement. Parameterize interfaces and target identity. Deliberately exclude Wi-Fi credentials, Tailscale node identity, SSH host keys, and OAuth tokens from the portable payload; provide secure import/reauthentication steps.

8. **Containers and virtualization. — Capture definitions and separately backed-up data.**

   Docker is installed and enabled. Its omitted daemon config specifies DNS, bridge addressing, and log rotation; its systemd override is omitted too. [daemon.json:2](/etc/docker/daemon.json:2), [docker override:1](/etc/systemd/system/docker.service.d/no-block-boot.conf:1).

   A real Compose template exists at `~/Projects/dex-docker-sandboxes/ubuntu-node/docker-compose.yml`, with a bind-mounted workspace. Its README explicitly calls these templates, so I am **not claiming a running container exists**. Docker socket access and `/var/lib/docker` inspection were denied. [docker-compose.yml:1](/home/pi/Projects/dex-docker-sandboxes/ubuntu-node/docker-compose.yml:1).

   Distrobox is installed. Incus and libvirt client configurations exist, including a storage-pool definition targeting `~/VMs/atmos-test`; that does not establish a working VM. Podman/Incus/libvirt system installations were not found in the inspected locations.

   **Required:** Compose/Containerfiles, image digests or build commits, networks, volume definitions, Distrobox creation commands, and VM definitions. Persistent volumes, databases, and VM disks need consistent data backups; pulling an image cannot restore them.

9. **Browser, application, and agent state. — Split portable preferences from private data.**

   Raw profiles for Brave, Chromium, Chrome variants, Firefox/Mozilla, Zen, Edge variants, Opera, and Vivaldi are outside capture. Some are only small stub directories. Their existence does not establish active use.

   Portable browser flags and Chromium’s managed color policy are also omitted, even though they are not browser credentials. [color.json:1](/etc/chromium/policies/managed/color.json:1).

   Keyring files exist under `~/.local/share/keyrings`; SSH private keys, GnuPG material, agent authentication, Rclone/Tesla tokens, and application credential stores are deliberately or incidentally absent. Browser bookmarks/extensions, agent settings/skills, Obsidian configuration, and application databases are not represented as export/import operations.

   **Required:** reinstall applications and extensions through recipes; export/import portable preferences and bookmarks. Preserve meaningful application data separately. Keep credentials encrypted or require reauthentication. Do not treat an entire Electron profile as either wholly disposable or wholly safe to copy.

10. **Input, audio, GTK/Qt appearance, printers, and the remaining system configuration. — Capture policy, regenerate machine-specific details.**

    - **OMW-VOX-ALERT-LOOP:** Voxtype config selects SenseVoice, and `/usr/bin/voxtype` currently points to the ONNX executable. Neither that backend-selection operation nor the model configuration/download is captured, although the retry-limit drop-in is. [voxtype config:12](/home/pi/.config/voxtype/config.toml:12).
    - Solaar rules, Fcitx configuration, and WirePlumber’s Bluetooth A2DP rule are omitted. [autostart.lua:9](/home/pi/.config/hypr/autostart.lua:9), [Bluetooth rule:5](/home/pi/.config/wireplumber/wireplumber.conf.d/bluetooth-a2dp-autoconnect.conf:5). Solaar, KDE Connect, and WayVNC packages are currently absent despite related configs/startup references; those are existing inconsistencies, not proven working capabilities that Imprint alone would break.
    - Dconf contains dark-mode, cursor, GTK/icon-theme, font, primary-paste, and scaling preferences. These are omitted. No personal font directory was found. Dconf requests SF Pro/macOS styling, but I did not find those font/cursor assets in the checked locations.
    - Kitty’s `open-actions.conf` is omitted despite containing custom Markdown/text-file behavior. [open-actions.conf:1](/home/pi/.config/kitty/open-actions.conf:1).
    - CUPS is enabled and printer configuration files exist, but those files were unreadable and the PPD directory was empty. **Configured printer queues remain unknown.** Capture queue/options recipes where present; regenerate certificates.
    - `/etc/pacman.d/hooks/99-omarchy-limine.hook` is unowned by a package and omitted. The other hook is package-owned and should return through its package. [99-omarchy-limine.hook:1](/etc/pacman.d/hooks/99-omarchy-limine.hook:1).
    - `/etc/udev/rules.d` is empty; pacman reports two Omarchy rule files missing. Record deliberate deletions/masks as well as additions. I cannot establish whether these deletions were intentional.
    - `/etc` also contains changed PAM/SSH configuration, locale, resolver configuration, subordinate-ID mappings, modprobe settings, and boot configuration. “Modified relative to a package” does **not** automatically mean “user customization”; a fresh-Omarchy baseline is needed.
    - No cron setup was found in the inspected locations. The concrete scheduling gaps are system timers and incomplete user-unit state.
    - The TPM/LUKS enrollment, initramfs hooks, bootloader paths, and hardware-specific module settings should become **guarded target-specific recipes**. Never copy disk headers, TPM enrollment, machine IDs, or source disk UUIDs as portable personality.

**Complete `~/.config` omission ledger**

Every entry below was present in the full listing and receives no capture. “Files” means selected authored configuration, excluding caches and credentials.

| Omitted entries | Disposition |
|---|---|
| `bash`, `environment.d`, `mise`, `go`, `gh`, `fabric`, `htop`, `mc`, `tmux`, `yay` | Files plus runtime/tool recipes; separate authentication from `gh`/provider settings. |
| `autostart`, `fcitx`, `fcitx5`, `ibus`, `hyprland-preview-share-picker`, `hyprshell`, `imv`, `menus`, `nwg-dock-hyprland`, `plonk`, `solaar`, `user-dirs.dirs` | Files; install referenced programs; map input devices and user directories on the target. |
| `dconf`, `gtk-3.0` | Semantic settings export plus bookmarks; record font/icon dependencies. |
| `brave-flags.conf`, `chrome-flags.conf`, `chromium-flags.conf`, `browser-harness` | Portable settings/launch recipes; sanitize any harness credentials. |
| `BraveSoftware`, `chromium`, `chromium-headless`, `google-chrome`, `google-chrome-beta`, `google-chrome-for-testing`, `google-chrome-unstable`, `microsoft-edge`, `microsoft-edge-dev`, `mozilla`, `opera`, `vivaldi`, `zen` | Browser installation/profile policy; portable exports and optional encrypted user data. Regenerate caches and unused stubs. |
| `.wrangler`, `Codex`, `Grok Bot`, `Hermes`, `astro`, `herdr`, `opencode`, `subfinder`, `x-api` | Selected settings, project/provider definitions, and installation recipes; secrets separate. |
| `AirPodsTrayApp`, `Omacom`, `apple-notes`, `atmos`, `blip`, `boomux`, `cava`, `cliamp`, `clipsync`, `gochromecast`, `libreoffice`, `mirador`, `obsidian`, `obsidian-second-brain` | Files and application recipes; export meaningful application data. Contents require per-app classification. |
| `omarchy-spotify`, `omarchy-tesla`, `omavoice`, `pa-dlna`, `pulse`, `retroarch`, `rift`, `spotify`, `tensaku`, `uxplayrc`, `voxtype`, `wireplumber`, `xournalpp` | Preferences, audio/input recipes, models and meaningful data; reauthenticate services and regenerate device caches. |
| `incus`, `libvirt`, `rclone`, `tailscale`, `tigervnc`, `wayvnc`, `windows` | Sanitized connection/virtualization recipes; separate credentials and machine identity. |
| `flea-network-recents.json` | Optional private convenience state. |
| `mpv`, `nautilus`, `procps` | Empty in the inspected listing; no current content to preserve. Future authored settings should be discoverable. |
| `mimeapps.list.bak-2026-09-05` | Deliberately exclude the old backup; the active file is captured. |

`~/.local/share` and `~/.local/state` require the same classification. Imprint currently captures only top-level desktop entries from the former and has no general persistent-state collector. Launcher icons/subdirectories are omitted too. [imprint-engine.py:871](/home/pi/Projects/imprint/imprint-engine.py:871).

## 3. IS THE RESTORE A TRUE RECIPE?

**Only partially. It is a fixed sequence of file copies plus a few installation commands. It neither upgrades the machine nor reconstructs the complete installation.**

The actual restore operations are:

| Resource | Current behavior |
|---|---|
| Repository packages | `omarchy pkg add`; installed implementation uses `pacman -S --needed`, without a full upgrade. |
| Foreign packages | Assumed to be AUR packages and sent to `yay`. |
| Git plugins | Clone current default branch through `plugin add`; ignore captured commit and local changes. |
| Git themes | Delete/reclone/apply through `theme install`; ignore captured commit. |
| Local/cloned plugins | Copy packed trees. |
| Local themes | Copy packed trees. |
| Scripts, configs, services, desktop entries | Copy files; rewrite home substrings. |
| Everything else | No operation. |

[imprint-engine.py:1235](/home/pi/Projects/imprint/imprint-engine.py:1235), [imprint-engine.py:1290](/home/pi/Projects/imprint/imprint-engine.py:1290), [imprint-engine.py:1459](/home/pi/Projects/imprint/imprint-engine.py:1459), [omarchy-pkg-add:8](/usr/bin/omarchy-pkg-add:8).

Under the owner’s practical definition—clone sources, install packages, run setup commands—this is a beginning. Under a literal “compile every application from source” definition, it does not qualify: pacman installs built packages, and the inventory includes `*-bin` packages.

**Where copied payload should become installation work**

The current plugin inventory has **42 Git plugins, 17 classified local, and 11 classified cloned**. The latter 28 are packed as trees. Several have identifiable source repositories elsewhere on dex:

- `pi.audio` → `Projects/audio.pulse.omarchy`.
- `pi.backup-monitor` → `Projects/backup.omarchy`.
- Burnbar, Beatdeck, Bluetooth, and other custom plugins likewise have source checkouts.

The classifier only discovers Git metadata at the installed directory or its symlink target. An installation that copied files out of a repository loses its provenance. [imprint-engine.py:302](/home/pi/Projects/imprint/imprint-engine.py:302), [imprint-engine.py:513](/home/pi/Projects/imprint/imprint-engine.py:513).

Those should be recorded as source/build/install recipes. Stock plugin clones should record their base Omarchy revision, destination ID, and local patch. Truly unpublished plugin source can remain an authored-source payload or Git bundle.

**I found no ELF binaries in the currently packed local/clone plugin trees.** The observed problem is copied source trees with lost provenance, plus omitted standalone binaries without reinstall instructions. The collector nevertheless has no general prohibition on packing compiled artifacts inside those trees.

Configuration, personal images, unpublished code, and user data legitimately require payload. A recipe cannot regenerate private notes, photos, database contents, or unpublished work.

**Concrete replacement design**

Implement three separate operations:

```text
imprint capture
imprint plan ARCHIVE --mode upgrade --output PLAN_DIRECTORY
imprint apply PLAN_DIRECTORY [--dry-run | --resume]
```

The plan directory should contain a validated `plan.json`, a readable `restore.sh`, checksummed source/config/data payloads, and a verification specification. These are proposed interfaces, not existing commands.

1. **Discover and classify before claiming a successful capture.**  
   Inventory the machine, compare against a recorded Omarchy baseline, and assign every discovered resource one of: package-managed, source-installed, authored config, persistent data, secret, hardware-bound, disposable, or unresolved. Report inaccessible and unresolved resources explicitly. A failed inventory command must not mean “nothing installed.”

2. **Resolve a complete plan before modifying the target.**  
   Validate schema, metadata types, resource IDs, destination containment, symlinks, hashes, resource limits, and required credentials. Resolve source revisions and dependency availability. Inspect the target’s existing state and produce exact diffs and replacement decisions.

   Preflight must also test native Zstandard support: the engine uses `tarfile`’s `zst` mode, added in Python 3.14; checking for external `zstd` is insufficient on an older target. Dex currently satisfies this requirement. [imprint-engine.py:1116](/home/pi/Projects/imprint/imprint-engine.py:1116), [imprint:32](/home/pi/Projects/imprint/imprint:32), [Python tarfile documentation](https://docs.python.org/3.14/library/tarfile.html).

3. **Execute an explicit dependency graph.**  
   A suitable default sequence is:

   ```text
   target checks and recovery preparation
     → repository/keyring configuration and full Omarchy/system upgrade
     → packages, build dependencies, runtimes
     → source checkout, patches, builds, installation
     → application configuration and persistent-data imports
     → system policies, unit definitions and dependencies
     → theme/font generation, final configuration overlays, wallpaper
     → explicit plugin/layout activation and service activation
     → verification
   ```

   All custom theme hooks must exist before applying themes. Install plugin code before activation. Use explicit state-setting operations rather than blind toggles. Account for installer side effects.

   Arch does not support partial upgrades; a failed full upgrade must block subsequent dependent installation work. [Arch system-maintenance guidance](https://wiki.archlinux.org/title/System_maintenance#Partial_upgrades_are_unsupported).

4. **Make every operation resumable and idempotent.**  
   Each operation needs a stable ID, dependencies, input hashes, desired-state check, apply command, postcondition, privilege scope, timeout, and rollback/compensation description.

   Persist a journal through `pending → running → succeeded/failed/blocked`. On resume, recheck postconditions; do not blindly trust an earlier “succeeded” record. Stage builds and replacements, then atomically install them. Back up each original path once, including its type, link target, mode, and whether it existed.

   Treat package transactions and external effects honestly: file undo cannot reverse all of them. Preserve a coherent system recovery point and report irreversible actions.

5. **Make dry-run describe the same plan that apply executes.**  
   Today’s dry-run omits service-enable operations, abbreviates plugin operations, and conceals theme/install side effects. [imprint-engine.py:1247](/home/pi/Projects/imprint/imprint-engine.py:1247), [imprint-engine.py:1419](/home/pi/Projects/imprint/imprint-engine.py:1419).

   The replacement should show exact commands, source revisions, file diffs, deletions, enable/disable/mask changes, downloads, required authentication, and unmet conditions. Applying a reviewed plan must detect changed inputs or target state.

**Minimum manifest contents**

| Area | Required records |
|---|---|
| Format and integrity | Schema/version constraints, producer revision, archive/resource hashes, sizes, types, optional signature, extraction limits. |
| Source machine and baseline | Architecture, Omarchy version **and source revision/fork**, package baseline, locale/timezone, source username/home, target-mapping rules. |
| Packages | Provider/repository, name, version, installation reason, intentional removals, repository/keyring setup, local PKGBUILD/source provenance. Do not equate foreign packages with AUR. |
| Git/source installations | Sanitized URL, branch, exact commit, destination, submodules/LFS, build/install commands, dependencies, patches, untracked authored files, bundles for unpublished commits. |
| Toolchains | Mise/Bun/Go/Rust/Python/etc. provider, resolved version, global tools, project declarations, installation and validation commands. |
| Plugins/themes/apps | Stable ID, source relationship, stock-clone base revision, install destination, desired enabled state, settings ownership, dependencies, extension/model inventories. |
| Files | Destination, file/directory/symlink type, link target, content hash, permissions/ownership, template variables, overwrite/merge policy, intentional absence. |
| Services and schedules | System/user scope, unit and drop-in definitions, aliases, enabled/disabled/masked state, timer schedules, prerequisites, lingering and startup policy. |
| System configuration | Firewall/network policy, mounts, printing, users/groups, package hooks, login/power policy, hardware applicability predicates and parameter mappings. |
| Data and secrets | Data format/export method, consistency boundary, restore location, encryption policy; secret references and reauthentication steps rather than plaintext credentials. |
| Execution and verification | Operation DAG, preconditions/postconditions, failure policy, journal identifiers, undo metadata, expected capabilities and explicit exclusions. |

Support both **reproduce** and **upgrade** modes. Reproduce uses captured revisions and retained source material. Upgrade resolves newer compatible versions into a new lock manifest and verifies the resulting behavior. “Always clone whatever is latest” cannot reproduce a saved machine reliably.

Verification should check package/runtime versions, source commits and patches, file hashes/types/modes, service enablement and health, Hyprland configuration errors, plugin discovery/layout, theme/font/wallpaper, missing launcher targets, backup scheduling, and imported data. Require concrete regression checks for **OMW-SELECT-COPY, OMW-NO-LOCK, OMW-PLONK-EMPTY, OMW-VOX-ALERT-LOOP, OMW-PLUGIN-ORDER, and OMW-BACKUP-SIZZLE**.

Finally, test a fresh VM with a **different username**, a second application of the same plan, interruption/resume, unavailable sources, dirty existing installations, symlinked plugins, and rollback. Completion should mean every required postcondition passed; otherwise the process must exit nonzero and identify exactly what remains missing.