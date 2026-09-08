## Problem

`imprint save` dies with an unhandled `FileNotFoundError` traceback when a
toolchain binary it probes is absent. `run_ok()` already treats a *non-zero
exit* as "nothing to report" (returns `""`), but a *missing* binary raises
`FileNotFoundError` from `subprocess.run` instead of returning `""`, so a
machine without `cargo` (or `mise`, or `npm`) aborts the whole save.

Fixes #3.

## Root cause

`run_ok()` calls `run()` (a thin wrapper over `subprocess.run`) without
catching `FileNotFoundError`. `collect_toolchains()` calls `run_ok()` for
`mise`, `cargo`, and `npm` unconditionally, so any one of those being absent
turns a routine save into a crash.

## Fix

- `run_ok()` now catches `FileNotFoundError` and returns `""`, matching its
  existing "non-zero exit → empty string" behaviour.
- `collect_toolchains()` guards each toolchain probe with `shutil.which(...)`
  before invoking it. A missing toolchain yields an empty list, and the
  absence is recorded in `meta.json` under `missingBinaries` (e.g.
  `"missingBinaries": ["cargo"]`) so a restore can see what the source machine
  did not have.

## Test evidence

Two new tests in `tests/test_engine.py`:

- `test_run_ok_missing_binary_returns_empty` — `run_ok` on a nonexistent
  binary returns `""`.
- `test_collect_toolchains_without_binaries_still_valid` — with `PATH` stripped
  of `cargo`/`npm`, `collect_toolchains` still returns a valid meta dict with
  empty lists and `missingBinaries` naming the absent tools.

Baseline: 136 tests pass. After the change: 138 tests pass.
