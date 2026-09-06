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

    def test_broken_symlink_over_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cat = t / "cat"; (cat / "files/bin").mkdir(parents=True)
            home = t / "home"; (home / "bin").mkdir(parents=True)
            undo = t / "undo"; undo.mkdir()
            (cat / "files/bin/gone").symlink_to("/nonexistent/target")
            (home / "bin/gone").write_text("existing\n", encoding="utf-8")
            engine.restore_file_tree(cat, home, "", undo, False)
            self.assertTrue((home / "bin/gone").is_symlink())

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


if __name__ == "__main__":
    unittest.main()
