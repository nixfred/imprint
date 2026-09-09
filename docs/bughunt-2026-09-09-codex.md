# Bug hunt — 2026-09-09

Produced by OpenAI Codex (codex-cli 0.153.4, gpt-6-astra, reasoning xhigh)
against commit `01bd8fd`, read-only sandbox, no files modified. It was given
`docs/audit-2026-09-06-codex.md` and told not to repeat it.

Findings 1 and 2 were independently re-read in the source before this file was
written and are real as described; the rest are recorded as Codex reported
them, including its own distinction between what it reproduced and what it
reasoned about. Nothing here is fixed yet.

Reviewed **01bd8fd**, excluding the prior audit’s findings. **No files changed.** Bash syntax checking and 28 existing tests that require no filesystem writes passed; I did not run the write-heavy full suite.

“Reproduced with mocks” below means the relevant code ran with filesystem mutations or failures simulated. No real restore, disk fill, or unmount was performed.

1. **Critical — Plugin IDs inject commands into generated plans.**  
   `safe_segment()` accepts `demo$(printf INJECTED >&2)`, which `plan_plugins()` inserts unquoted into shell commands. Applying such a plan executes the substitution as the restoring user. **Reproduced:** generating and executing the affected commands with `mkdir`/`cp` stubbed printed `INJECTED` twice. This is a new generated-shell vulnerability, separate from the prior traversal finding.  
   [imprint-engine.py:752](/home/pi/Projects/imprint/imprint-engine.py:752), [imprint-engine.py:3929](/home/pi/Projects/imprint/imprint-engine.py:3929), [imprint-engine.py:3964](/home/pi/Projects/imprint/imprint-engine.py:3964).

2. **High — Interactive selections become whole-category operations.**  
   Select one plugin and deselect others. `only=$(choose_categories ...)` runs the picker function in a subshell, so its assignment to `SELECTION_FILE` disappears. Save, preview, and restore receive `--only plugins` without `--select`. This can restore plugins the user explicitly excluded, including applying whole-category enable/disable reconciliation. **Reproduced with mocked picker/engine:** all three engine calls lacked `--select`.  
   [imprint:177](/home/pi/Projects/imprint/imprint:177), [imprint:310](/home/pi/Projects/imprint/imprint:310), [imprint:341](/home/pi/Projects/imprint/imprint:341), [imprint:369](/home/pi/Projects/imprint/imprint:369).

3. **High — A “skipped” file can remain partially present in the archive.**  
   If a source read fails with `EIO` after copying some bytes, `try_copy()` records the failure but leaves the destination behind. `write_archive()` packs that partial file; restore does not exclude manifest-listed skipped paths. The warning says the file is absent, but restoring can overwrite a good file with its truncated payload. **Reproduced using real copying into memory:** `try_copy()` returned false while destination bytes remained `PREFIX`, with no cleanup attempt. Archive/restore consequences follow from inspection.  
   [imprint-engine.py:807](/home/pi/Projects/imprint/imprint-engine.py:807), [imprint-engine.py:2448](/home/pi/Projects/imprint/imprint-engine.py:2448), [imprint-engine.py:2718](/home/pi/Projects/imprint/imprint-engine.py:2718).

4. **High — Home-path rewriting can corrupt a restored file and report success.**  
   Restore between different home paths, then encounter an I/O or space error during `write_text()` after it truncates the copied file. `rewrite_in_place()` suppresses the error; `restore_file_tree()` records success. **Reproduced with fault injection:** the simulated destination became empty, the returned action was the filename, and `FAILURES` remained empty.  
   [imprint-engine.py:850](/home/pi/Projects/imprint/imprint-engine.py:850), [imprint-engine.py:2748](/home/pi/Projects/imprint/imprint-engine.py:2748).

5. **High — Unreadable directories disappear without any skipped-file report.**  
   For example, save `configs` with an unreadable `~/.config/myapp` directory. `os.walk()` has no `onerror` callback, so directory enumeration failures silently omit the subtree. Individual `lstat()` failures are also silently filtered before `try_copy()`. **Reproduced:** injecting `EACCES` into `scandir()` returned no files and left `SKIPPED=[]`.  
   [imprint-engine.py:859](/home/pi/Projects/imprint/imprint-engine.py:859), [imprint-engine.py:873](/home/pi/Projects/imprint/imprint-engine.py:873).

6. **High — System and stock-theme collectors swallow staging-disk exhaustion.**  
   Their direct-copy loops catch `ENOSPC` and `EDQUOT` without invoking the fatal staging handler. Stock themes record no skip; system files are mislabeled “unreadable.” If space becomes available before subsequent metadata writes, an incomplete save can finish. A persistently full disk may instead fail later, so this is not a claim that every disk-full run succeeds. **Reproduced with mocks:** both collectors returned normally for both errors; `SKIPPED` stayed empty.  
   [imprint-engine.py:1434](/home/pi/Projects/imprint/imprint-engine.py:1434), [imprint-engine.py:1855](/home/pi/Projects/imprint/imprint-engine.py:1855), [imprint-engine.py:1864](/home/pi/Projects/imprint/imprint-engine.py:1864).

7. **High — Planning deletes an existing `payload` directory before extraction succeeds.**  
   `imprint plan ARCHIVE -o EXISTING_DIR` recursively removes `EXISTING_DIR/payload` without checking ownership. A particularly destructive input is an archive stored inside that directory: planning deletes its own input, then fails to extract it. **Reproduced with mocked deletion/extraction:** deletion happened before the extraction failure.  
   [imprint-engine.py:4207](/home/pi/Projects/imprint/imprint-engine.py:4207).

8. **High — Generated `restore.sh` does not enforce its advertised per-step `set -e`.**  
   Each step runs inside `if ! ( set -e … )`, where Bash suppresses errexit. An earlier failed copy can therefore be masked by a later successful command. **Reproduced with the actual renderer:** a step containing `false` followed by a successful `printf` continued, printed “plan applied cleanly,” and exited zero. This concerns generated scripts, independently of `cmd_restore()` reporting.  
   [imprint-engine.py:4114](/home/pi/Projects/imprint/imprint-engine.py:4114).

9. **High — Explicitly captured stock themes are never restored.**  
   Engine save with a selection file choosing a stock theme stores it under `categories/themes/stock/NAME`. Restore handles theme URLs and the category’s `files/` tree, but never `stock/`. The selected theme therefore produces no installation action. **Reproduced dispatch with mocked metadata;** the payload omission follows from the collector/restore paths.  
   [imprint-engine.py:1430](/home/pi/Projects/imprint/imprint-engine.py:1430), [imprint-engine.py:3107](/home/pi/Projects/imprint/imprint-engine.py:3107), [imprint-engine.py:3127](/home/pi/Projects/imprint/imprint-engine.py:3127).

10. **Medium — Several single-file failures still abort an entire run.**  
    An unreadable `shell.json` is first recorded by `try_copy()`, then reread without catching `OSError`, aborting save. Plugin payloads, overlays, and bundled units also bypass `try_copy()`. **Reproduced:** unreadable bar configuration and one unreadable local-plugin file both propagated `PermissionError`. On restore, `overlay_tree()` similarly bypasses the per-file recovery logic; that portion is **reasoned from inspection**.  
    [imprint-engine.py:1122](/home/pi/Projects/imprint/imprint-engine.py:1122), [imprint-engine.py:1321](/home/pi/Projects/imprint/imprint-engine.py:1321), [imprint-engine.py:1341](/home/pi/Projects/imprint/imprint-engine.py:1341), [imprint-engine.py:1372](/home/pi/Projects/imprint/imprint-engine.py:1372), [imprint-engine.py:3069](/home/pi/Projects/imprint/imprint-engine.py:3069).

11. **Medium — Relative save destinations are remembered relative to future working directories.**  
    Save from directory A using `-o backups/one.tar.zst`, then run save from directory B, which also contains `backups/`. The next archive silently goes to B’s directory. Even `-o one.tar.zst` remembers `"."`. **Reproduced:** `remember_save_dir(Path("docs"))` stored `"docs"` and the default resolver returned it unchanged; the cross-directory consequence is established by inspection.  
    [imprint-engine.py:2654](/home/pi/Projects/imprint/imprint-engine.py:2654), [imprint-engine.py:2582](/home/pi/Projects/imprint/imprint-engine.py:2582), [imprint-engine.py:2596](/home/pi/Projects/imprint/imprint-engine.py:2596).

12. **Medium — An unmounted remembered destination can silently receive a local backup.**  
    Save to a user-owned mountpoint, unmount it while leaving its underlying directory present and writable, then save again. `is_dir()` still passes; there is no mount/storage identity check and no fallback warning. **Reproduced resolver behavior with mocks; no actual unmount performed.**  
    [imprint-engine.py:2597](/home/pi/Projects/imprint/imprint-engine.py:2597).

13. **Medium — `--system-root` violates the user-named-directory rule.**  
    Run the engine’s `restore ARCHIVE --only system --allow-system --system-root /missing/typed/root`. It calls `mkdir(parents=True)` on that exact user input. No `need_dir()` validation occurs. **Reproduced with mocked mutations:** `/missing/typed/root` was requested for creation.  
    [imprint-engine.py:3451](/home/pi/Projects/imprint/imprint-engine.py:3451), [imprint-engine.py:5155](/home/pi/Projects/imprint/imprint-engine.py:5155).

14. **Medium — Ctrl-C during archive writing leaves `.partial` behind.**  
    Interrupt packing after the partial archive opens. Cleanup catches only `OSError`; `KeyboardInterrupt` escapes to the top-level handler, which prints “cancelled” and exits 130. **Reproduced with an interrupted tar writer:** zero cleanup-unlink calls.  
    [imprint-engine.py:2450](/home/pi/Projects/imprint/imprint-engine.py:2450), [imprint-engine.py:5231](/home/pi/Projects/imprint/imprint-engine.py:5231).

15. **Medium — Successful interactive operations return failure status.**  
    With `SELECTION_FILE` empty, the final `[[ -n $SELECTION_FILE ]] && rm …` returns 1. That becomes `do_save()`/`do_restore()`’s status and triggers the surrounding `set -e`. The subshell bug makes this the normal picker outcome. **Reproduced with successful mocked engine calls:** both wrapper functions exited 1.  
    [imprint:343](/home/pi/Projects/imprint/imprint:343), [imprint:400](/home/pi/Projects/imprint/imprint:400).

16. **Medium — Archive discovery corrupts valid filenames.**  
    A newline-containing archive name becomes multiple picker entries because discovery uses newline-delimited `ls` output. `nearby_archives()` additionally splits spaces and expands glob characters through unquoted `$found`. A remembered relative directory named `-drive` also breaks discovery because `ls` lacks `--`. **Reproduced with mocked listings;** the real `ls` rejected `-drive/imprint-one.tar.zst` as an option.  
    [imprint:200](/home/pi/Projects/imprint/imprint:200), [imprint:224](/home/pi/Projects/imprint/imprint:224), [imprint:407](/home/pi/Projects/imprint/imprint:407).

17. **Medium — Invalid UTF-8 state can break saving despite state being optional.**  
    If `state.json` contains invalid UTF-8, `read_state()` raises `UnicodeDecodeError`. Default-output/default-save fail immediately; explicit-output save reaches this failure after writing its archive, when remembering the destination. Neither the state handler nor top-level `OSError` handler catches it. **Reproduced the escaping decode error with a mocked read;** post-save timing follows from inspection.  
    [imprint-engine.py:2563](/home/pi/Projects/imprint/imprint-engine.py:2563), [imprint-engine.py:2654](/home/pi/Projects/imprint/imprint-engine.py:2654).

18. **Medium — More than 200 skipped paths lose their persistent identities.**  
    With 201 unreadable files, the manifest retains only 200, while stderr promises they are “all listed in the manifest.” The interactive wrapper discards the full JSON result, so later skipped filenames are unavailable from either the archive or normal terminal report. **Reproduced:** the 201st path was absent while the message still promised complete listing.  
    [imprint-engine.py:2644](/home/pi/Projects/imprint/imprint-engine.py:2644), [imprint-engine.py:2673](/home/pi/Projects/imprint/imprint-engine.py:2673), [imprint:341](/home/pi/Projects/imprint/imprint:341).

Sound in the exercised paths: ordinary missing save/plan/input paths are refused without creation; a genuinely missing remembered directory falls back with a warning; empty `where=()` and the optional `--select` expansion preserve arguments correctly on the installed Bash; conditional picker calls tolerate unmatched search globs.
