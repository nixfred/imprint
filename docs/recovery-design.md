# Selective configuration recovery: implementation contract

This document records the integration's acceptance contract. Until the linked
CLI tests pass, it is not a claim that the new behavior is implemented.

## Existing architecture

Imprint already stores category payloads and installation recipes in one archive.
The `save`, `verify`, `preview`, `restore`, `plan`, `apply`, and `undo` commands
must remain the entry points. Recovery should strengthen that architecture, not
introduce a competing backup format or copy a private host snapshot into source.

## File mapping and safety

A home payload `categories/CATEGORY/files/RELATIVE_PATH` maps to
`TARGET_HOME/RELATIVE_PATH`. A system payload
`categories/system/etc/RELATIVE_PATH` maps to
`SYSTEM_ROOT/etc/RELATIVE_PATH`. Archive paths never dictate a target home or
system root. The operator chooses those targets explicitly for rehearsals.

Verify archived bytes against SHA-256 before any restore command can execute.
Record file modes and original source provenance as well as sizes. Integrity
is corruption detection, not authenticity: an attacker can rewrite both a
payload and its manifest. Do not run the embedded tool from an untrusted archive.
Archives may carry executable configuration and private data even when the
Secrets category is off. Keep them private and review the chosen payload.

Refuse traversal, special files, unsafe links and symlink ancestors at write
boundaries. Rehearsal targets must not trigger commands against the running
user session. Changes should be staged atomically and validated before rename;
back up existing bytes and modes before overwrite. Undo must distinguish replaced
files from newly created files. This is file recovery, not a transaction over
package installs, Git checkouts, processes, desktop IPC and root policy together.

## Helpers and services

User service collection already includes drop-in directories, including drop-ins
for package-owned units. Do not flatten or discard them. A helper in a custom
location needs an exact operator-reviewed allowlist entry, not recursive copying
of the directory named in an ExecStart or script. Follow no credentials,
EnvironmentFile, cron, sessions, Signal state, or arbitrary dynamic shell paths.
Record which reviewed selected configuration references each helper. References
are evidence, not permission to execute or collect another tree.

Installing a unit or drop-in is distinct from activation:

- `systemctl --user daemon-reload` rereads unit definitions. It does not restart
  an already-running daemon.
- `systemctl --user enable NAME` configures future startup. It does not start
  the daemon now.
- `systemctl --user start NAME` starts a stopped unit.
- `systemctl --user restart NAME` interrupts and replaces its process.
- `systemctl --root=ROOT enable NAME` changes offline enablement in ROOT.
  It does not activate a daemon in the current host.

Report restart-required and manual dependencies honestly. Never infer successful
runtime activation merely from an installed file. Disabled monitoring jobs must
stay disabled: recovering their helper is not authorization to schedule them.

## Shell-specific restoration

Never replace a running shell's whole configuration with an archived snapshot.
Capture `omarchy shell config-edit snapshot BASE`, make EDITED from that fresh
BASE, merge only explicitly selected archived settings, and apply with
`omarchy shell config-edit apply BASE EDITED`. Preserve all live layout arrays
and their order unless the operator separately requests a layout change. A stale
BASE refusal is a conflict, not permission to retry with force. Re-read and
reconsider the requested fields. Do not use `omarchy refresh`, immutable files,
or an automatic shell restart as a substitute for persistence verification.

## Required evidence

Run the real CLI in disposable targets in the dedicated test VM:

1. Save synthetic selected configuration, custom helper and service drop-ins.
2. Verify archive integrity; reject damaged and unsafe archives.
3. Preview exact selected path mappings, byte diffs and mode changes.
4. Restore into isolated home and system root; verify bytes and modes.
5. Exercise undo, preflight failures and rollback after an actual write failure.
6. Trace supported activation commands with deterministic command fixtures and
   separately test offline systemd enablement with real `systemctl --root`.
7. Prove that no fixture can reach the live host or enable paused monitoring.

A passing rehearsal is not bare-metal recovery. OS installation, packages not
covered by recipes, hardware compatibility, bootloader/initramfs, disk encryption,
TPM access, credentials, service readiness and a desktop session still require
separate recovery procedures and validation.
