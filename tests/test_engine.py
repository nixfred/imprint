#!/usr/bin/env python3
import importlib.util
import json
import sys
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
        self.assertTrue(any("already installed from the same source" in a for a in actions), actions)

    def test_already_present_from_a_different_source_is_a_failure(self):
        actions = self._run(1, "plugin already exists", "https://evil/other.git")
        self.assertTrue(any("DIFFERENT source" in a for a in actions), actions)


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


if __name__ == "__main__":
    unittest.main()
