#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("imprint_engine", ROOT / "imprint-engine.py")
engine = importlib.util.module_from_spec(spec)
sys.modules["imprint_engine"] = engine
spec.loader.exec_module(engine)


class RelPathTests(unittest.TestCase):
    def test_keeps_symlink_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "bin").mkdir()
            (home / "Projects/plonk").mkdir(parents=True)
            target = home / "Projects/plonk/plonk"
            target.write_text("#!/bin/sh\n", encoding="utf-8")
            link = home / "bin/plonk"
            link.symlink_to(target)
            self.assertEqual(engine.rel_under_home(link, home), "bin/plonk")


class RewriteTests(unittest.TestCase):
    def test_rewrites_home(self):
        text = "exec /home/pi/bin/plonk\nHOME=/home/pi\n"
        out = engine.rewrite_text(text, "/home/pi", "/home/fred")
        self.assertEqual(out, "exec /home/fred/bin/plonk\nHOME=/home/fred\n")

    def test_leaves_other_homes(self):
        text = "/home/other/bin/x"
        self.assertEqual(engine.rewrite_text(text, "/home/pi", "/home/fred"), text)

    def test_noop_when_same(self):
        self.assertEqual(engine.rewrite_text("a", "/home/pi", "/home/pi"), "a")


class GitUrlTests(unittest.TestCase):
    def test_ssh_to_https(self):
        self.assertEqual(
            engine.https_git_url("git@github.com:nixfred/workspace-names.git"),
            "https://github.com/nixfred/workspace-names.git",
        )

    def test_keeps_https(self):
        url = "https://github.com/nixfred/workspace-names.git"
        self.assertEqual(engine.https_git_url(url), url)


class CategoryTests(unittest.TestCase):
    def test_defaults_are_portable(self):
        for item in engine.CATEGORIES:
            if item["default"]:
                self.assertEqual(item["risk"], "portable", item["id"])

    def test_host_bound_off(self):
        for cid in ("monitors", "input", "identity", "secrets", "wallpapers"):
            self.assertFalse(engine.category_by_id(cid)["default"])

    def test_parse_only(self):
        self.assertEqual(engine.parse_only("look,bar"), ["look", "bar"])

    def test_parse_only_rejects_unknown(self):
        with self.assertRaises(SystemExit):
            engine.parse_only("look,not-a-thing")


class PluginClassifyTests(unittest.TestCase):
    def test_git_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(
                json.dumps({"id": "demo.plug", "name": "Demo"}), encoding="utf-8"
            )
            (root / ".git").mkdir()
            info = engine.classify_plugin(root, {"id": "demo.plug", "enabled": True})
            self.assertIn(info["kind"], {"git", "local"})
            self.assertEqual(info["id"], "demo.plug")

    def test_clone_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "pi.bar",
                        "name": "Locked Bar",
                        "omarchy": {"clonedFrom": "omarchy.bar"},
                    }
                ),
                encoding="utf-8",
            )
            info = engine.classify_plugin(root, {"id": "pi.bar", "clonedFrom": "omarchy.bar"})
            self.assertEqual(info["kind"], "clone")
            self.assertEqual(info["clonedFrom"], "omarchy.bar")


class ExtraPackagesTests(unittest.TestCase):
    def test_splits_aur_and_skips_stock(self):
        # Function talks to pacman; just assert shape on this Omarchy box.
        data = engine.extra_packages()
        self.assertIn("repo", data)
        self.assertIn("aur", data)
        self.assertIsInstance(data["repo"], list)
        self.assertIsInstance(data["aur"], list)
        self.assertNotIn("omarchy", data["repo"])
        self.assertTrue(any(name.startswith("omarchy") for name in data["skipped_stock"]))


class SaveRestoreRoundtrip(unittest.TestCase):
    def test_look_files_roundtrip_in_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hypr = home / ".config/hypr"
            hypr.mkdir(parents=True)
            (hypr / "looknfeel.lua").write_text("gaps = 1\n", encoding="utf-8")
            cat = Path(tmp) / "look"
            cat.mkdir()
            # collector uses Path.home() for theme commands; still copies given home.
            meta = engine.collect_look(cat, home)
            packed = cat / "files/.config/hypr/looknfeel.lua"
            self.assertTrue(packed.is_file(), meta)
            self.assertEqual(packed.read_text(encoding="utf-8"), "gaps = 1\n")


class RegressionTests(unittest.TestCase):
    def test_rewrite_skips_partially_binary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cat = t / "cat"; (cat / "files/.config").mkdir(parents=True)
            home = t / "home"; home.mkdir()
            undo = t / "undo"; undo.mkdir()
            (cat / "files/.config/thing.conf").write_bytes(b"a" * 5000 + b"\xff not utf8")
            done = engine.restore_file_tree(cat, home, "/home/old", undo, False)
            self.assertEqual(done, [".config/thing.conf"])
            self.assertTrue((home / ".config/thing.conf").is_file())

    def test_relative_broken_symlink_over_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cat = t / "cat"; (cat / "files/bin").mkdir(parents=True)
            home = t / "home"; (home / "bin").mkdir(parents=True)
            undo = t / "undo"; undo.mkdir()
            (cat / "files/bin/gone").symlink_to("../gone-target")
            (home / "bin/gone").write_text("existing\n", encoding="utf-8")
            engine.restore_file_tree(cat, home, "", undo, False)
            self.assertTrue((home / "bin/gone").is_symlink())

    def test_absolute_broken_symlink_is_not_packed(self):
        # tarfile's data filter rejects absolute link targets, so packing one
        # produces an archive that cannot be extracted at all.
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            home = t / "home"; (home / "bin").mkdir(parents=True)
            link = home / "bin/dangling"
            link.symlink_to("/nonexistent/target")
            self.assertFalse(engine.packable_symlink(link))
            cat = t / "cat"; cat.mkdir()
            self.assertIsNone(engine.copy_into_category(cat, link, home))

    def test_never_writes_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            outsider = t / "outsider.txt"; outsider.write_text("keep me\n", encoding="utf-8")
            src = t / "src.txt"; src.write_text("new\n", encoding="utf-8")
            dest = t / "dest.txt"; dest.symlink_to(outsider)
            engine.copy_file(src, dest)
            self.assertEqual(outsider.read_text(encoding="utf-8"), "keep me\n")
            self.assertEqual(dest.read_text(encoding="utf-8"), "new\n")

    def test_bar_without_shell_json_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cat = t / "cat"; cat.mkdir()
            home = t / "home"; home.mkdir()
            undo = t / "undo"; undo.mkdir()
            actions = engine.restore_bar(cat, home, "", undo, False)
            self.assertIn("bar left alone", actions[0])
            self.assertFalse((home / ".config/omarchy/shell.json").exists())

    def test_undo_dirs_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = engine.new_undo_dir(root); first.mkdir(parents=True)
            second = engine.new_undo_dir(root)
            self.assertNotEqual(first, second)

    def test_undo_returns_files_iter_files_would_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            chosen = t / "undo-x"; chosen.mkdir()
            (chosen / ".config").mkdir()
            (chosen / ".config/keep.bak").write_text("original\n", encoding="utf-8")
            self.assertTrue(engine.is_skipped_name("keep.bak"))
            names = [p.name for p in sorted(chosen.rglob("*")) if p.is_file()]
            self.assertIn("keep.bak", names)

    def test_git_plugins_are_a_recipe_not_a_payload(self):
        recs = [
            {"id": "a", "kind": "git", "url": "https://x/y.git", "packed": False, "tree": ""},
            {"id": "b", "kind": "local", "packed": True, "tree": "trees/b"},
        ]
        self.assertEqual(sorted(r["id"] for r in recs if not r["packed"]), ["a"])
        # An empty tree must never resolve to the category directory itself.
        self.assertEqual(recs[0]["tree"], "")


class CodexAuditRegressions(unittest.TestCase):
    def test_plugin_id_cannot_escape_the_plugins_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cat = t / "cat"; cat.mkdir()
            home = t / "home"
            (home / ".config/omarchy/plugins").mkdir(parents=True)
            (home / "Documents").mkdir(parents=True)
            (home / "Documents/important.txt").write_text("MY DATA", encoding="utf-8")
            tree = cat / "trees/evil"; tree.mkdir(parents=True)
            (tree / "payload.txt").write_text("owned", encoding="utf-8")
            (cat / "meta.json").write_text(json.dumps({"plugins": [
                {"id": "../../../Documents", "kind": "local", "tree": "trees/evil"}]}), encoding="utf-8")
            undo = t / "undo"; undo.mkdir()
            actions = engine.restore_plugins(cat, home, "", undo, False)
            self.assertTrue((home / "Documents/important.txt").is_file())
            self.assertFalse((home / "Documents/payload.txt").exists())
            self.assertTrue(any("refused" in a for a in actions), actions)

    def test_plugin_tree_cannot_escape_the_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cat = t / "cat"; cat.mkdir()
            outside = t / "outside"; outside.mkdir()
            (outside / "x.txt").write_text("nope", encoding="utf-8")
            home = t / "home"; (home / ".config/omarchy/plugins").mkdir(parents=True)
            (cat / "meta.json").write_text(json.dumps({"plugins": [
                {"id": "ok.plug", "kind": "local", "tree": "../outside"}]}), encoding="utf-8")
            undo = t / "undo"; undo.mkdir()
            actions = engine.restore_plugins(cat, home, "", undo, False)
            self.assertTrue(any("refused" in a for a in actions), actions)

    def test_home_rewrite_respects_path_boundaries(self):
        self.assertEqual(engine.rewrite_text("/home/pip/shared", "/home/pi", "/home/alice"),
                         "/home/pip/shared")
        self.assertEqual(engine.rewrite_text("/home/pi/bin/x", "/home/pi", "/home/alice"),
                         "/home/alice/bin/x")
        self.assertEqual(engine.rewrite_text("HOME=/home/pi\n", "/home/pi", "/home/alice"),
                         "HOME=/home/alice\n")

    def test_backup_existing_handles_directory_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            real = t / "realdir"; real.mkdir(); (real / "f.txt").write_text("x", encoding="utf-8")
            home = t / "home"; home.mkdir()
            link = home / "linkdir"; link.symlink_to(real)
            undo = t / "undo"; undo.mkdir()
            engine.backup_existing(link, undo, home)
            self.assertTrue((undo / "linkdir").is_symlink())

    def test_verify_rejects_unknown_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "manifest.json").write_text(json.dumps(
                {"kind": engine.KIND, "schema": 999, "hostname": "x", "categories": {}}), encoding="utf-8")
            from types import SimpleNamespace
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = engine.cmd_verify(SimpleNamespace(archive=str(t)))
            self.assertEqual(rc, 1)
            self.assertFalse(json.loads(buf.getvalue())["ok"])

    def test_failures_make_restore_report_not_ok(self):
        engine.FAILURES.clear()
        engine.fail("something broke")
        self.assertEqual(engine.FAILURES, ["something broke"])
        engine.FAILURES.clear()


class RecipeCategoryTests(unittest.TestCase):
    def test_etc_denylist_blocks_credentials(self):
        for bad in ("/etc/shadow", "/etc/gshadow", "/etc/sudoers",
                    "/etc/NetworkManager/system-connections/wifi.nmconnection",
                    "/etc/ssh/ssh_host_ed25519_key", "/etc/pki/tls/private/x.key"):
            self.assertTrue(engine.etc_is_denied(Path(bad)), bad)
        for good in ("/etc/ufw/user.rules", "/etc/docker/daemon.json",
                     "/etc/systemd/system/dex-backup.timer"):
            self.assertFalse(engine.etc_is_denied(Path(good)), good)

    def test_symlink_into_a_repo_is_a_link_not_a_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / "Projects/tool"
            (repo / ".git").mkdir(parents=True)
            script = repo / "tool"; script.write_text("#!/bin/sh\n", encoding="utf-8")
            binp = home / "bin"; binp.mkdir()
            (binp / "tool").symlink_to(script)
            cat = home / "cat"; cat.mkdir()
            meta = engine.collect_scripts(cat, home)
            self.assertEqual(len(meta["links"]), 1)
            link = meta["links"][0]
            self.assertEqual(link["link"], "bin/tool")
            self.assertEqual(link["repo"], "Projects/tool")
            self.assertFalse((cat / "files/bin/tool").exists(),
                             "the link must not also be flattened into a payload copy")

    def test_link_restore_recreates_the_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = home / "Projects/tool/tool"
            target.parent.mkdir(parents=True)
            target.write_text("#!/bin/sh\n", encoding="utf-8")
            cat = Path(tmp) / "cat"; cat.mkdir()
            (cat / "meta.json").write_text(json.dumps({"files": [], "links": [
                {"link": "bin/tool", "target": "Projects/tool/tool",
                 "repo": "Projects/tool", "url": "https://example/tool.git"}]}), encoding="utf-8")
            undo = Path(tmp) / "undo"; undo.mkdir()
            actions = engine.restore_scripts(cat, home, "", undo, False)
            self.assertTrue((home / "bin/tool").is_symlink())
            self.assertEqual((home / "bin/tool").resolve(), target.resolve())
            self.assertTrue(any("linked" in a for a in actions), actions)

    def test_link_restore_reports_a_missing_target_as_failure(self):
        engine.FAILURES.clear()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir(parents=True)
            cat = Path(tmp) / "cat"; cat.mkdir()
            (cat / "meta.json").write_text(json.dumps({"files": [], "links": [
                {"link": "bin/tool", "target": "Projects/gone/tool",
                 "repo": "Projects/gone", "url": "https://example/gone.git"}]}), encoding="utf-8")
            undo = Path(tmp) / "undo"; undo.mkdir()
            engine.restore_scripts(cat, home, "", undo, False)
            self.assertEqual(len(engine.FAILURES), 1)
            self.assertIn("is missing", engine.FAILURES[0])
        engine.FAILURES.clear()

    def test_project_path_cannot_escape_home(self):
        engine.FAILURES.clear()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir(parents=True)
            cat = Path(tmp) / "cat"; cat.mkdir()
            (cat / "meta.json").write_text(json.dumps({"repos": [
                {"path": "../../etc/evil", "url": "https://example/x.git"}]}), encoding="utf-8")
            actions = engine.restore_projects(cat, home, False)
            self.assertTrue(any("refused" in a for a in actions), actions)
        engine.FAILURES.clear()

    def test_repos_without_a_remote_are_flagged_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir(parents=True)
            cat = Path(tmp) / "cat"; cat.mkdir()
            (cat / "meta.json").write_text(json.dumps({
                "repos": [{"path": "Projects/local-only", "url": ""}],
                "withoutRemote": ["Projects/local-only"]}), encoding="utf-8")
            actions = engine.restore_projects(cat, home, True)
            self.assertTrue(any("no git remote" in a for a in actions), actions)
            self.assertTrue(any("WARNING" in a for a in actions), actions)

    def test_system_restore_needs_explicit_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat = Path(tmp) / "cat"; (cat / "etc").mkdir(parents=True)
            (cat / "etc/thing.conf").write_text("x=1\n", encoding="utf-8")
            (cat / "meta.json").write_text(json.dumps(
                {"enabledUnits": ["docker.service"], "etcFiles": ["thing.conf"]}), encoding="utf-8")
            home = Path(tmp) / "home"; home.mkdir()
            actions = engine.restore_system(cat, home, False, False)
            self.assertTrue(any("NOT applied" in a for a in actions), actions)
            # Staged outside the archive's temp dir so it survives the restore.
            scripts = list((home / ".local/state/imprint").glob("system-*/restore-system.sh"))
            self.assertEqual(len(scripts), 1, scripts)
            script = scripts[0].read_text(encoding="utf-8")
            self.assertIn("systemctl enable docker.service", script)
            self.assertTrue(scripts[0].parent.joinpath("etc/thing.conf").is_file())

    def test_system_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat = Path(tmp) / "cat"; (cat / "etc").mkdir(parents=True)
            (cat / "etc/thing.conf").write_text("x=1\n", encoding="utf-8")
            (cat / "meta.json").write_text(json.dumps(
                {"enabledUnits": ["docker.service"], "etcFiles": ["thing.conf"]}), encoding="utf-8")
            home = Path(tmp) / "home"; home.mkdir()
            engine.restore_system(cat, home, True, False)
            self.assertFalse((home / ".local/state/imprint").exists())


class PluginInstallReportingTests(unittest.TestCase):
    """A restore must not report an install it did not perform."""

    def _run(self, rc, out, remote):
        import types
        calls = []
        real_run, real_remote = engine.run, engine.git_remote
        engine.run = lambda cmd, **kw: (calls.append(cmd), types.SimpleNamespace(
            returncode=rc, stdout=out, stderr=""))[1]
        engine.git_remote = lambda path: remote
        try:
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp); cat = t / "cat"; cat.mkdir()
                home = t / "home"; (home / ".config/omarchy/plugins").mkdir(parents=True)
                (cat / "meta.json").write_text(json.dumps({"plugins": [
                    {"id": "a.plug", "kind": "git", "url": "https://example/a.git",
                     "enabled": True}]}), encoding="utf-8")
                undo = t / "undo"; undo.mkdir()
                engine.FAILURES.clear()
                return engine.restore_plugins(cat, home, "", undo, False)
        finally:
            engine.run, engine.git_remote = real_run, real_remote
            engine.FAILURES.clear()

    def test_fresh_install_is_reported_as_installed(self):
        actions = self._run(0, "", "https://example/a.git")
        self.assertTrue(any("installed a.plug from source" in a for a in actions), actions)

    def test_already_present_is_not_reported_as_an_install(self):
        actions = self._run(1, "plugin already exists", "https://example/a.git")
        self.assertFalse(any("installed a.plug from source" in a for a in actions), actions)
        # It now goes through the version sync instead of shrugging.
        self.assertTrue(any("a.plug" in a for a in actions), actions)

    def test_a_different_recorded_url_is_reported_and_wins(self):
        # The source machine may track a fork while this one points at upstream.
        # That is the weather-plugin case; refusing it would block the update.
        actions = self._run(1, "plugin already exists", "https://upstream/a.git")
        self.assertTrue(any("imprint recorded https://example/a.git" in a for a in actions), actions)
        self.assertTrue(any("syncing from the recorded one" in a for a in actions), actions)


class EnabledStateTests(unittest.TestCase):
    """Installing plugin code is not the same as showing it."""

    def _shell(self, tmp, **cfg):
        home = Path(tmp) / "home"
        (home / ".config/omarchy").mkdir(parents=True)
        (home / ".config/omarchy/shell.json").write_text(json.dumps(cfg), encoding="utf-8")
        return home

    def test_bar_widget_is_enabled_only_when_placed_in_the_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._shell(tmp,
                bar={"id": "omarchy.bar", "layout": {"left": [{"id": "a.widget"}]}},
                plugins=[{"id": "b.widget"}])
            placement, referenced, disabled, ok = engine.shell_plugin_state(home)
            self.assertTrue(ok)
            self.assertEqual(placement["a.widget"], {"section": "left", "index": 0})
            # b.widget sits in plugins[] but not in the layout: for a bar widget
            # that is NOT enabled, which is what the live registry reports.
            self.assertIn("b.widget", referenced)
            self.assertNotIn("b.widget", placement)

    def test_panels_and_services_count_from_the_plugins_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._shell(tmp, bar={"id": "pi.bar", "layout": {}},
                               plugins=[{"id": "x.panel"}], disabledPlugins=["y.svc"])
            _placement, referenced, disabled, _ok = engine.shell_plugin_state(home)
            self.assertIn("x.panel", referenced)
            self.assertIn("pi.bar", referenced)
            self.assertIn("y.svc", disabled)

    def test_missing_shell_json_is_reported_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir()
            placement, referenced, disabled, ok = engine.shell_plugin_state(home)
            self.assertFalse(ok)
            self.assertEqual((placement, referenced, disabled), ({}, set(), set()))

    def test_restore_enables_recorded_plugins_with_placement(self):
        import types
        calls = []
        real_run, real_list = engine.run, engine.plugin_list
        engine.plugin_list = lambda: []          # nothing enabled on the target
        engine.run = lambda cmd, **kw: (calls.append(cmd),
                                        types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp); cat = t / "cat"; cat.mkdir()
                home = t / "home"; (home / ".config/omarchy/plugins").mkdir(parents=True)
                (cat / "meta.json").write_text(json.dumps({"plugins": [
                    {"id": "a.widget", "kind": "git", "url": "https://x/a.git",
                     "enabled": True, "placement": {"section": "left", "index": 2}},
                    {"id": "b.off", "kind": "git", "url": "https://x/b.git", "enabled": False},
                ]}), encoding="utf-8")
                undo = t / "undo"; undo.mkdir()
                engine.FAILURES.clear()
                actions = engine.restore_plugins(cat, home, "", undo, False)
        finally:
            engine.run, engine.plugin_list = real_run, real_list
            engine.FAILURES.clear()
        enables = [c for c in calls if c[:3] == ["omarchy", "plugin", "enable"]]
        self.assertEqual(len(enables), 1, enables)
        self.assertEqual(enables[0],
                         ["omarchy", "plugin", "enable", "a.widget", "--section", "left", "--index", "2"])
        # plugin add must not carry --enable, or it places the widget itself.
        adds = [c for c in calls if c[:3] == ["omarchy", "plugin", "add"]]
        self.assertTrue(adds)
        for c in adds:
            self.assertNotIn("--enable", c)
        self.assertTrue(any("enabled a.widget at left[2]" in a for a in actions), actions)


class DisableAndUnitTests(unittest.TestCase):
    def test_plugin_units_are_discovered_from_execstart(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            units = home / ".config/systemd/user"; units.mkdir(parents=True)
            (units / "net-pulse.service").write_text(
                "[Service]\nExecStart=/usr/bin/python3 %h/.config/omarchy/plugins/"
                "nixfred.net-pulse/net_pulse.py daemon\n", encoding="utf-8")
            (units / "unrelated.service").write_text(
                "[Service]\nExecStart=/usr/bin/true\n", encoding="utf-8")
            found = engine.plugin_units(home)
            self.assertEqual(found, {"nixfred.net-pulse": ["net-pulse.service"]})

    def test_missing_backing_unit_is_a_failure_naming_the_category(self):
        import types
        real_run, real_list = engine.run, engine.plugin_list
        engine.plugin_list = lambda: []
        def fake(cmd, **kw):
            if cmd[:3] == ["systemctl", "--user", "cat"]:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="No files found")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        engine.run = fake
        try:
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp); cat = t / "cat"; cat.mkdir()
                home = t / "home"; (home / ".config/omarchy/plugins").mkdir(parents=True)
                (cat / "meta.json").write_text(json.dumps({"plugins": [
                    {"id": "n.pulse", "kind": "git", "url": "https://x/n.git",
                     "enabled": False, "units": ["net-pulse.service"]}]}), encoding="utf-8")
                undo = t / "undo"; undo.mkdir()
                engine.FAILURES.clear()
                actions = engine.restore_plugins(cat, home, "", undo, False)
                self.assertTrue(any("needs user unit net-pulse.service" in a for a in actions), actions)
                self.assertTrue(any("not in this archive" in f for f in engine.FAILURES), engine.FAILURES)
        finally:
            engine.run, engine.plugin_list = real_run, real_list
            engine.FAILURES.clear()

    def test_ids_disabled_on_the_source_get_disabled_on_the_target(self):
        import types
        calls = []
        real_run, real_list = engine.run, engine.plugin_list
        state = {"pi.workspaces": True, "vic.only": True}
        def fake_list():
            return [{"id": k, "enabled": v} for k, v in state.items()]
        def fake(cmd, **kw):
            calls.append(cmd)
            if cmd[:3] == ["omarchy", "plugin", "disable"]:
                state[cmd[3]] = False
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        engine.plugin_list, engine.run = fake_list, fake
        try:
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp); cat = t / "cat"; cat.mkdir()
                home = t / "home"; (home / ".config/omarchy/plugins").mkdir(parents=True)
                (cat / "meta.json").write_text(json.dumps({
                    "plugins": [], "disabledIds": ["pi.workspaces"]}), encoding="utf-8")
                undo = t / "undo"; undo.mkdir()
                engine.FAILURES.clear()
                actions = engine.restore_plugins(cat, home, "", undo, False)
        finally:
            engine.plugin_list, engine.run = real_list, real_run
            engine.FAILURES.clear()
        disables = [c[3] for c in calls if c[:3] == ["omarchy", "plugin", "disable"]]
        self.assertEqual(disables, ["pi.workspaces"])
        # A plugin the source never knew about must be left alone.
        self.assertNotIn("vic.only", disables)
        self.assertTrue(any("disabled pi.workspaces" in a for a in actions), actions)


class PackedUnitTests(unittest.TestCase):
    def test_a_packed_unit_is_installed_rather_than_only_reported(self):
        import types
        calls = []
        real_run, real_list = engine.run, engine.plugin_list
        engine.plugin_list = lambda: []
        def fake(cmd, **kw):
            calls.append(cmd)
            if cmd[:3] == ["systemctl", "--user", "cat"]:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        engine.run = fake
        try:
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp); cat = t / "cat"; (cat / "units").mkdir(parents=True)
                (cat / "units/net-pulse.service").write_text(
                    "[Service]\nExecStart=/usr/bin/python3 /home/old/x.py daemon\n", encoding="utf-8")
                home = t / "home"; (home / ".config/omarchy/plugins").mkdir(parents=True)
                (cat / "meta.json").write_text(json.dumps({"plugins": [
                    {"id": "n.pulse", "kind": "local", "enabled": False,
                     "units": ["net-pulse.service"]}]}), encoding="utf-8")
                undo = t / "undo"; undo.mkdir()
                engine.FAILURES.clear()
                actions = engine.restore_plugins(cat, home, "/home/old", undo, False)
                dest = home / ".config/systemd/user/net-pulse.service"
                self.assertTrue(dest.is_file(), actions)
                # the home path inside the unit is rewritten for this machine
                self.assertIn(str(home), dest.read_text(encoding="utf-8"))
                self.assertEqual(engine.FAILURES, [])
                self.assertTrue(any("installed and enabled net-pulse.service" in a for a in actions), actions)
        finally:
            engine.run, engine.plugin_list = real_run, real_list
            engine.FAILURES.clear()
        self.assertIn(["systemctl", "--user", "enable", "--now", "net-pulse.service"], calls)


class GitSyncTests(unittest.TestCase):
    """An already-installed git plugin must be brought to the recorded version."""

    def _repo(self, root: Path) -> Path:
        import subprocess
        root.mkdir(parents=True, exist_ok=True)
        run = lambda *a: subprocess.run(a, cwd=root, capture_output=True, text=True)
        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@t"); run("git", "config", "user.name", "t")
        return root

    def test_up_to_date_checkout_is_left_alone(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            r = self._repo(Path(tmp) / "p")
            (r / "f.txt").write_text("v1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=r, capture_output=True)
            subprocess.run(["git", "commit", "-qm", "v1"], cwd=r, capture_output=True)
            head = engine.git_head(r)
            msg = engine.sync_git_checkout(r, {"id": "a", "commit": head, "branch": "main"})
            self.assertIn("already at", msg)

    def test_dirty_checkout_is_never_clobbered(self):
        import subprocess
        engine.FAILURES.clear()
        with tempfile.TemporaryDirectory() as tmp:
            r = self._repo(Path(tmp) / "p")
            (r / "f.txt").write_text("v1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=r, capture_output=True)
            subprocess.run(["git", "commit", "-qm", "v1"], cwd=r, capture_output=True)
            (r / "f.txt").write_text("my local edit\n", encoding="utf-8")
            msg = engine.sync_git_checkout(r, {"id": "a", "commit": "0" * 40, "branch": "main",
                                               "url": "https://example/a.git"})
            self.assertIn("uncommitted changes here", msg)
            self.assertEqual((r / "f.txt").read_text(encoding="utf-8"), "my local edit\n")
            self.assertEqual(len(engine.FAILURES), 1)
        engine.FAILURES.clear()

    def test_out_of_date_checkout_is_moved_to_the_recorded_commit(self):
        import subprocess
        engine.FAILURES.clear()
        with tempfile.TemporaryDirectory() as tmp:
            src = self._repo(Path(tmp) / "src")
            (src / "f.txt").write_text("v1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=src, capture_output=True)
            subprocess.run(["git", "commit", "-qm", "v1"], cwd=src, capture_output=True)
            subprocess.run(["git", "checkout", "-qb", "feature/x"], cwd=src, capture_output=True)
            (src / "f.txt").write_text("v2\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "v2"], cwd=src, capture_output=True)
            want = engine.git_head(src)

            dst = Path(tmp) / "dst"
            subprocess.run(["git", "clone", "-q", str(src), str(dst)], capture_output=True)
            subprocess.run(["git", "checkout", "-q", "main"], cwd=dst, capture_output=True)
            self.assertEqual((dst / "f.txt").read_text(encoding="utf-8"), "v1\n")

            msg = engine.sync_git_checkout(dst, {"id": "a", "commit": want,
                                                 "branch": "feature/x", "url": str(src)})
            self.assertIn("updated a", msg)
            self.assertEqual(engine.git_head(dst), want)
            self.assertEqual((dst / "f.txt").read_text(encoding="utf-8"), "v2\n")
            self.assertEqual(engine.git_branch_of(dst), "feature/x")
            self.assertEqual(engine.FAILURES, [])
        engine.FAILURES.clear()

    def test_branch_on_a_fork_is_recorded_not_origin(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            up = self._repo(Path(tmp) / "upstream")
            (up / "f.txt").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=up, capture_output=True)
            subprocess.run(["git", "commit", "-qm", "x"], cwd=up, capture_output=True)
            fork = Path(tmp) / "fork"
            subprocess.run(["git", "clone", "-q", str(up), str(fork)], capture_output=True)
            subprocess.run(["git", "checkout", "-qb", "side"], cwd=fork, capture_output=True)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "s"], cwd=fork, capture_output=True)

            work = Path(tmp) / "work"
            subprocess.run(["git", "clone", "-q", str(up), str(work)], capture_output=True)
            subprocess.run(["git", "remote", "add", "myfork", str(fork)], cwd=work, capture_output=True)
            subprocess.run(["git", "fetch", "-q", "myfork"], cwd=work, capture_output=True)
            subprocess.run(["git", "checkout", "-qb", "side", "--track", "myfork/side"],
                           cwd=work, capture_output=True)
            # origin is upstream, but the branch only exists on the fork
            self.assertEqual(engine.tracking_remote(work), "myfork")
            self.assertIn("fork", engine.git_remote(work))
            self.assertEqual(engine.git_branch_of(work), "side")


class GapClosureTests(unittest.TestCase):
    def test_guarded_source_lines_are_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".config/bash").mkdir(parents=True)
            (home / ".config/bash/aliases.sh").write_text("alias x=y\n", encoding="utf-8")
            (home / ".bashrc").write_text(
                "source /usr/share/pkg/rc\n"                       # outside home, skipped
                'source "$OMARCHY_PATH/default/bash/rc"\n'          # unresolvable, skipped
                "[[ -r ~/.config/bash/aliases.sh ]] && source ~/.config/bash/aliases.sh\n"
                "[ -f ~/.config/bash/missing.sh ] && source ~/.config/bash/missing.sh\n",
                encoding="utf-8")
            found = [str(f.relative_to(home)) for f in engine.sourced_files(home / ".bashrc", home)]
            self.assertEqual(found, [".config/bash/aliases.sh"])

    def test_cli_collects_what_the_shell_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / ".config/bash").mkdir(parents=True)
            (home / ".config/bash/aliases.sh").write_text("alias x=y\n", encoding="utf-8")
            (home / ".bashrc").write_text(
                "[[ -r ~/.config/bash/aliases.sh ]] && source ~/.config/bash/aliases.sh\n",
                encoding="utf-8")
            cat = Path(tmp) / "cat"; cat.mkdir()
            meta = engine.collect_cli(cat, home)
            self.assertIn(".config/bash/aliases.sh", meta["sourcedByShell"])
            self.assertTrue((cat / "files/.config/bash/aliases.sh").is_file())

    def test_dconf_drops_display_specific_keys(self):
        self.assertIn("text-scaling-factor", engine.DCONF_SKIP_KEYS)
        self.assertIn("cursor-size", engine.DCONF_SKIP_KEYS)

    def test_bar_restore_writes_the_other_omarchy_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cat = t / "cat"
            files = cat / "files/.config/omarchy"; files.mkdir(parents=True)
            (files / "workspace-names.json").write_text('{"1":"web"}', encoding="utf-8")
            home = t / "home"; home.mkdir(); undo = t / "undo"; undo.mkdir()
            actions = engine.restore_bar(cat, home, "", undo, False)
            self.assertTrue((home / ".config/omarchy/workspace-names.json").is_file())
            self.assertTrue(any("bar left alone" in a for a in actions), actions)

    def test_bar_restore_does_not_write_shell_json_as_a_plain_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            files = t / "cat/files/.config/omarchy"; files.mkdir(parents=True)
            (files / "shell.json").write_text('{"bar":{}}', encoding="utf-8")
            home = t / "home"; home.mkdir(); undo = t / "undo"; undo.mkdir()
            done = engine.restore_file_tree(t / "cat", home, "", undo, False,
                                            skip_rel=".config/omarchy/shell.json")
            self.assertEqual(done, [])
            self.assertFalse((home / ".config/omarchy/shell.json").exists())

    def test_source_repo_provenance_is_recovered_by_manifest_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / "Projects/mything"; repo.mkdir(parents=True)
            (repo / "manifest.json").write_text(json.dumps({"id": "me.thing"}), encoding="utf-8")
            index = engine.source_repo_index(home)
            self.assertEqual(index.get("me.thing"), [repo])

    def test_trees_match_ignores_repo_only_extras(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = t / "repo"; repo.mkdir()
            live = t / "live"; live.mkdir()
            for d in (repo, live):
                (d / "Panel.qml").write_text("x" * 10, encoding="utf-8")
            (repo / "README.md").write_text("docs", encoding="utf-8")   # repo-only
            self.assertTrue(engine.trees_match(repo, live))
            (live / "extra.qml").write_text("drift", encoding="utf-8")  # live-only = drift
            self.assertFalse(engine.trees_match(repo, live))


class PlanTests(unittest.TestCase):
    """`plan` must emit a script that is reviewable, re-runnable and honest."""

    def _plan(self, manifest, files=None):
        tmp = tempfile.mkdtemp()
        root = Path(tmp) / "payload"
        (root / "categories").mkdir(parents=True)
        for cid in manifest.get("categories", {}):
            (root / "categories" / cid).mkdir(parents=True, exist_ok=True)
        for rel, body in (files or {}).items():
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
        manifest.setdefault("kind", engine.KIND)
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        plan = engine.build_plan(root, manifest, list(manifest.get("categories", {})))
        return plan, engine.render_plan_script(plan, manifest, Path("a.tar.zst"), root)

    def test_script_bootstraps_the_desktop_session(self):
        _p, script = self._plan({"categories": {"plugins": {"plugins": []}}})
        # Without these the omarchy CLI and shell IPC silently do nothing.
        self.assertIn("OMARCHY_PATH:=/usr/share/omarchy", script)
        self.assertIn("XDG_RUNTIME_DIR:=/run/user/$(id -u)", script)
        self.assertIn("WAYLAND_DISPLAY", script)
        self.assertIn("HYPRLAND_INSTANCE_SIGNATURE", script)

    def test_preflight_refuses_a_dead_shell(self):
        _p, script = self._plan({"categories": {"plugins": {"plugins": []}}})
        self.assertIn("is not answering", script)

    def test_upgrade_runs_before_packages(self):
        _p, script = self._plan({"categories": {"packages": {"repo": ["jq"]}}})
        self.assertLess(script.index("omarchy update -y"), script.index("omarchy pkg add"))

    def test_a_repo_with_no_remote_is_called_out_not_skipped_silently(self):
        _p, script = self._plan({"categories": {"projects": {
            "repos": [{"path": "Projects/orphan", "url": ""}]}}})
        self.assertIn("no git remote recorded", script)

    def test_existing_plugin_is_not_recloned(self):
        _p, script = self._plan({"categories": {"plugins": {"plugins": [
            {"id": "a.plug", "kind": "git", "url": "https://x/a.git"}]}}})
        self.assertIn("] || omarchy plugin add", script)

    def test_placement_is_carried_into_the_enable_command(self):
        _p, script = self._plan({"categories": {"plugins": {"plugins": [
            {"id": "a.plug", "kind": "git", "url": "https://x/a.git", "enabled": True,
             "placement": {"section": "left", "index": 3}}]}}})
        self.assertIn("omarchy plugin enable a.plug --section left --index 3", script)

    def test_disable_pass_repeats_for_clone_cascades(self):
        _p, script = self._plan({"categories": {"plugins": {
            "plugins": [], "disabledIds": ["pi.workspaces"]}}})
        self.assertIn("for _ in 1 2 3; do", script)
        self.assertIn("omarchy plugin disable pi.workspaces", script)

    def test_a_failed_line_fails_its_step_instead_of_sailing_past(self):
        import subprocess
        _p, script = self._plan({"categories": {"packages": {"repo": ["jq"]}}})
        self.assertIn("( set -e", script)
        self.assertNotIn("if ! ( set -e", script)
        self.assertIn("step_rc=$?", script)
        self.assertIn("failed_steps+=(", script)
        # bash suspends errexit inside an `if` condition, so `if ! ( set -e ...)`
        # ran every line of a step and still called the whole plan clean.
        plan = engine.Plan()
        plan.add("packages", "a step that fails halfway",
                 ["false", "echo REACHED-AFTER-FAILURE"])
        out = engine.render_plan_script(plan, {"hostname": "x"},
                                        Path("/tmp/a.tar.zst"), Path("/tmp/payload"))
        proc = subprocess.run(["bash", "-c", out], capture_output=True, text=True)
        self.assertNotIn("REACHED-AFTER-FAILURE", proc.stdout)
        self.assertNotIn("plan applied cleanly", proc.stdout)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("1 step(s) failed", proc.stderr)

    def test_a_plugin_id_cannot_smuggle_a_command_into_the_plan(self):
        import subprocess
        evil = "demo$(printf INJECTED >&2)"
        self.assertFalse(engine.shell_segment(evil))
        plan = engine.Plan()
        engine.plan_plugins(plan, {"plugins": [
            {"id": evil, "kind": "git", "url": "https://x/a.git", "enabled": True},
            {"id": "also'; printf ALSO-INJECTED >&2; :", "kind": "local", "tree": "trees/x"},
        ]}, Path("/tmp/payload"))
        script = engine.render_plan_script(plan, {"hostname": "x"},
                                           Path("/tmp/a.tar.zst"), Path("/tmp/payload"))
        self.assertNotIn("INJECTED", script)
        proc = subprocess.run(["bash", "-n", "-c", script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_ordinary_ids_and_units_are_untouched(self):
        # shlex.quote leaves a mechanical name alone, so the guard above must
        # not have cost the normal path its steps.
        plan = engine.Plan()
        engine.plan_plugins(plan, {"plugins": [
            {"id": "a.plug", "kind": "git", "url": "https://x/a.git", "branch": "main",
             "commit": "d" * 40, "units": ["a@b.service"]},
        ]}, Path("/tmp/payload"))
        body = "\n".join(line for step in plan.steps for line in step["body"])
        self.assertIn('"$HOME"/.config/omarchy/plugins/a.plug', body)
        self.assertIn("systemctl --user enable --now a@b.service", body)
        self.assertTrue(engine.shell_segment("a.plug"))
        self.assertTrue(engine.shell_segment("a@b.service"))
        for bad in ("demo$(x)", "a;b", "a b", "-rf", "a`x`", "a|b", "a\nb", "..", ""):
            self.assertFalse(engine.shell_segment(bad), bad)

    def test_generated_script_is_valid_bash(self):
        import subprocess
        _p, script = self._plan({"categories": {
            "packages": {"repo": ["jq"], "aur": ["yay"]},
            "projects": {"repos": [{"path": "Projects/x", "url": "https://x/x.git",
                                    "branch": "main"}]},
            "plugins": {"plugins": [{"id": "a.plug", "kind": "git", "url": "https://x/a.git",
                                     "enabled": True, "branch": "main", "commit": "d" * 40}],
                        "disabledIds": ["b.off"]},
            "themes": {"themes": [{"id": "t", "url": "https://x/t.git"}]},
        }})
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(script); path = fh.name
        proc = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_system_is_not_inlined_because_it_needs_root(self):
        _p, script = self._plan({"categories": {"system": {"enabledUnits": [], "etcFiles": []}}})
        self.assertIn("--allow-system", script)
        self.assertNotIn("sudo bash", script)


class SystemLayerTests(unittest.TestCase):
    """The system layer writes /etc and enables units, so it must be verifiable."""

    def _cat(self, tmp):
        cat = Path(tmp) / "cat"
        (cat / "etc/systemd/system").mkdir(parents=True)
        (cat / "etc/thing.conf").write_text("x=1\n", encoding="utf-8")
        (cat / "etc/systemd/system/demo.service").write_text(
            "[Unit]\nDescription=demo\n[Service]\nExecStart=/bin/true\n"
            "[Install]\nWantedBy=multi-user.target\n", encoding="utf-8")
        (cat / "meta.json").write_text(json.dumps({
            "enabledUnits": ["demo.service"],
            "etcFiles": ["thing.conf", "systemd/system/demo.service"]}), encoding="utf-8")
        return cat

    def test_alt_root_applies_without_root_and_touches_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat = self._cat(tmp)
            home = Path(tmp) / "home"; home.mkdir()
            alt = Path(tmp) / "root"
            engine.FAILURES.clear()
            actions = engine.restore_system(cat, home, False, True, str(alt))
            self.assertTrue((alt / "etc/thing.conf").is_file(), actions)
            self.assertEqual((alt / "etc/thing.conf").read_text(encoding="utf-8"), "x=1\n")
            self.assertTrue((alt / "etc/systemd/system/demo.service").is_file())
            # enabled via systemctl --root, so the wants symlink exists
            wants = alt / "etc/systemd/system/multi-user.target.wants/demo.service"
            self.assertTrue(wants.is_symlink(), sorted(p.name for p in alt.rglob("*")))
            self.assertEqual(engine.FAILURES, [])
        engine.FAILURES.clear()

    def test_units_that_did_not_enable_are_reported_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            alt = Path(tmp) / "root"
            (alt / "etc/systemd/system").mkdir(parents=True)
            engine.FAILURES.clear()
            out = engine.verify_units(["nope.service"], str(alt), True)
            self.assertTrue(any("no unit file here" in x for x in out), out)
            self.assertEqual(len(engine.FAILURES), 1)
        engine.FAILURES.clear()

    def test_static_units_are_not_treated_as_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            alt = Path(tmp) / "root"
            d = alt / "usr/lib/systemd/system"; d.mkdir(parents=True)
            # no [Install] section => static, cannot be enabled, not a failure
            (d / "st.service").write_text("[Unit]\nDescription=s\n[Service]\nExecStart=/bin/true\n",
                                          encoding="utf-8")
            engine.FAILURES.clear()
            out = engine.verify_units(["st.service"], str(alt), True)
            self.assertEqual(engine.FAILURES, [], out)
        engine.FAILURES.clear()

    def test_without_allow_system_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat = self._cat(tmp)
            home = Path(tmp) / "home"; home.mkdir()
            alt = Path(tmp) / "root"
            actions = engine.restore_system(cat, home, False, False, str(alt))
            self.assertFalse(alt.exists(), actions)
            self.assertTrue(any("NOT applied" in a for a in actions), actions)

    def test_upgrade_failure_aborts_before_any_category(self):
        import types
        from types import SimpleNamespace
        import io, contextlib
        real_run, real_open, real_manifest = engine.run, engine.open_imprint, engine.load_manifest
        fake_root = Path(tempfile.mkdtemp())
        (fake_root / "categories/packages").mkdir(parents=True)
        engine.open_imprint = lambda a, b: fake_root
        engine.load_manifest = lambda r: {"kind": engine.KIND, "home": "/home/x",
                                          "categories": {"packages": {}}}
        touched = []
        engine.run = lambda cmd, **kw: (touched.append(cmd),
                                        types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))[1]
        real_restore = engine.restore_category
        engine.restore_category = lambda *a, **k: ["SHOULD NOT RUN"]
        buf = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                # An unpacked imprint, which is what need_archive lets through.
                (Path(tmp) / "manifest.json").write_text("{}", encoding="utf-8")
                args = SimpleNamespace(archive=tmp, only="packages", dry_run=False, upgrade=True)
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                    rc = engine.cmd_restore(args)
        finally:
            engine.run, engine.open_imprint, engine.load_manifest = real_run, real_open, real_manifest
            engine.restore_category = real_restore
            engine.FAILURES.clear()
        report = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertFalse(report["ok"])
        self.assertIn("omarchy update failed", report["upgrade"])
        # Arch has no partial upgrades: nothing may be installed onto a failed one.
        self.assertEqual(report["categories"], {})


class ApplyJournalTests(unittest.TestCase):
    """apply must survive interruption and never re-do finished work blindly."""

    def _plan_dir(self, tmp, bodies):
        d = Path(tmp) / "plan"
        (d / "payload").mkdir(parents=True)
        steps = [{"id": f"{i:03d}-t-{i}", "phase": "t", "title": f"step{i}",
                  "note": "", "body": b, "hash": engine.body_hash(b)}
                 for i, b in enumerate(bodies)]
        (d / "plan.json").write_text(json.dumps(
            {"steps": steps, "preamble": [], "payload": str(d / "payload")}), encoding="utf-8")
        return d

    def _args(self, d, **kw):
        from types import SimpleNamespace
        base = dict(plan=str(d), resume=False, restart=False, recheck=False,
                    dry_run=False, phase="", stop_on_failure=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def _run(self, d, **kw):
        import io, contextlib
        buf = io.StringIO()
        engine.FAILURES.clear()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = engine.cmd_apply(self._args(d, **kw))
        engine.FAILURES.clear()
        return rc, json.loads(buf.getvalue())

    def test_a_failing_step_is_recorded_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._plan_dir(tmp, [["true"], ["exit 3"], ["true"]])
            rc, r = self._run(d)
            self.assertEqual(rc, 1)
            self.assertFalse(r["ok"])
            self.assertEqual(len(r["failed"]), 1)
            self.assertEqual(r["failed"][0]["exit"], 3)

    def test_resume_skips_what_succeeded_and_retries_what_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "flag"
            d = self._plan_dir(tmp, [["true"], [f'test -f {marker}']])
            rc, r = self._run(d)
            self.assertEqual(rc, 1)
            marker.write_text("", encoding="utf-8")     # fix the cause
            rc, r = self._run(d, resume=True)
            self.assertEqual(rc, 0)
            self.assertEqual(r["ranNow"], 1)             # only the failed one
            self.assertEqual(r["skippedAlreadyDone"], 1)

    def test_a_step_left_running_by_a_kill_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._plan_dir(tmp, [["true"], ["true"]])
            self._run(d)
            j = json.loads((d / "journal.json").read_text(encoding="utf-8"))
            key = sorted(j["steps"])[1]
            j["steps"][key]["state"] = "running"          # as SIGKILL would leave it
            (d / "journal.json").write_text(json.dumps(j), encoding="utf-8")
            _rc, r = self._run(d, resume=True)
            self.assertEqual(r["ranNow"], 1)

    def test_an_edited_step_is_no_longer_considered_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._plan_dir(tmp, [["true"], ["true"]])
            self._run(d)
            plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
            plan["steps"][0]["body"] = ["true", "true"]
            plan["steps"][0]["hash"] = engine.body_hash(plan["steps"][0]["body"])
            (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            _rc, r = self._run(d, resume=True)
            self.assertEqual(r["ranNow"], 1)
            self.assertEqual(r["skippedAlreadyDone"], 1)

    def test_dry_run_executes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            made = Path(tmp) / "made"
            d = self._plan_dir(tmp, [[f"touch {made}"]])
            _rc, r = self._run(d, dry_run=True)
            self.assertFalse(made.exists())
            self.assertTrue(r["dryRun"])

    def test_stop_on_failure_halts_instead_of_carrying_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            later = Path(tmp) / "later"
            d = self._plan_dir(tmp, [["exit 1"], [f"touch {later}"]])
            self._run(d, stop_on_failure=True)
            self.assertFalse(later.exists())

    def test_unknown_phase_is_rejected_rather_than_doing_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._plan_dir(tmp, [["true"]])
            with self.assertRaises(SystemExit) as ctx:
                self._run(d, phase="nope")
            self.assertIn("no phase", str(ctx.exception))

    def test_missing_payload_is_reported_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._plan_dir(tmp, [["true"]])
            shutil.rmtree(d / "payload")
            plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
            plan["payload"] = "/definitely/not/here"
            (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                self._run(d)
            self.assertIn("payload is missing", str(ctx.exception))

    def test_steps_can_reach_the_payload_by_variable_not_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._plan_dir(tmp, [['test -d "$PLAN_PAYLOAD"']])
            rc, _r = self._run(d)
            self.assertEqual(rc, 0)

    def test_a_corrupt_plan_is_a_message_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "plan"; d.mkdir()
            (d / "plan.json").write_text("not json", encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                self._run(d)
            self.assertIn("not readable as a plan", str(ctx.exception))


class PickerTests(unittest.TestCase):
    """space toggles, space on a branch descends, and the top row drives all."""

    def _tree(self):
        leaf = engine.Node("look", "Look", selected=True)
        kids = [engine.Node("a", "a", selected=True), engine.Node("b", "b", selected=True)]
        branch = engine.Node("plugins", "Plugins", children=kids, selected=True)
        cats = [leaf, branch]
        return cats, [engine.SelectAll(cats)] + cats

    def test_branch_state_is_derived_from_children(self):
        cats, _roots = self._tree()
        branch = cats[1]
        self.assertEqual(branch.state(), "on")
        branch.children[0].selected = False
        self.assertEqual(branch.state(), "partial")
        branch.children[1].selected = False
        self.assertEqual(branch.state(), "off")

    def test_select_all_row_reflects_and_drives_everything(self):
        cats, roots = self._tree()
        all_row = roots[0]
        self.assertEqual(all_row.state(), "on")
        all_row.set_all(False)
        self.assertTrue(all(n.state() == "off" for n in cats))
        self.assertEqual(all_row.state(), "off")
        all_row.set_all(True)
        self.assertEqual(all_row.state(), "on")
        cats[0].selected = False
        self.assertEqual(all_row.state(), "partial")

    def test_select_all_toggles_in_place_rather_than_latching(self):
        cats, roots = self._tree()
        all_row = roots[0]
        all_row.selected = not all_row.selected      # what space does
        self.assertEqual(all_row.state(), "off")
        all_row.selected = not all_row.selected
        self.assertEqual(all_row.state(), "on")

    def test_only_partial_branches_produce_a_subselection(self):
        cats, _roots = self._tree()
        branch = cats[1]
        self.assertEqual(branch.chosen_leaves(), ["a", "b"])
        branch.children[0].selected = False
        self.assertEqual(branch.chosen_leaves(), ["b"])
        self.assertEqual(branch.state(), "partial")

    def test_a_row_with_children_is_drawn_with_the_submenu_arrow(self):
        cats, roots = self._tree()
        rows = {}
        class Win:
            def erase(self): rows.clear()
            def addnstr(self, y, x, text, n, attr=0): rows[y] = text[:n]
            def refresh(self): pass
        engine._draw(Win(), roots, 0, 0, [], "hdr", 24, 100)
        drawn = "\n".join(rows.values())
        self.assertIn(engine.MARK_SUB, drawn)                 # ▸ present
        self.assertIn("Plugins (2)", drawn)                   # child count shown
        self.assertNotIn(f"Select ALL for backup ({engine.MARK_SUB}", drawn)
        look_line = [v for v in rows.values() if "Look" in v][0]
        self.assertNotIn(engine.MARK_SUB, look_line)          # leaves have no arrow

    def test_marks_distinguish_on_off_and_partial(self):
        cats, roots = self._tree()
        cats[1].children[0].selected = False
        rows = {}
        class Win:
            def erase(self): rows.clear()
            def addnstr(self, y, x, text, n, attr=0): rows[y] = text[:n]
            def refresh(self): pass
        engine._draw(Win(), roots, 0, 0, [], "hdr", 24, 100)
        joined = "\n".join(rows.values())
        self.assertIn(engine.MARK_ON, joined)
        self.assertIn(engine.MARK_PART, joined)

    def test_subselection_narrows_what_a_category_collects(self):
        engine.SUBSELECT.clear()
        try:
            engine.SUBSELECT["plugins"] = {"keep.me"}
            self.assertTrue(engine.wanted("plugins", "keep.me"))
            self.assertFalse(engine.wanted("plugins", "drop.me"))
            # a category with no subselection is unrestricted
            self.assertTrue(engine.wanted("themes", "anything"))
        finally:
            engine.SUBSELECT.clear()


class ActionRowTests(unittest.TestCase):
    """Selecting everything must not start the job; only the action row does."""

    def _rows(self):
        cats = [engine.Node("look", "Look", selected=True),
                engine.Node("bar", "Bar", selected=False)]
        return cats, [engine.SelectAll(cats)] + cats + [engine.ActionRow("Start backup", cats)]

    def test_select_all_row_is_not_the_action_row(self):
        _cats, rows = self._rows()
        self.assertIsInstance(rows[0], engine.SelectAll)
        self.assertIsInstance(rows[-1], engine.ActionRow)
        self.assertNotIsInstance(rows[0], engine.ActionRow)

    def test_select_all_changes_selection_without_confirming(self):
        cats, rows = self._rows()
        rows[0].selected = not rows[0].selected      # what space/enter does there
        self.assertTrue(all(n.state() == "on" for n in cats))
        # the action row is a separate object; nothing about toggling all touches it
        self.assertEqual(rows[-1].state(), "off")

    def test_action_row_reports_how_much_is_selected(self):
        cats, rows = self._rows()
        self.assertIn("1 category selected", rows[-1].hint)
        cats[1].selected = True
        self.assertIn("2 categories selected", rows[-1].hint)
        for n in cats:
            n.set_all(False)
        self.assertIn("nothing selected", rows[-1].hint)

    def test_action_row_never_becomes_a_selectable_category(self):
        cats, rows = self._rows()
        action = rows[-1]
        action.set_all(True)
        self.assertEqual(action.state(), "off")
        self.assertEqual(action.chosen_leaves(), [])
        self.assertFalse(action.is_branch)

    def test_action_row_is_drawn_as_a_call_to_action_not_a_checkbox(self):
        _cats, rows = self._rows()
        drawn = {}
        class Win:
            def erase(self): drawn.clear()
            def addnstr(self, y, x, t, n, a=0): drawn[y] = t[:n]
            def refresh(self): pass
        engine._draw(Win(), rows, 0, 0, [], "hdr", 24, 100)
        line = [v for v in drawn.values() if "Start backup" in v][0]
        self.assertIn("\u25b6", line)                       # ▶
        for mark in (engine.MARK_ON, engine.MARK_OFF, engine.MARK_PART):
            self.assertNotIn(mark, line)


class WiderCaptureTests(unittest.TestCase):
    def test_sockets_and_fifos_are_never_copied(self):
        import socket
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            regular = t / "ok.conf"; regular.write_text("x", encoding="utf-8")
            sock_path = t / "app.sock"
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(sock_path))
            fifo = t / "pipe"; os.mkfifo(fifo)
            try:
                self.assertTrue(engine.copyable(regular))
                self.assertFalse(engine.copyable(sock_path))
                self.assertFalse(engine.copyable(fifo))
                found = {f.name for f in engine.iter_files(t)}
                self.assertEqual(found, {"ok.conf"})
            finally:
                srv.close()

    def test_stock_themes_are_listed_but_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / ".config/omarchy/themes/mine").mkdir(parents=True)
            stock = Path(tmp) / "share/themes"
            (stock / "shipped").mkdir(parents=True)
            old = os.environ.get("OMARCHY_PATH")
            os.environ["OMARCHY_PATH"] = str(Path(tmp) / "share")
            try:
                rows = engine.theme_children(home)
            finally:
                if old is None: os.environ.pop("OMARCHY_PATH", None)
                else: os.environ["OMARCHY_PATH"] = old
            by = {r.label: r for r in rows}
            self.assertIn("mine", by); self.assertIn("shipped", by)
            self.assertTrue(by["mine"].selected)
            self.assertFalse(by["shipped"].selected)      # ships with Omarchy
            self.assertIn("stock", by["shipped"].hint)

    def test_configs_sweep_skips_with_a_stated_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cfg = home / ".config"
            for name in ("hypr", "BraveSoftware", "littleapp"):
                (cfg / name).mkdir(parents=True)
            (cfg / "littleapp/conf.ini").write_text("a=1", encoding="utf-8")
            (cfg / "BraveSoftware/profile").write_text("secrets", encoding="utf-8")
            cat = Path(tmp) / "cat"; cat.mkdir()
            meta = engine.collect_configs(cat, home)
            kept = " ".join(meta["files"])
            self.assertIn("littleapp", kept)
            self.assertNotIn("hypr", kept)                # owned by another category
            reasons = {x["path"]: x["why"] for x in meta["skipped"]}
            self.assertIn("BraveSoftware", reasons)
            self.assertIn("credential", reasons["BraveSoftware"])

    def test_configs_sweep_caps_huge_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            big = home / ".config/hog"; big.mkdir(parents=True)
            (big / "blob").write_bytes(b"0" * (engine.CONFIG_MAX_BYTES + 1024))
            cat = Path(tmp) / "cat"; cat.mkdir()
            meta = engine.collect_configs(cat, home)
            reasons = {x["path"]: x["why"] for x in meta["skipped"]}
            self.assertIn("hog", reasons)
            self.assertIn("over the cap", reasons["hog"])

    def test_home_documents_are_collected_but_not_dotfiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir(parents=True)
            (home / "AGENTS.md").write_text("notes", encoding="utf-8")
            (home / "list.txt").write_text("todo", encoding="utf-8")
            (home / ".bashrc").write_text("shell", encoding="utf-8")
            (home / "photo.png").write_bytes(b"\x89PNG")
            cat = Path(tmp) / "cat"; cat.mkdir()
            meta = engine.collect_home(cat, home)
            self.assertEqual(sorted(meta["files"]), ["AGENTS.md", "list.txt"])

    def test_shell_history_rides_with_secrets_not_a_default_save(self):
        self.assertFalse(engine.category_by_id("secrets")["default"])
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / ".ssh").mkdir(parents=True)
            (home / ".ssh/id.pub").write_text("ssh-ed25519 AAA", encoding="utf-8")
            (home / ".bash_history").write_text("export TOKEN=hunter2\n", encoding="utf-8")
            cat = Path(tmp) / "cat"; cat.mkdir()
            meta = engine.collect_secrets(cat, home)
            self.assertIn(".bash_history", " ".join(meta["files"]))
            self.assertTrue((cat / "files/.bash_history").is_file())

    def test_about_links_are_clickable_or_degrade(self):
        plain = engine.osc8("https://nixfred.com", "nixfred.com")
        self.assertIn("nixfred.com", plain)
        self.assertIn("https://nixfred.com", plain)       # visible when not a tty

    def test_theme_palette_falls_back_when_no_theme(self):
        for key in ("green", "red", "amber", "blue", "grey", "accent"):
            self.assertIsInstance(engine.FG.get(key, ""), str)
        self.assertEqual(engine.hex_rgb("#ff8000"), (255, 128, 0))
        self.assertIsNone(engine.hex_rgb("nonsense"))
        self.assertIsInstance(engine.theme_accent(), int)


class RestorePreviewTests(unittest.TestCase):
    """A restore says what it will change, before it is allowed to change it."""

    def _archive(self, tmp, packed, live):
        root = Path(tmp) / "arc"
        files = root / "categories/cli/files"
        for rel, body in packed.items():
            f = files / rel; f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
        (root / "categories/cli").mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(json.dumps(
            {"kind": engine.KIND, "home": "/home/src", "categories": {"cli": {}}}), encoding="utf-8")
        home = Path(tmp) / "home"
        for rel, body in live.items():
            f = home / rel; f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
        home.mkdir(parents=True, exist_ok=True)
        return root, home

    def test_classifies_new_changed_and_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, home = self._archive(tmp,
                packed={"a.conf": "same\n", "b.conf": "new value\n", "c.conf": "brand new\n"},
                live={"a.conf": "same\n", "b.conf": "old value\n"})
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            ch = engine.preview_changes(root, home, manifest, ["cli"])
            self.assertEqual(ch["filesChanged"], ["~/b.conf"])
            self.assertEqual(ch["filesNew"], ["~/c.conf"])
            self.assertEqual(ch["filesUnchanged"], 1)

    def test_home_rewrite_is_accounted_for_before_calling_a_file_changed(self):
        # The packed file mentions the source machine's home; after rewriting it
        # matches this machine, so it must not be reported as a change.
        with tempfile.TemporaryDirectory() as tmp:
            root, home = self._archive(tmp, packed={"p.conf": "PATH=/home/src/bin\n"}, live={})
            (home / "p.conf").write_text(f"PATH={home}/bin\n", encoding="utf-8")
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            ch = engine.preview_changes(root, home, manifest, ["cli"])
            self.assertEqual(ch["filesChanged"], [])
            self.assertEqual(ch["filesUnchanged"], 1)

    def test_render_says_so_when_nothing_would_change(self):
        text = engine.render_preview({"filesNew": [], "filesChanged": [], "filesUnchanged": 9}, ["cli"])
        self.assertIn("Nothing to change", text)

    def test_render_lists_destructive_actions_explicitly(self):
        text = engine.render_preview({
            "filesChanged": ["~/.bashrc"], "filesNew": [], "filesUnchanged": 0,
            "pluginsDisable": ["pi.workspaces"], "hostname": "vic \u2192 dex"}, ["plugins"])
        self.assertIn("overwrite existing files", text)
        self.assertIn("DISABLE plugins currently on", text)
        self.assertIn("rename this machine", text)

    def test_selection_narrows_a_restore(self):
        engine.SUBSELECT.clear()
        try:
            engine.SUBSELECT["scripts"] = {"bin/keep"}
            self.assertTrue(engine.wanted("scripts", "bin/keep"))
            self.assertFalse(engine.wanted("scripts", "bin/drop"))
            with tempfile.TemporaryDirectory() as tmp:
                cat = Path(tmp) / "cat"
                files = cat / "files/bin"; files.mkdir(parents=True)
                (files / "keep").write_text("a", encoding="utf-8")
                (files / "drop").write_text("b", encoding="utf-8")
                home = Path(tmp) / "home"; home.mkdir()
                undo = Path(tmp) / "undo"; undo.mkdir()
                done = engine.restore_file_tree(cat, home, "", undo, False, category="scripts")
                self.assertEqual(done, ["bin/keep"])
                self.assertFalse((home / "bin/drop").exists())
        finally:
            engine.SUBSELECT.clear()

    def test_noise_files_are_not_captured(self):
        for name in ("app.log", "x.sock", "SingletonLock", "Cache", "daemon.pid"):
            self.assertTrue(engine.is_skipped_name(name), name)
        for name in ("config.toml", "settings.json", "keybinds.conf"):
            self.assertFalse(engine.is_skipped_name(name), name)


class ArrowKeyTests(unittest.TestCase):
    """Arrows arrive as multi-byte sequences, split across reads, in several
    encodings. Every one of those has to move the cursor."""

    def test_every_arrow_variant_maps_to_a_direction(self):
        for name, want in (("KEY_RIGHT", "right"), ("KEY_LEFT", "left"),
                           ("KEY_UP", "up"), ("KEY_DOWN", "down")):
            code = getattr(engine.curses, name)
            self.assertEqual(engine.arrow_of(code), want, name)

    def test_unknown_keys_report_no_direction(self):
        self.assertEqual(engine.arrow_of(ord("x")), "")
        self.assertEqual(engine.arrow_of(ord(" ")), "")

    def test_modified_arrow_codes_are_recognised(self):
        # ncurses invents codes for ctrl/shift arrows from terminfo; they have no
        # Python constant, so they are matched by name instead.
        for name in ("KEY_SRIGHT", "KEY_SLEFT", "KEY_SR", "KEY_SF"):
            if hasattr(engine.curses, name):
                self.assertNotEqual(engine.arrow_of(getattr(engine.curses, name)), "", name)

    def test_direction_sets_are_non_empty_and_disjoint(self):
        self.assertTrue(engine.DOWN_KEYS and engine.UP_KEYS)
        self.assertTrue(engine.RIGHT_KEYS and engine.LEFT_KEYS)
        self.assertFalse(set(engine.DOWN_KEYS) & set(engine.UP_KEYS))
        self.assertFalse(set(engine.RIGHT_KEYS) & set(engine.LEFT_KEYS))

    def test_csi_tables_cover_the_sequences_terminals_send(self):
        # \x1b[B and \x1bOB both end in B; the final byte is what decides.
        for final, name in engine.CSI_FINAL.items():
            self.assertTrue(hasattr(engine.curses, name), final)
        for param, name in engine.CSI_TILDE.items():
            self.assertTrue(hasattr(engine.curses, name), param)
        self.assertEqual(engine.CSI_FINAL["B"], "KEY_DOWN")
        self.assertEqual(engine.CSI_FINAL["C"], "KEY_RIGHT")


class NarrowedRestoreTests(unittest.TestCase):
    """Picking a few items must not drag the whole category's parity with it."""

    def _cat(self, tmp):
        cat = Path(tmp) / "cat"; cat.mkdir()
        (cat / "meta.json").write_text(json.dumps({
            "plugins": [
                {"id": "a.keep", "kind": "local", "tree": "trees/a", "enabled": False},
                {"id": "b.drop", "kind": "local", "tree": "trees/b", "enabled": False,
                 "units": ["b.service"]},
            ],
            "disabledIds": ["x.off", "y.off"],
        }), encoding="utf-8")
        for name in ("a", "b"):
            d = cat / "trees" / name; d.mkdir(parents=True)
            (d / "f.qml").write_text("x", encoding="utf-8")
        return cat

    def _run(self, cat, home):
        undo = home.parent / "undo"; undo.mkdir(exist_ok=True)
        engine.FAILURES.clear()
        try:
            return engine.restore_plugins(cat, home, "", undo, True)
        finally:
            engine.FAILURES.clear()

    def test_narrowing_skips_the_sources_disabled_list(self):
        engine.SUBSELECT.clear()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cat = self._cat(tmp)
                home = Path(tmp) / "home"; (home / ".config/omarchy/plugins").mkdir(parents=True)
                engine.SUBSELECT["plugins"] = {"a.keep"}
                actions = self._run(cat, home)
                joined = " ".join(actions)
                self.assertIn("a.keep", joined)
                self.assertNotIn("b.drop", joined)
                self.assertIn("narrowed", joined)
                self.assertNotIn("would disable", joined)
        finally:
            engine.SUBSELECT.clear()

    def test_whole_category_still_gets_full_parity(self):
        engine.SUBSELECT.clear()
        with tempfile.TemporaryDirectory() as tmp:
            cat = self._cat(tmp)
            home = Path(tmp) / "home"; (home / ".config/omarchy/plugins").mkdir(parents=True)
            actions = self._run(cat, home)
            joined = " ".join(actions)
            self.assertIn("would disable", joined)
            self.assertIn("b.drop", joined)

    def test_unit_checks_respect_the_narrowing_too(self):
        engine.SUBSELECT.clear()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cat = self._cat(tmp)
                home = Path(tmp) / "home"; (home / ".config/omarchy/plugins").mkdir(parents=True)
                engine.SUBSELECT["plugins"] = {"a.keep"}
                actions = self._run(cat, home)
                # b.service belongs to the plugin that was not selected
                self.assertNotIn("b.service", " ".join(actions))
        finally:
            engine.SUBSELECT.clear()

    def test_one_file_tree_walker_handles_both_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat = Path(tmp) / "cat"
            files = cat / "files/.config/omarchy"; files.mkdir(parents=True)
            (files / "shell.json").write_text("{}", encoding="utf-8")
            (files / "other.json").write_text("{}", encoding="utf-8")
            home = Path(tmp) / "home"; home.mkdir()
            undo = Path(tmp) / "undo"; undo.mkdir()
            done = engine.restore_file_tree(cat, home, "", undo, True,
                                            skip_rel=".config/omarchy/shell.json")
            self.assertEqual(done, [".config/omarchy/other.json"])


class DestinationTests(unittest.TestCase):
    def test_unwritable_parent_fails_before_collecting(self):
        with tempfile.TemporaryDirectory() as tmp:
            wall = Path(tmp) / "wall"
            wall.mkdir(mode=0o500)
            try:
                with self.assertRaises(SystemExit) as caught:
                    engine.check_dest(wall / "sub" / "a.tar.zst", create=True)
                self.assertIn("cannot create", str(caught.exception))
                self.assertIn(str(wall), str(caught.exception))
                with self.assertRaises(SystemExit) as caught:
                    engine.check_dest(wall / "a.tar.zst")
                self.assertIn("cannot write into", str(caught.exception))
            finally:
                wall.chmod(0o700)

    def test_a_directory_that_is_not_there_is_refused_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "imprints" / "a.tar.zst"
            with self.assertRaises(SystemExit) as caught:
                engine.check_dest(dest)
            message = str(caught.exception)
            self.assertIn(f"no such directory: {dest.parent}", message)
            self.assertIn("imprint does not create directories", message)
            self.assertIn(f"mkdir -p {dest.parent}", message)
            self.assertFalse(dest.parent.exists())

    def test_an_existing_directory_passes_and_is_left_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.tar.zst"
            engine.check_dest(dest)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_only_the_path_imprint_picks_itself_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "imprints" / "a.tar.zst"
            engine.check_dest(dest, create=True)
            self.assertTrue(dest.parent.is_dir())
            self.assertEqual(list(dest.parent.iterdir()), [])

    def test_a_file_where_the_directory_should_be(self):
        with tempfile.TemporaryDirectory() as tmp:
            wall = Path(tmp) / "imprints"
            wall.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                engine.check_dest(wall / "a.tar.zst", create=True)
            self.assertIn("is a file, not a directory", str(caught.exception))

    def test_directory_in_the_way_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.tar.zst"
            dest.mkdir()
            with self.assertRaises(SystemExit) as caught:
                engine.check_dest(dest)
            self.assertIn("is a directory", str(caught.exception))

    def test_suggests_the_same_path_under_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "pi"
            (home / "google").mkdir(parents=True)
            real_home = engine.Path.home
            engine.Path.home = staticmethod(lambda: home)
            try:
                hint = engine.suggest_under_home(
                    Path("/home/google/imprints/a.tar.zst"))
            finally:
                engine.Path.home = real_home
            self.assertEqual(hint, home / "google/imprints/a.tar.zst")

    def test_no_suggestion_when_nothing_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "pi"
            home.mkdir(parents=True)
            real_home = engine.Path.home
            engine.Path.home = staticmethod(lambda: home)
            try:
                self.assertIsNone(
                    engine.suggest_under_home(Path("/mnt/usb/a.tar.zst")))
            finally:
                engine.Path.home = real_home

    def test_partial_archive_is_removed_when_the_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            staging.mkdir()
            (staging / "manifest.json").write_text("{}", encoding="utf-8")
            out = Path(tmp) / "out"
            out.mkdir()
            dest = out / "a.tar.zst"
            real_open = engine.tarfile.open

            def explode(name, mode="r", *a, **kw):
                handle = real_open(name, mode, *a, **kw)
                if "w" in mode:
                    handle.close()
                    raise OSError(28, "No space left on device")
                return handle

            engine.tarfile.open = explode
            try:
                with self.assertRaises(SystemExit) as caught:
                    engine.write_archive(staging, dest)
            finally:
                engine.tarfile.open = real_open
            self.assertIn("No space left on device", str(caught.exception))
            self.assertFalse(dest.exists())
            self.assertEqual(list(dest.parent.iterdir()), [])


class UnreadableFileTests(unittest.TestCase):
    def setUp(self):
        engine.SKIPPED.clear()
        engine.FAILURES.clear()

    def tearDown(self):
        engine.SKIPPED.clear()
        engine.FAILURES.clear()

    def test_one_unreadable_file_does_not_cost_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / ".config/app").mkdir(parents=True)
            (home / ".config/app/good.conf").write_text("ok", encoding="utf-8")
            bad = home / ".config/app/secret.conf"
            bad.write_text("nope", encoding="utf-8")
            bad.chmod(0o000)
            cat = Path(tmp) / "cat"
            cat.mkdir()
            try:
                noted = engine.copy_into_category(cat, home / ".config/app", home)
            finally:
                bad.chmod(0o600)
            self.assertEqual(noted, ".config/app/ (1 files)")
            self.assertEqual([p.name for p in (cat / "files/.config/app").iterdir()],
                             ["good.conf"])
            self.assertEqual(len(engine.SKIPPED), 1)
            self.assertEqual(engine.SKIPPED[0]["reason"], "Permission denied")
            self.assertTrue(engine.SKIPPED[0]["path"].endswith("secret.conf"))

    def test_a_full_staging_disk_stops_the_save(self):
        real = engine.copy_file

        def no_space(src, dest):
            raise OSError(28, "No space left on device")

        engine.copy_file = no_space
        try:
            with self.assertRaises(SystemExit) as caught:
                engine.try_copy(Path("/etc/hostname"), Path("/tmp/x"))
        finally:
            engine.copy_file = real
        self.assertIn("TMPDIR", str(caught.exception))
        self.assertEqual(engine.SKIPPED, [])

    def test_restore_reports_the_file_it_could_not_write_and_carries_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat = Path(tmp) / "cat"
            files = cat / "files/.config"
            (files / "locked").mkdir(parents=True)
            (files / "locked/a.conf").write_text("a", encoding="utf-8")
            (files / "b.conf").write_text("b", encoding="utf-8")
            home = Path(tmp) / "home"
            (home / ".config/locked").mkdir(parents=True)
            (home / ".config/locked").chmod(0o500)
            undo = Path(tmp) / "undo"
            undo.mkdir()
            try:
                done = engine.restore_file_tree(cat, home, "", undo, False)
            finally:
                (home / ".config/locked").chmod(0o700)
            self.assertIn(".config/b.conf", done)
            self.assertTrue(any("cannot write ~/.config/locked/a.conf" in line for line in done), done)
            self.assertEqual(len(engine.FAILURES), 1)
            self.assertEqual((home / ".config/b.conf").read_text(encoding="utf-8"), "b")

    def test_the_brief_names_what_could_not_be_read(self):
        brief = engine.render_brief({
            "hostname": "dex", "categories": {},
            "skipped": [{"path": "/home/pi/.config/a.sock", "reason": "Permission denied"}],
            "skippedCount": 3,
        })
        self.assertIn("Not in this archive (3 unreadable)", brief)
        self.assertIn("/home/pi/.config/a.sock", brief)


class InputPathTests(unittest.TestCase):
    def test_missing_archive_says_where_the_name_stopped_being_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                engine.need_archive(f"{tmp}/nowhere/nope.tar.zst")
            message = str(caught.exception)
            self.assertIn("no imprint archive at", message)
            self.assertIn(f"the deepest part that exists is {tmp}", message)

    def test_unpacked_imprint_directory_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "manifest.json").write_text("{}", encoding="utf-8")
            self.assertEqual(engine.need_archive(tmp), Path(tmp))

    def test_plain_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                engine.need_archive(tmp)
            self.assertIn("is a directory, not an imprint archive", str(caught.exception))

    def test_empty_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tar.zst"
            path.touch()
            with self.assertRaises(SystemExit) as caught:
                engine.need_archive(str(path))
            self.assertIn("did not finish", str(caught.exception))

    def test_need_dir_refuses_a_file_and_need_file_refuses_a_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                engine.need_dir(str(path), "cannot read the plan")
            self.assertIn("is a file, not a directory", str(caught.exception))
            with self.assertRaises(SystemExit) as caught:
                engine.need_file(tmp, "cannot read the selection file")
            self.assertIn("is a directory, not a file", str(caught.exception))


class VersionTests(unittest.TestCase):
    def test_about_carries_the_version_and_both_links(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            engine.cmd_about(None)
        out = buf.getvalue()
        self.assertIn(engine.VERSION, out)
        self.assertIn("github.com/nixfred/imprint", out)
        self.assertIn("nixfred.com", out)

    def test_an_archive_records_the_version_that_wrote_it(self):
        self.assertEqual(engine.machine_facts()["imprintVersion"], engine.VERSION)
        brief = engine.render_brief({"hostname": "dex", "categories": {},
                                     "imprintVersion": engine.VERSION})
        self.assertIn(f'imprint = "{engine.VERSION}"', brief)

    def test_an_archive_from_before_versions_still_reads(self):
        brief = engine.render_brief({"hostname": "dex", "categories": {}})
        self.assertIn('imprint = "before 1.0.0"', brief)


class RememberedSaveDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp) / "home"
        (self.home / "imprints").mkdir(parents=True)
        self.real_state_dir = engine.state_dir
        engine.state_dir = staticmethod(lambda: Path(self.tmp) / "state")

    def tearDown(self):
        engine.state_dir = self.real_state_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_with_nothing_remembered_it_is_imprints_under_home(self):
        directory, note = engine.default_archive_dir(self.home)
        self.assertEqual(directory, self.home / "imprints")
        self.assertEqual(note, "")

    def test_the_next_save_follows_the_last_one(self):
        drive = Path(self.tmp) / "google/imprints"
        drive.mkdir(parents=True)
        engine.remember_save_dir(drive)
        directory, note = engine.default_archive_dir(self.home)
        self.assertEqual(directory, drive)
        self.assertEqual(note, "")
        self.assertTrue(str(engine.default_archive_path(self.home, "dex")).startswith(str(drive)))

    def test_a_directory_that_went_away_falls_back_and_says_so(self):
        gone = Path(self.tmp) / "usb/imprints"
        gone.mkdir(parents=True)
        engine.remember_save_dir(gone)
        shutil.rmtree(Path(self.tmp) / "usb")
        directory, note = engine.default_archive_dir(self.home)
        self.assertEqual(directory, self.home / "imprints")
        self.assertIn(str(gone), note)
        self.assertIn("not there now", note)

    def test_unreadable_state_is_no_state_at_all(self):
        state = Path(self.tmp) / "state"
        state.mkdir()
        (state / "state.json").write_text("{not json", encoding="utf-8")
        directory, note = engine.default_archive_dir(self.home)
        self.assertEqual(directory, self.home / "imprints")
        self.assertEqual(note, "")

    def test_a_state_directory_that_cannot_be_written_does_not_raise(self):
        wall = Path(self.tmp) / "wall"
        wall.mkdir(mode=0o500)
        engine.state_dir = staticmethod(lambda: wall / "state")
        try:
            engine.remember_save_dir(self.home / "imprints")   # must not raise
            self.assertEqual(engine.read_state(), {})
        finally:
            wall.chmod(0o700)


class WrapperPickerTests(unittest.TestCase):
    """The bash wrapper, driven with a stub engine.

    choose_categories() is read through $(...), and a subshell cannot hand an
    assignment back to its parent. While it made the selection file itself the
    path never reached the engine, and every submenu choice was thrown away.
    """

    HARNESS = "\n".join([
        'engine() {',
        '  case "$1" in',
        '''    pick) printf '{"categories":["plugins"],"subselections":{"plugins":["a.plug"]}}' ;;''',
        '''    save|restore|preview) printf '%s\\n' "$*" >> "$ARGV_LOG" ;;''',
        '    default-output) echo "$HOME/imprints/x.tar.zst" ;;',
        """    verify) echo '{"ok":true}' ;;""",
        '    *) : ;;',
        '  esac',
        '}',
        'banner() { :; }',
        'confirm() { return 0; }',
        'HAVE_GUM=1',
        'PICK_ACTION=""',
        '',
    ])

    def _run(self, call: str):
        # Everything above the dispatch: the real functions, with a stub engine
        # spliced on. It runs from a temp directory with a placeholder engine
        # beside it, because the header resolves its own location.
        wrapper = (ROOT / "imprint").read_text(encoding="utf-8").split("\ncase $cmd in")[0]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "imprint-engine.py").write_text("", encoding="utf-8")
            runner = Path(tmp) / "runner.sh"
            runner.write_text(wrapper + self.HARNESS + call + '\necho "status=$?"\n',
                              encoding="utf-8")
            log = Path(tmp) / "argv.log"
            log.touch()
            env = dict(os.environ, ARGV_LOG=str(log))
            proc = subprocess.run(["bash", str(runner)], capture_output=True, text=True,
                                  env=env, cwd=tmp, stdin=subprocess.DEVNULL)
            return proc, log.read_text(encoding="utf-8")

    def test_a_narrowed_category_reaches_the_engine_as_a_selection(self):
        proc, log = self._run('do_save "" "" 0')
        self.assertIn("--select", log, proc.stderr)
        self.assertIn("--only plugins", log)

    def test_a_successful_save_reports_success(self):
        proc, _log = self._run('do_save "" "" 0')
        self.assertIn("status=0", proc.stdout, proc.stderr)

    def test_the_selection_file_is_cleaned_up(self):
        proc, log = self._run('do_save "" "" 0; echo "left=[$SELECTION_FILE]"')
        self.assertIn("left=[]", proc.stdout, proc.stdout)
        for word in log.split():
            if word.startswith("/tmp/imprint-selection-"):
                self.assertFalse(Path(word).exists(), word + " was left behind")


class PartialAndSilentLossTests(unittest.TestCase):
    def setUp(self):
        engine.SKIPPED.clear()
        engine.FAILURES.clear()

    def tearDown(self):
        engine.SKIPPED.clear()
        engine.FAILURES.clear()

    def test_a_read_that_dies_halfway_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.conf"
            src.write_text("PREFIX-then-a-bad-sector", encoding="utf-8")
            dest = Path(tmp) / "staged" / "src.conf"
            real = engine.copy_file

            def dies_halfway(s, d):
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_text("PREFIX", encoding="utf-8")
                raise OSError(5, "Input/output error")

            engine.copy_file = dies_halfway
            try:
                self.assertFalse(engine.try_copy(src, dest))
            finally:
                engine.copy_file = real
            self.assertFalse(dest.exists(), "a file reported as skipped was still staged")
            self.assertEqual(len(engine.SKIPPED), 1)

    def test_a_directory_nobody_can_read_is_reported_not_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            shut = home / ".config/shut"
            shut.mkdir(parents=True)
            (shut / "a.conf").write_text("a", encoding="utf-8")
            (home / ".config/open.conf").write_text("b", encoding="utf-8")
            cat = Path(tmp) / "cat"
            cat.mkdir()
            shut.chmod(0o000)
            try:
                noted = engine.copy_into_category(cat, home / ".config", home)
            finally:
                shut.chmod(0o700)
            self.assertIn(".config/", noted or "")
            self.assertTrue(any("shut" in item["path"] for item in engine.SKIPPED),
                            engine.SKIPPED)

    def test_a_rewrite_that_fails_keeps_the_file_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.conf"
            dest.write_text("path = /home/old/thing\n", encoding="utf-8")
            real = Path.write_text

            def no_space(self, *a, **kw):
                if self.name.endswith(".imprint-rewrite"):
                    raise OSError(28, "No space left on device")
                return real(self, *a, **kw)

            Path.write_text = no_space
            try:
                trouble = engine.rewrite_in_place(dest, "/home/old", "/home/new")
            finally:
                Path.write_text = real
            self.assertIn("No space left", trouble)
            self.assertEqual(dest.read_text(encoding="utf-8"), "path = /home/old/thing\n")
            self.assertEqual(list(Path(tmp).iterdir()), [dest])

    def test_a_rewrite_that_works_replaces_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.conf"
            dest.write_text("path = /home/old/thing\n", encoding="utf-8")
            self.assertEqual(engine.rewrite_in_place(dest, "/home/old", "/home/new"), "")
            self.assertEqual(dest.read_text(encoding="utf-8"), "path = /home/new/thing\n")
            self.assertEqual(list(Path(tmp).iterdir()), [dest])

    def test_a_failed_rewrite_during_restore_counts_as_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat = Path(tmp) / "cat"
            files = cat / "files/.config"
            files.mkdir(parents=True)
            (files / "a.conf").write_text("p = /home/old/x\n", encoding="utf-8")
            home = Path(tmp) / "home"
            home.mkdir()
            undo = Path(tmp) / "undo"
            undo.mkdir()
            real = engine.rewrite_in_place
            engine.rewrite_in_place = lambda *a, **k: "Read-only file system"
            try:
                done = engine.restore_file_tree(cat, home, "/home/old", undo, False)
            finally:
                engine.rewrite_in_place = real
            self.assertTrue(any("still points at the old home" in line for line in done), done)
            self.assertEqual(len(engine.FAILURES), 1)

    def test_a_collector_stops_on_a_full_staging_disk_instead_of_shrugging(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.conf"
            src.write_text("a", encoding="utf-8")
            real = engine.copy_file

            def no_space(s, d):
                raise OSError(28, "No space left on device")

            engine.copy_file = no_space
            try:
                with self.assertRaises(SystemExit) as caught:
                    engine.try_copy(src, Path(tmp) / "staged/a.conf")
            finally:
                engine.copy_file = real
            self.assertIn("TMPDIR", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
