#!/usr/bin/env python3
"""Imprint — save and restore an Omarchy machine personality."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

KIND = "omarchy-imprint"
SCHEMA = 1
EXCLUDE_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
}
SKIP_UNIT_PREFIXES = (
    "omarchy-crash-watch",
    "omarchy-fcitx5",
    "omarchy-migrate-notify",
    "omarchy-recover-internal-monitor",
    "omarchy-tailscale-receive",
    "pipewire",
    "wireplumber",
    "xdg-user-dirs",
    "gnome-keyring",
    "p11-kit",
    "bt-agent",
)
TEXT_LIMIT = 512 * 1024
SCRIPT_LIMIT = 512 * 1024

CATEGORIES = [
    {
        "id": "look",
        "title": "Look",
        "summary": "Theme, font, gaps, rounding, branding",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "hyprland",
        "title": "Hyprland",
        "summary": "Bindings, autostart, window rules, extra Lua",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "bar",
        "title": "Bar and shell",
        "summary": "Bar layout, plugin order, idle, disabled plugins",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "plugins",
        "title": "Plugins",
        "summary": "Reinstalled from git source; only local-only trees are packed",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "themes",
        "title": "Themes",
        "summary": "Custom themes and git theme remotes (not stock)",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "hooks",
        "title": "Hooks and menu",
        "summary": "Omarchy hooks and menu extensions",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "terminals",
        "title": "Terminals",
        "summary": "foot, kitty, alacritty, ghostty configs",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "defaults",
        "title": "Defaults",
        "summary": "Browser, editor, agent, MIME handlers",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "packages",
        "title": "Packages",
        "summary": "Extra pacman and AUR packages on top of Omarchy",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "scripts",
        "title": "Scripts",
        "summary": "User scripts in ~/bin and ~/.local/bin (not fat binaries)",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "services",
        "title": "User services",
        "summary": "systemd --user units you added, plus enabled state",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "cli",
        "title": "CLI configs",
        "summary": "starship, git, btop, lazygit, XCompose, bashrc",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "projects",
        "title": "Projects",
        "summary": "Your git checkouts as clone recipes plus uncommitted patches",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "toolchains",
        "title": "Toolchains",
        "summary": "mise / cargo / go / npm tools as reinstall commands",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "wallpapers",
        "title": "Wallpaper overlays",
        "summary": "Extra images under ~/.config/omarchy/backgrounds — often huge",
        "default": False,
        "risk": "portable",
    },
    {
        "id": "webapps",
        "title": "Web apps",
        "summary": "Desktop entries under ~/.local/share/applications",
        "default": False,
        "risk": "portable",
    },
    {
        "id": "nvim",
        "title": "Neovim",
        "summary": "Neovim config (can be large; not the plugin cache)",
        "default": False,
        "risk": "portable",
    },
    {
        "id": "monitors",
        "title": "Monitors",
        "summary": "This machine's display layout — usually wrong on another box",
        "default": False,
        "risk": "host",
    },
    {
        "id": "input",
        "title": "Pointer and keyboard",
        "summary": "Touchpad, mouse, repeat — tuned per device",
        "default": False,
        "risk": "host",
    },
    {
        "id": "system",
        "title": "System layer",
        "summary": "Enabled system services and your /etc changes — needs root",
        "default": False,
        "risk": "host",
    },
    {
        "id": "identity",
        "title": "Identity",
        "summary": "Hostname only. Timezone and locale stay on the new box.",
        "default": False,
        "risk": "identity",
    },
    {
        "id": "secrets",
        "title": "Secrets",
        "summary": "SSH keys. Off on purpose. Never copies rclone/Tesla tokens.",
        "default": False,
        "risk": "secrets",
    },
]


def category_by_id(cid: str) -> dict:
    for item in CATEGORIES:
        if item["id"] == cid:
            return item
    raise KeyError(cid)


def catalog_payload() -> list[dict]:
    return [dict(item) for item in CATEGORIES]


def run(cmd: list[str], cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


def ensure_session_env() -> None:
    os.environ.setdefault("OMARCHY_PATH", "/usr/share/omarchy")
    uid = os.getuid()
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    if not os.environ.get("WAYLAND_DISPLAY"):
        for sock in sorted(runtime.glob("wayland-*")):
            if sock.name.endswith(".lock"):
                continue
            os.environ["WAYLAND_DISPLAY"] = sock.name
            break
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return
    proc = run(["hyprctl", "instances"])
    for line in proc.stdout.splitlines():
        if line.startswith("instance "):
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = line.split()[1].rstrip(":")
            break


def run_ok(cmd: list[str], cwd: Path | None = None) -> str:
    proc = run(cmd, cwd=cwd)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M")


def new_undo_dir(root: Path) -> Path:
    """Second resolution plus a counter: two restores a minute apart must not
    share an undo directory, or the second overwrites the first's originals."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = root / f"undo-{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = root / f"undo-{stamp}-{suffix}"
        suffix += 1
    return candidate


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def is_skipped_name(name: str) -> bool:
    if name in EXCLUDE_DIR_NAMES:
        return True
    if name.endswith("~"):
        return True
    if ".bak" in name or ".stock" in name or ".pre-" in name:
        return True
    if re.search(r"\.\d{10,}$", name):
        return True
    return False


def is_probably_text(path: Path, limit: int = TEXT_LIMIT) -> bool:
    try:
        data = path.read_bytes()[:4096]
    except OSError:
        return False
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    try:
        return path.stat().st_size <= limit * 4
    except OSError:
        return False


def https_git_url(url: str) -> str:
    url = (url or "").strip()
    m = re.match(r"git@github\.com:(.+?)(?:\.git)?$", url)
    if m:
        return f"https://github.com/{m.group(1)}.git"
    m = re.match(r"ssh://git@github\.com/(.+?)(?:\.git)?$", url)
    if m:
        return f"https://github.com/{m.group(1)}.git"
    return url


def git_remote(path: Path) -> str:
    if not (path / ".git").exists():
        return ""
    url = run_ok(["git", "-C", str(path), "remote", "get-url", "origin"]).strip()
    return https_git_url(url)


def git_head(path: Path) -> str:
    if not (path / ".git").exists():
        return ""
    return run_ok(["git", "-C", str(path), "rev-parse", "HEAD"]).strip()


def git_dirty(path: Path) -> bool:
    if not (path / ".git").exists():
        return False
    return bool(run_ok(["git", "-C", str(path), "status", "--porcelain"]).strip())


def git_changed_files(path: Path) -> list[str]:
    """Tracked modifications plus untracked authored files, relative to the repo.

    Upstream has no copy of these, so a from-source reinstall would silently
    discard them. Deletions are not represented; re-adding a file upstream
    still has is the safe direction.
    """
    if not (path / ".git").exists():
        return []
    out = run_ok(["git", "-C", str(path), "status", "--porcelain", "-z", "--untracked-files=all"])
    names: list[str] = []
    for entry in out.split("\0"):
        if len(entry) < 4:
            continue
        code, name = entry[:2], entry[3:]
        if code[0] == "D" or code[1] == "D":
            continue
        if is_skipped_name(Path(name).name):
            continue
        if any(is_skipped_name(part) for part in Path(name).parts):
            continue
        names.append(name)
    return names


def omarchy_version() -> str:
    text = run_ok(["omarchy", "version"]).strip()
    return text.splitlines()[0] if text else "unknown"


def current_theme() -> str:
    return run_ok(["omarchy", "theme", "current"]).strip() or read_text(Path.home() / ".config/omarchy/theme.name").strip()


def current_font() -> str:
    return run_ok(["omarchy", "font", "current"]).strip()


def dmi_product() -> str:
    return read_text(Path("/sys/class/dmi/id/product_name")).strip()


def hostname() -> str:
    return run_ok(["hostname"]).strip() or os.uname().nodename


def load_package_baselines() -> set[str]:
    names: set[str] = set()
    root = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy"))
    for name in ("omarchy-base.packages", "omarchy-other.packages"):
        path = root / "install" / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                names.add(line)
    return names


CORE_PACKAGES = {
    "efibootmgr",
    "intel-ucode",
    "amd-ucode",
    "linux",
    "linux-headers",
    "linux-firmware",
    "mkinitcpio",
    "sudo",
    "usbutils",
}


def is_stock_package(name: str, base: set[str]) -> bool:
    if name in base or name in CORE_PACKAGES:
        return True
    if name == "omarchy" or name.startswith("omarchy-"):
        return True
    return False


def extra_packages() -> dict:
    explicit = [line for line in run_ok(["pacman", "-Qqe"]).splitlines() if line]
    aur = set(line for line in run_ok(["pacman", "-Qmq"]).splitlines() if line)
    base = load_package_baselines()
    extra_aur = sorted(pkg for pkg in explicit if pkg in aur and not is_stock_package(pkg, base))
    extra_repo = sorted(
        pkg for pkg in explicit if pkg not in aur and not is_stock_package(pkg, base)
    )
    return {
        "explicit": explicit,
        "repo": extra_repo,
        "aur": extra_aur,
        "skipped_stock": sorted(pkg for pkg in explicit if is_stock_package(pkg, base)),
    }


def rewrite_text(text: str, old_home: str, new_home: str) -> str:
    """Replace the old home path only at a path boundary.

    A plain substring swap turned /home/pip/shared into /home/alicep/shared
    when migrating /home/pi -> /home/alice.
    """
    if not old_home or old_home == new_home:
        return text
    pattern = re.escape(old_home) + r"(?=$|[^A-Za-z0-9_.-])"
    return re.sub(pattern, new_home.replace("\\", "\\\\"), text)


FAILURES: list[str] = []


def fail(message: str) -> str:
    """Record a real failure and return it for the action log.

    Restore used to fold every failed command into a string and still report
    ok:true with exit 0, so an unattended restore looked clean while packages,
    services and the bar config had all refused.
    """
    FAILURES.append(message)
    return message


class UnsafePath(Exception):
    """An archive asked to touch a path outside where its category may write."""


def safe_segment(name: str) -> bool:
    """A single path component with no separators and no traversal."""
    if not name or name in {".", ".."}:
        return False
    if "/" in name or "\\" in name or "\0" in name:
        return False
    return True


def contained(base: Path, candidate: Path) -> Path:
    """Resolve candidate and refuse anything that escapes base."""
    base_r = base.resolve()
    cand_r = candidate.resolve()
    if cand_r != base_r and base_r not in cand_r.parents:
        raise UnsafePath(f"{candidate} escapes {base}")
    return cand_r


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    broken_link = src.is_symlink() and not src.exists()
    # Never write through a symlink at the destination, and never symlink onto
    # an existing path -- both raise or corrupt an unrelated file.
    if dest.is_symlink() or (broken_link and dest.exists()):
        dest.unlink()
    if broken_link:
        dest.symlink_to(os.readlink(src))
        return
    shutil.copy2(src, dest, follow_symlinks=True)


def read_text_safe(path: Path, limit: int = TEXT_LIMIT * 4) -> str | None:
    """Whole-file text read. None when binary, undecodable, oversized or unreadable."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > limit:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def rewrite_in_place(dest: Path, old_home: str, new_home: str) -> None:
    if not old_home or old_home == new_home:
        return
    text = read_text_safe(dest)
    if text is None:
        return
    rewritten = rewrite_text(text, old_home, new_home)
    if rewritten != text:
        try:
            dest.write_text(rewritten, encoding="utf-8")
        except OSError:
            pass


def iter_files(root: Path):
    if not root.exists():
        return
    if root.is_file() or root.is_symlink():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if not is_skipped_name(name)]
        for name in filenames:
            if is_skipped_name(name):
                continue
            candidate = Path(dirpath) / name
            if not packable_symlink(candidate):
                continue
            yield candidate


def rel_under_home(path: Path, home: Path) -> str:
    """Keep symlink locations. Resolving would pack ~/bin/plonk as Projects/plonk/plonk."""
    home_abs = home if home.is_absolute() else home.resolve()
    candidate = path if path.is_absolute() else (home_abs / path)
    try:
        return str(candidate.absolute().relative_to(home_abs))
    except ValueError:
        try:
            return str(candidate.resolve().relative_to(home_abs.resolve()))
        except ValueError:
            return str(path)


def stage_path(cat_dir: Path, rel: str) -> Path:
    return cat_dir / "files" / rel


def packable_symlink(src: Path) -> bool:
    """tarfile's `data` extraction filter rejects absolute link targets, so an
    archive containing one cannot be extracted at all. Refuse to pack those."""
    if not src.is_symlink():
        return True
    if src.exists():
        return True  # dereferenced into a regular file
    return not os.readlink(src).startswith("/")


def copy_into_category(cat_dir: Path, src: Path, home: Path) -> str | None:
    if not src.exists() and not src.is_symlink():
        return None
    if not packable_symlink(src):
        return None
    rel = rel_under_home(src, home)
    dest = stage_path(cat_dir, rel)
    if src.is_dir() and not src.is_symlink():
        count = 0
        for file_path in iter_files(src):
            file_rel = rel_under_home(file_path, home)
            copy_file(file_path, stage_path(cat_dir, file_rel))
            count += 1
        return f"{rel}/ ({count} files)" if count else None
    if src.is_dir() and src.is_symlink():
        real = src.resolve()
        for file_path in iter_files(real):
            try:
                inner = file_path.relative_to(real)
            except ValueError:
                continue
            copy_file(file_path, stage_path(cat_dir, str(Path(rel) / inner)))
        return f"{rel}/ (symlink -> {real})"
    copy_file(src, dest)
    return rel


def plugin_list() -> list[dict]:
    raw = run_ok(["omarchy", "plugin", "list", "--json"])
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    return []


def classify_plugin(plugin_dir: Path, listing: dict | None) -> dict:
    listing = listing or {}
    manifest_path = plugin_dir / "manifest.json"
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    cloned_from = ""
    omarchy_meta = manifest.get("omarchy") if isinstance(manifest.get("omarchy"), dict) else {}
    cloned_from = str(omarchy_meta.get("clonedFrom") or listing.get("clonedFrom") or "")
    real = plugin_dir
    if plugin_dir.is_symlink():
        try:
            real = plugin_dir.resolve()
        except OSError:
            real = plugin_dir
    url = git_remote(real)
    commit = git_head(real)
    dirty = git_dirty(real)
    if url:
        kind = "git"
    elif cloned_from:
        kind = "clone"
    else:
        kind = "local"
    return {
        "id": listing.get("id") or manifest.get("id") or plugin_dir.name,
        "name": listing.get("name") or manifest.get("name") or plugin_dir.name,
        "kind": kind,
        "url": url,
        "commit": commit,
        "dirty": dirty,
        "clonedFrom": cloned_from,
        "enabled": bool(listing.get("enabled")),
        "firstParty": bool(listing.get("firstParty")),
        "symlink": plugin_dir.is_symlink(),
        "source": str(real),
    }


def enabled_user_units() -> set[str]:
    text = run_ok(["systemctl", "--user", "list-unit-files", "--state=enabled", "--no-legend"])
    names = set()
    for line in text.splitlines():
        name = line.split()[0] if line.split() else ""
        if name.endswith(".service") or name.endswith(".timer") or name.endswith(".socket"):
            names.add(name)
    return names


def stock_unit(name: str) -> bool:
    stem = name.split(".")[0]
    return any(stem.startswith(prefix) for prefix in SKIP_UNIT_PREFIXES)


def machine_facts() -> dict:
    home = Path.home()
    return {
        "kind": KIND,
        "schema": SCHEMA,
        "created": iso_now(),
        "hostname": hostname(),
        "user": os.environ.get("USER") or Path.home().name,
        "home": str(home),
        "omarchy": omarchy_version(),
        "kernel": os.uname().release,
        "arch": os.uname().machine,
        "hardware": dmi_product(),
        "theme": current_theme(),
        "font": current_font(),
    }


def collect_look(cat_dir: Path, home: Path) -> dict:
    files = []
    for rel in (
        ".config/hypr/looknfeel.lua",
        ".config/omarchy/theme.name",
        ".config/omarchy/defaults",
        ".config/omarchy/branding",
    ):
        noted = copy_into_category(cat_dir, home / rel, home)
        if noted:
            files.append(noted)
    meta = {
        "theme": current_theme(),
        "font": current_font(),
        "files": files,
    }
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_wallpapers(cat_dir: Path, home: Path) -> dict:
    files = []
    noted = copy_into_category(cat_dir, home / ".config/omarchy/backgrounds", home)
    if noted:
        files.append(noted)
    meta = {"files": files}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_hyprland(cat_dir: Path, home: Path) -> dict:
    hypr = home / ".config/hypr"
    files = []
    skip = {"monitors.lua", "input.lua", "looknfeel.lua"}
    if hypr.is_dir():
        for path in sorted(hypr.iterdir()):
            if path.name in skip or is_skipped_name(path.name):
                continue
            noted = copy_into_category(cat_dir, path, home)
            if noted:
                files.append(noted)
    meta = {"files": files}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_bar(cat_dir: Path, home: Path) -> dict:
    files = []
    noted = copy_into_category(cat_dir, home / ".config/omarchy/shell.json", home)
    if noted:
        files.append(noted)
    shell = {}
    shell_path = home / ".config/omarchy/shell.json"
    if shell_path.is_file():
        try:
            shell = json.loads(shell_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            shell = {}
    layout = ((shell.get("bar") or {}).get("layout")) or {}
    meta = {
        "files": files,
        "barId": (shell.get("bar") or {}).get("id"),
        "disabledPlugins": shell.get("disabledPlugins") or [],
        "pluginModules": [
            item.get("id") if isinstance(item, dict) else item for item in (shell.get("plugins") or [])
        ],
        "layout": {
            section: [
                item.get("id") if isinstance(item, dict) else item for item in (items or [])
            ]
            for section, items in layout.items()
        },
    }
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_plugins(cat_dir: Path, home: Path) -> dict:
    plugins_root = home / ".config/omarchy/plugins"
    listing = {item.get("id"): item for item in plugin_list() if item.get("id")}
    records = []
    if plugins_root.is_dir():
        for plugin_dir in sorted(plugins_root.iterdir()):
            if not plugin_dir.is_dir() and not plugin_dir.is_symlink():
                continue
            if is_skipped_name(plugin_dir.name):
                continue
            info = classify_plugin(plugin_dir, listing.get(plugin_dir.name))
            real = Path(info["source"])
            # Recipe, not payload: anything with a git remote is re-installed
            # from source at restore. Only trees with no upstream get packed,
            # because nothing else could bring them back.
            if info["kind"] == "git" and info["url"]:
                # ...except uncommitted local work, which upstream does not have.
                # Pack just the changed files as an overlay, not the whole tree.
                changed = git_changed_files(real)
                if changed:
                    overlay_rel = Path("overlays") / info["id"]
                    dest_root = cat_dir / overlay_rel
                    copied = 0
                    for inner in changed:
                        src = real / inner
                        if not src.is_file():
                            continue
                        copy_file(src, dest_root / inner)
                        copied += 1
                    info["overlay"] = str(overlay_rel)
                    info["overlayFiles"] = sorted(changed)
                    info["files"] = copied
                else:
                    info["overlay"] = ""
                    info["files"] = 0
                info["tree"] = ""
                info["packed"] = False
                records.append(info)
                continue
            tree_rel = Path("trees") / info["id"]
            dest_root = cat_dir / tree_rel
            copied = 0
            for file_path in iter_files(real):
                try:
                    inner = file_path.relative_to(real)
                except ValueError:
                    continue
                copy_file(file_path, dest_root / inner)
                copied += 1
            info["tree"] = str(tree_rel)
            info["files"] = copied
            info["packed"] = True
            records.append(info)
    meta = {
        "plugins": records,
        "count": len(records),
        "fromSource": sorted(p["id"] for p in records if not p.get("packed")),
        "packed": sorted(p["id"] for p in records if p.get("packed")),
    }
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_themes(cat_dir: Path, home: Path) -> dict:
    themes_root = home / ".config/omarchy/themes"
    records = []
    if themes_root.is_dir():
        for theme_dir in sorted(themes_root.iterdir()):
            if not theme_dir.is_dir():
                continue
            url = git_remote(theme_dir)
            rec = {
                "id": theme_dir.name,
                "url": url,
                "commit": git_head(theme_dir),
                "kind": "git" if url else "local",
            }
            if not url:
                copied = copy_into_category(cat_dir, theme_dir, home)
                rec["copied"] = copied
            records.append(rec)
    meta = {"themes": records, "current": current_theme()}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_hooks(cat_dir: Path, home: Path) -> dict:
    files = []
    for rel in (
        ".config/omarchy/hooks",
        ".config/omarchy/extensions",
    ):
        noted = copy_into_category(cat_dir, home / rel, home)
        if noted:
            files.append(noted)
    meta = {"files": files}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_terminals(cat_dir: Path, home: Path) -> dict:
    files = []
    for rel in (
        ".config/foot/foot.ini",
        ".config/kitty/kitty.conf",
        ".config/alacritty/alacritty.toml",
        ".config/ghostty/config",
    ):
        noted = copy_into_category(cat_dir, home / rel, home)
        if noted:
            files.append(noted)
    meta = {"files": files}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_defaults(cat_dir: Path, home: Path) -> dict:
    files = []
    for rel in (
        ".config/mimeapps.list",
        ".config/omarchy/defaults",
        ".config/xdg-terminals.list",
    ):
        noted = copy_into_category(cat_dir, home / rel, home)
        if noted:
            files.append(noted)
    meta = {"files": files}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_packages(cat_dir: Path, home: Path) -> dict:
    meta = extra_packages()
    write_json(cat_dir / "meta.json", meta)
    write_json(cat_dir / "packages.json", meta)
    return meta


def script_should_keep(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    if not path.is_file():
        return False
    if st.st_size > SCRIPT_LIMIT:
        return False
    if not is_probably_text(path, SCRIPT_LIMIT):
        return False
    # Skip obvious tool shims that belong to a version manager, not the desktop.
    name = path.name
    if name in {"bun", "npm", "node", "pip", "python", "python3"}:
        return False
    return True


def repo_root_for(path: Path, home: Path) -> Path | None:
    """The git checkout a path lives in, if any, bounded to $HOME."""
    try:
        current = path.resolve()
    except OSError:
        return None
    home_abs = home.resolve()
    while current != current.parent and home_abs in current.parents:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def collect_scripts(cat_dir: Path, home: Path) -> dict:
    kept = []
    links = []
    skipped = []
    for folder in (home / "bin", home / ".local/bin"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() and not path.is_symlink():
                continue
            if is_skipped_name(path.name):
                continue
            # A symlink into a checkout is a link to that repo, not a file.
            # Flattening it produced a standalone copy cut off from the rest of
            # its tree -- that is how imprint's own wrapper lost its engine.
            if path.is_symlink():
                repo = repo_root_for(path, home)
                if repo is not None:
                    links.append({
                        "link": rel_under_home(path, home),
                        "target": rel_under_home(path.resolve(), home),
                        "repo": rel_under_home(repo, home),
                        "url": git_remote(repo),
                    })
                    continue
            if script_should_keep(path):
                noted = copy_into_category(cat_dir, path, home)
                if noted:
                    kept.append(noted)
            else:
                skipped.append({"path": rel_under_home(path, home), "size": path.stat().st_size if path.exists() else 0})
    meta = {"files": kept, "links": links, "skipped_binaries": skipped}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_services(cat_dir: Path, home: Path) -> dict:
    root = home / ".config/systemd/user"
    enabled = enabled_user_units()
    units = []
    seen_dropins: set[str] = set()
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if path.suffix in {".service", ".timer", ".socket"}:
                if stock_unit(path.name):
                    continue
                copy_into_category(cat_dir, path, home)
                dropin = root / f"{path.name}.d"
                if dropin.is_dir():
                    copy_into_category(cat_dir, dropin, home)
                    seen_dropins.add(dropin.name)
                units.append({"name": path.name, "enabled": path.name in enabled})
            elif path.is_dir() and path.name.endswith(".d") and path.name not in seen_dropins:
                if stock_unit(path.name.removesuffix(".d")):
                    continue
                copy_into_category(cat_dir, path, home)
    meta = {"units": units}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_cli(cat_dir: Path, home: Path) -> dict:
    files = []
    for rel in (
        ".config/starship.toml",
        ".config/git/config",
        ".gitconfig",
        ".config/btop/btop.conf",
        ".config/lazygit/config.yml",
        ".XCompose",
        ".bashrc",
        ".bash_profile",
        ".inputrc",
    ):
        noted = copy_into_category(cat_dir, home / rel, home)
        if noted:
            files.append(noted)
    meta = {"files": files}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_webapps(cat_dir: Path, home: Path) -> dict:
    apps = home / ".local/share/applications"
    files = []
    if apps.is_dir():
        for path in sorted(apps.glob("*.desktop")):
            noted = copy_into_category(cat_dir, path, home)
            if noted:
                files.append(noted)
    meta = {"files": files}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_nvim(cat_dir: Path, home: Path) -> dict:
    nvim = home / ".config/nvim"
    files = []
    if nvim.is_dir():
        noted = copy_into_category(cat_dir, nvim, home)
        if noted:
            files.append(noted)
    meta = {"files": files}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_monitors(cat_dir: Path, home: Path) -> dict:
    files = []
    noted = copy_into_category(cat_dir, home / ".config/hypr/monitors.lua", home)
    if noted:
        files.append(noted)
    meta = {"files": files, "hostBound": True}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_input(cat_dir: Path, home: Path) -> dict:
    files = []
    noted = copy_into_category(cat_dir, home / ".config/hypr/input.lua", home)
    if noted:
        files.append(noted)
    meta = {"files": files, "hostBound": True}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_identity(cat_dir: Path, home: Path) -> dict:
    meta = {
        "hostname": hostname(),
        "timezone": read_text(Path("/etc/timezone")).strip() or run_ok(["timedatectl", "show", "-p", "Timezone", "--value"]).strip(),
        "note": "Restore only writes hostname, and only with an extra confirm.",
    }
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_secrets(cat_dir: Path, home: Path) -> dict:
    ssh = home / ".ssh"
    files = []
    skipped = []
    if ssh.is_dir():
        for path in sorted(ssh.iterdir()):
            if path.name in {"authorized_keys", "config", "known_hosts"} or path.name.endswith(".pub"):
                noted = copy_into_category(cat_dir, path, home)
                if noted:
                    files.append(noted)
            elif path.is_file() and path.name not in {"known_hosts.old"}:
                skipped.append(path.name)
    meta = {
        "files": files,
        "private_keys_present_not_copied": skipped,
        "note": "Private keys stay on the source machine unless you copy them yourself.",
    }
    write_json(cat_dir / "meta.json", meta)
    return meta



PROJECT_ROOTS = ("Projects", "Work")
PATCH_LIMIT = 512 * 1024

# /etc paths worth carrying. Everything here is policy you wrote, not package
# content and not credentials.
ETC_DIRS = (
    "systemd/system",
    "systemd/logind.conf.d",
    "systemd/sleep.conf.d",
    "systemd/system.conf.d",
    "sddm.conf.d",
    "ssh/sshd_config.d",
    "pacman.d/hooks",
    "modprobe.d",
    "udev/rules.d",
    "NetworkManager/conf.d",
    "docker",
    "ufw",
)
UFW_FILES = {
    "user.rules", "user6.rules", "after.rules", "after6.rules",
    "before.rules", "before6.rules", "ufw.conf", "sysctl.conf",
}
ETC_FILES = ("pacman.conf", "locale.gen", "environment", "vconsole.conf")
# Never leaves the machine, whatever else matches.
ETC_DENY_PARTS = (
    "shadow", "gshadow", "passwd", "group", "sudoers",
    "system-connections", "private", "secrets",
)
ETC_DENY_SUFFIX = (".key", ".pem", ".p12", ".pfx", ".crt", ".gpg")


def etc_is_denied(path: Path) -> bool:
    text = str(path)
    if any(part in text for part in ETC_DENY_PARTS):
        return True
    if path.name.startswith("ssh_host_"):
        return True
    return path.suffix in ETC_DENY_SUFFIX


def package_owns(path: Path) -> bool:
    return run(["pacman", "-Qo", str(path)]).returncode == 0


def enabled_system_units() -> list[str]:
    text = run_ok(["systemctl", "list-unit-files", "--state=enabled", "--no-legend"])
    names = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        name = parts[0]
        # Templates cannot be enabled by name; instances would need their own record.
        if name.endswith("@.service") or name.endswith("@.socket"):
            continue
        names.append(name)
    return sorted(names)


def collect_system(cat_dir: Path, home: Path) -> dict:
    etc = Path("/etc")
    kept, skipped = [], []
    for rel in ETC_DIRS:
        root = etc / rel
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if is_skipped_name(path.name) or any(is_skipped_name(x) for x in path.parts):
                continue
            if etc_is_denied(path):
                skipped.append(str(path))
                continue
            # ufw and docker rewrite package files in place, so those are kept
            # by name; everything else is only interesting when unowned.
            if rel == "ufw":
                if path.name not in UFW_FILES and package_owns(path):
                    continue
            elif rel == "docker":
                if path.name != "daemon.json" and package_owns(path):
                    continue
            elif package_owns(path):
                continue
            dest = cat_dir / "etc" / path.relative_to(etc)
            try:
                copy_file(path, dest)
            except OSError:
                skipped.append(f"{path} (unreadable)")
                continue
            kept.append(str(path.relative_to(etc)))
    for name in ETC_FILES:
        path = etc / name
        if path.is_file() and not etc_is_denied(path):
            try:
                copy_file(path, cat_dir / "etc" / name)
                kept.append(name)
            except OSError:
                skipped.append(f"{path} (unreadable)")
    meta = {
        "enabledUnits": enabled_system_units(),
        "etcFiles": kept,
        "etcSkipped": skipped,
        "note": "Restoring this needs root. Credentials and host keys are never collected.",
    }
    write_json(cat_dir / "meta.json", meta)
    return meta


def git_branch(path: Path) -> str:
    name = run_ok(["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    return "" if name == "HEAD" else name


def git_unpushed(path: Path) -> int:
    out = run_ok(["git", "-C", str(path), "rev-list", "--count", "@{upstream}..HEAD"]).strip()
    try:
        return int(out)
    except ValueError:
        return 0


def collect_projects(cat_dir: Path, home: Path) -> dict:
    records = []
    for root_name in PROJECT_ROOTS:
        root = home / root_name
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not (entry / ".git").exists() or is_skipped_name(entry.name):
                continue
            url = git_remote(entry)
            rel = rel_under_home(entry, home)
            rec = {
                "path": rel,
                "name": entry.name,
                "url": url,
                "branch": git_branch(entry),
                "commit": git_head(entry),
                "dirty": git_dirty(entry),
                "unpushed": git_unpushed(entry),
                "changed": git_changed_files(entry),
            }
            # Uncommitted work has no upstream copy, so carry it as a patch.
            if rec["dirty"]:
                diff = run_ok(["git", "-C", str(entry), "diff", "HEAD"])
                if diff and len(diff.encode("utf-8")) <= PATCH_LIMIT:
                    patch_rel = Path("patches") / f"{entry.name}.patch"
                    dest = cat_dir / patch_rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(diff, encoding="utf-8")
                    rec["patch"] = str(patch_rel)
                elif diff:
                    rec["patchSkipped"] = f"diff is {len(diff)} bytes, over the {PATCH_LIMIT} limit"
            if not url:
                rec["warning"] = "no git remote; this checkout cannot be recreated from source"
            records.append(rec)
    meta = {
        "repos": records,
        "count": len(records),
        "withoutRemote": sorted(r["path"] for r in records if not r["url"]),
        "unpushed": sorted(r["path"] for r in records if r["unpushed"]),
    }
    write_json(cat_dir / "meta.json", meta)
    return meta


def go_module_for(binary: Path) -> str:
    out = run_ok(["go", "version", "-m", str(binary)])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "path":
            return parts[1] if parts[1] != "path" else parts[2]
        if len(parts) >= 2 and parts[0] == "path":
            return parts[1]
    return ""


def collect_toolchains(cat_dir: Path, home: Path) -> dict:
    files = []
    for rel in (".config/mise/config.toml", ".tool-versions"):
        noted = copy_into_category(cat_dir, home / rel, home)
        if noted:
            files.append(noted)
    mise = []
    for line in run_ok(["mise", "ls", "--current"]).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            mise.append({"tool": parts[0], "version": parts[1]})
    cargo = []
    for line in run_ok(["cargo", "install", "--list"]).splitlines():
        if line and not line.startswith(" ") and line.endswith(":"):
            cargo.append(line.rstrip(":").split()[0])
    go_tools = []
    gobin = home / "go/bin"
    if gobin.is_dir():
        for binary in sorted(gobin.iterdir()):
            if not binary.is_file():
                continue
            module = go_module_for(binary)
            go_tools.append({"name": binary.name, "module": module})
    npm = []
    for line in run_ok(["npm", "ls", "-g", "--depth=0", "--parseable"]).splitlines():
        name = Path(line).name
        if name and name != "lib" and name != "npm":
            npm.append(name)
    meta = {
        "files": files,
        "mise": mise,
        "cargo": cargo,
        "go": go_tools,
        "npmGlobal": npm,
        "unresolvedGo": sorted(t["name"] for t in go_tools if not t["module"]),
    }
    write_json(cat_dir / "meta.json", meta)
    return meta


COLLECTORS = {
    "look": collect_look,
    "wallpapers": collect_wallpapers,
    "hyprland": collect_hyprland,
    "bar": collect_bar,
    "plugins": collect_plugins,
    "themes": collect_themes,
    "hooks": collect_hooks,
    "terminals": collect_terminals,
    "defaults": collect_defaults,
    "packages": collect_packages,
    "scripts": collect_scripts,
    "services": collect_services,
    "cli": collect_cli,
    "webapps": collect_webapps,
    "nvim": collect_nvim,
    "monitors": collect_monitors,
    "input": collect_input,
    "identity": collect_identity,
    "secrets": collect_secrets,
    "projects": collect_projects,
    "toolchains": collect_toolchains,
    "system": collect_system,
}


def default_category_ids() -> list[str]:
    return [item["id"] for item in CATEGORIES if item["default"]]


def parse_only(value: str | None) -> list[str]:
    if not value or value.strip() in {"*", "all"}:
        return [item["id"] for item in CATEGORIES]
    ids = []
    known = {item["id"] for item in CATEGORIES}
    for part in value.split(","):
        cid = part.strip()
        if not cid:
            continue
        if cid not in known:
            raise SystemExit(f"unknown category: {cid}")
        ids.append(cid)
    return ids


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for file_path in iter_files(path):
        try:
            total += file_path.stat().st_size
        except OSError:
            pass
    return total


def render_brief(manifest: dict) -> str:
    cats = manifest.get("categories") or {}
    lines = [
        f"# Imprint of {manifest.get('hostname') or 'an Omarchy machine'}",
        "",
        "Hand this file to a person or an agent. It describes the machine.",
        "Applying it is `imprint restore` — do not blindly copy home directories.",
        "",
        "```toml",
        f"schema = {manifest.get('schema')}",
        f"created = \"{manifest.get('created')}\"",
        f"hostname = \"{manifest.get('hostname')}\"",
        f"omarchy = \"{manifest.get('omarchy')}\"",
        f"hardware = \"{manifest.get('hardware')}\"",
        f"theme = \"{manifest.get('theme')}\"",
        f"font = \"{manifest.get('font')}\"",
        "```",
        "",
        "## Selected categories",
        "",
    ]
    for cid, info in cats.items():
        if not info:
            continue
        title = category_by_id(cid)["title"] if cid in {c["id"] for c in CATEGORIES} else cid
        lines.append(f"- **{title}** (`{cid}`)")
    plugins = ((cats.get("plugins") or {}).get("plugins")) or []
    if plugins:
        lines += ["", "## Plugins", ""]
        for plug in plugins:
            if plug.get("kind") == "git" and plug.get("url"):
                enable = " --enable" if plug.get("enabled") else ""
                commit = (plug.get("commit") or "")[:12]
                seen = f" (saved at {commit})" if commit else ""
                lines.append(f"- `{plug['id']}` installed from source{seen}")
                lines.append(f"  `omarchy plugin add {plug['url']}{enable}`")
            elif plug.get("kind") == "clone":
                lines.append(f"- `{plug['id']}` cloned from `{plug.get('clonedFrom')}`")
            else:
                lines.append(f"- `{plug['id']}` local tree, packed in this archive")
    pkgs = cats.get("packages") or {}
    if pkgs.get("repo") or pkgs.get("aur"):
        lines += ["", "## Extra packages", ""]
        if pkgs.get("repo"):
            lines.append("```bash")
            lines.append("omarchy pkg add " + " ".join(pkgs["repo"]))
            lines.append("```")
        if pkgs.get("aur"):
            lines.append("```bash")
            lines.append("omarchy pkg aur add " + " ".join(pkgs["aur"]))
            lines.append("```")
    projects = ((cats.get("projects") or {}).get("repos")) or []
    if projects:
        lines += ["", f"## Projects ({len(projects)} checkouts)", ""]
        for repo in projects[:40]:
            if repo.get("url"):
                extra = " +patch" if repo.get("patch") else ""
                lines.append(f"- `{repo['path']}` `git clone {repo['url']}`{extra}")
            else:
                lines.append(f"- `{repo['path']}` **no remote — not recoverable from this imprint**")
        if len(projects) > 40:
            lines.append(f"- ... {len(projects) - 40} more")
    tools = cats.get("toolchains") or {}
    if tools.get("mise") or tools.get("go") or tools.get("cargo"):
        lines += ["", "## Toolchains", ""]
        if tools.get("mise"):
            lines.append("```bash")
            lines.append("mise install   # " + ", ".join(f"{t['tool']}@{t['version']}" for t in tools["mise"][:12]))
            lines.append("```")
        for tool in tools.get("go") or []:
            if tool.get("module"):
                lines.append(f"- `go install {tool['module']}@latest`")
        for crate in tools.get("cargo") or []:
            lines.append(f"- `cargo install {crate}`")
    system = cats.get("system") or {}
    if system.get("enabledUnits") or system.get("etcFiles"):
        lines += ["", "## System layer (needs root)", ""]
        lines.append(f"- {len(system.get('etcFiles') or [])} files under `/etc`")
        lines.append(f"- {len(system.get('enabledUnits') or [])} enabled system units")
        lines.append("- apply with `imprint restore FILE --only system --allow-system`")
    themes = ((cats.get("themes") or {}).get("themes")) or []
    git_themes = [t for t in themes if t.get("url")]
    if git_themes:
        lines += ["", "## Themes from git", ""]
        for theme in git_themes:
            lines.append(f"- `{theme['id']}` `omarchy theme install {theme['url']}`")
    lines += [
        "",
        "## Do not clone from this imprint",
        "",
        "- LUKS / TPM / disk unlock",
        "- Autologin and lock policy, unless you chose them elsewhere",
        "- rclone tokens, Tesla auth, browser profiles, mail",
        "- Private SSH keys (public keys only, and only if Secrets was selected)",
        "- Another machine's monitors and pointer speed unless you opted in",
        "",
        "Restore with `imprint restore <this-archive>` and space-select categories.",
        "",
    ]
    return "\n".join(lines)


def copy_tool(staging: Path) -> None:
    here = Path(__file__).resolve().parent
    tool = staging / "tool"
    tool.mkdir(parents=True, exist_ok=True)
    engine = Path(__file__).resolve()
    shutil.copy2(engine, tool / "imprint-engine.py")
    script = here / "imprint"
    if script.is_file():
        shutil.copy2(script, tool / "imprint")
        os.chmod(tool / "imprint", os.stat(tool / "imprint").st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def collect_selected(staging: Path, home: Path, ids: list[str]) -> dict:
    categories = {}
    for cid in ids:
        collector = COLLECTORS[cid]
        cat_dir = staging / "categories" / cid
        cat_dir.mkdir(parents=True, exist_ok=True)
        meta = collector(cat_dir, home)
        meta = dict(meta or {})
        meta["bytes"] = dir_size(cat_dir)
        categories[cid] = meta
        print(f"  collected {cid} ({human_size(meta['bytes'])})", file=sys.stderr)
    return categories


def human_size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def write_archive(staging: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    with tarfile.open(tmp, "w:zst") as tar:
        tar.add(staging, arcname=".")
    tmp.replace(dest)


def extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        tar.extractall(dest, filter="data")


def load_manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        raise SystemExit("not an imprint: missing manifest.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") != KIND:
        raise SystemExit(f"not an imprint: kind={data.get('kind')}")
    return data


def open_imprint(path: Path, tmp: Path) -> Path:
    if path.is_dir() and (path / "manifest.json").is_file():
        return path
    extract_archive(path, tmp)
    if (tmp / "manifest.json").is_file():
        return tmp
    # tarball may have a single top folder
    for child in tmp.iterdir():
        if child.is_dir() and (child / "manifest.json").is_file():
            return child
    raise SystemExit("archive did not contain an imprint manifest")


def cmd_categories(_args) -> int:
    json.dump(catalog_payload(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_facts(_args) -> int:
    json.dump(machine_facts(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def default_archive_path(home: Path, host: str) -> Path:
    return home / "imprints" / f"imprint-{host}-{now_stamp()}.tar.zst"


def cmd_save(args) -> int:
    home = Path.home()
    ids = parse_only(args.only) if args.only else default_category_ids()
    if args.all:
        ids = [item["id"] for item in CATEGORIES]
    dest = Path(args.output).expanduser() if args.output else default_archive_path(home, hostname())
    if dest.suffixes[-2:] != [".tar", ".zst"] and not str(dest).endswith(".tar.zst"):
        dest = dest.with_name(dest.name + ".tar.zst") if dest.suffix == "" else dest
    with tempfile.TemporaryDirectory(prefix="imprint-") as tmp:
        staging = Path(tmp) / "imprint"
        staging.mkdir()
        print("Collecting categories:", ", ".join(ids), file=sys.stderr)
        categories = collect_selected(staging, home, ids)
        manifest = machine_facts()
        manifest["categories"] = {cid: strip_heavy(categories[cid]) for cid in ids}
        manifest["archiveName"] = dest.name
        write_json(staging / "manifest.json", manifest)
        (staging / "BRIEF.md").write_text(render_brief(manifest), encoding="utf-8")
        copy_tool(staging)
        print(f"Writing {dest}", file=sys.stderr)
        write_archive(staging, dest)
    size = dest.stat().st_size
    result = {"ok": True, "path": str(dest), "bytes": size, "categories": ids}
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def strip_heavy(meta: dict) -> dict:
    data = dict(meta)
    # Recipes are the point of the archive; never truncate them.
    if "repos" in data or "mise" in data or "enabledUnits" in data:
        return data
    # Keep plugin index, drop per-file lists that bloat the manifest.
    if "files" in data and isinstance(data["files"], list) and len(data["files"]) > 40:
        data["fileCount"] = len(data["files"])
        data["files"] = data["files"][:20] + [f"... {len(data['files']) - 20} more"]
    return data


def backup_existing(src: Path, undo: Path, home: Path) -> None:
    if not src.exists() and not src.is_symlink():
        return
    rel = rel_under_home(src, home)
    dest = undo / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        # A symlink -- to a directory or a missing target -- must be recorded as
        # a link. copy2(follow_symlinks=True) on a directory symlink raises
        # IsADirectoryError, which aborted restore partway through.
        if dest.exists() or dest.is_symlink():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        dest.symlink_to(os.readlink(src))
        return
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True, symlinks=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest, follow_symlinks=True)


def restore_file_tree(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool) -> list[str]:
    files_root = cat_dir / "files"
    done = []
    if not files_root.is_dir():
        return done
    for path in iter_files(files_root):
        rel = path.relative_to(files_root)
        dest = home / rel
        try:
            contained(home, dest.parent)
        except UnsafePath:
            done.append(f"refused {rel} (escapes home)")
            continue
        if dry:
            done.append(str(rel))
            continue
        backup_existing(dest, undo, home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy_file(path, dest)
        rewrite_in_place(dest, old_home, str(home))
        done.append(str(rel))
    return done


def restore_plugins(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool) -> list[str]:
    meta_path = cat_dir / "meta.json"
    if not meta_path.is_file():
        return []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    plugins_root = home / ".config/omarchy/plugins"
    actions = []
    for plug in meta.get("plugins") or []:
        pid = plug.get("id")
        if not pid:
            continue
        # An archive is untrusted input: an id like "../../../Documents" would
        # otherwise be rmtree'd and replaced.
        if not safe_segment(pid):
            actions.append(f"refused unsafe plugin id {pid!r}")
            continue
        target = plugins_root / pid
        if dry:
            # Say exactly what apply would run, not just the category name.
            if plug.get("kind") == "git" and plug.get("url"):
                enable = " --enable" if plug.get("enabled") else ""
                actions.append(f"omarchy plugin add {plug['url']} --yes{enable}")
                n = len(plug.get("overlayFiles") or [])
                if n:
                    actions.append(f"  then reapply {n} local edits to {pid}: "
                                   + ", ".join((plug.get("overlayFiles") or [])[:4]))
            elif plug.get("tree"):
                actions.append(f"replace tree {pid} ({plug.get('files', 0)} packed files)")
            elif plug.get("clonedFrom"):
                actions.append(f"omarchy plugin clone {plug['clonedFrom']}")
            else:
                actions.append(f"plugin {pid} ({plug.get('kind')}) - nothing to install")
            continue
        if target.exists() or target.is_symlink():
            backup_existing(target, undo, home)
        if plug.get("kind") == "git" and plug.get("url"):
            cmd = ["omarchy", "plugin", "add", plug["url"], "--yes"]
            if plug.get("enabled"):
                cmd.append("--enable")
            proc = run(cmd)
            if proc.returncode != 0 and "already" not in (proc.stdout + proc.stderr).lower():
                actions.append(fail(f"plugin add failed {pid}: {(proc.stderr or proc.stdout).strip()[:200]}"))
            else:
                actions.append(f"plugin add {pid} from source")
            rel_overlay = plug.get("overlay") or ""
            if rel_overlay:
                try:
                    overlay = contained(cat_dir, cat_dir / rel_overlay)
                except UnsafePath:
                    actions.append(f"refused unsafe overlay {rel_overlay!r} for {pid}")
                    continue
                if overlay.is_dir():
                    overlay_tree(overlay, target, old_home, home)
                    actions.append(f"reapplied {len(plug.get('overlayFiles') or [])} local edits to {pid}")
        else:
            # A bare cat_dir is a directory too, so an empty tree must not
            # overlay the whole category onto the plugin path.
            rel_tree = plug.get("tree") or ""
            tree = None
            if rel_tree:
                try:
                    tree = contained(cat_dir, cat_dir / rel_tree)
                except UnsafePath:
                    actions.append(f"refused unsafe plugin tree {rel_tree!r} for {pid}")
                    continue
            if tree is not None and tree.is_dir():
                if target.exists() or target.is_symlink():
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                overlay_tree(tree, target, old_home, home)
                actions.append(f"plugin tree {pid}")
            elif plug.get("kind") == "clone" and plug.get("clonedFrom"):
                run(["omarchy", "plugin", "clone", plug["clonedFrom"]])
                actions.append(f"plugin clone {plug['clonedFrom']}")
    return actions


def overlay_tree(src: Path, dest: Path, old_home: str, new_home: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for file_path in iter_files(src):
        inner = file_path.relative_to(src)
        target = dest / inner
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_file(file_path, target)
        rewrite_in_place(target, old_home, str(new_home))


def restore_packages(cat_dir: Path, dry: bool) -> list[str]:
    meta_path = cat_dir / "meta.json"
    if not meta_path.is_file():
        return []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    actions = []
    repo = meta.get("repo") or []
    aur = meta.get("aur") or []
    if dry:
        if repo:
            actions.append("pkg add " + " ".join(repo))
        if aur:
            actions.append("pkg aur add " + " ".join(aur))
        return actions
    if repo:
        proc = run(["omarchy", "pkg", "add", *repo])
        if proc.returncode == 0:
            actions.append("pkg add ok")
        else:
            actions.append(fail("pkg add failed: " + (proc.stderr or proc.stdout).strip()[:400]))
    if aur:
        proc = run(["omarchy", "pkg", "aur", "add", *aur])
        if proc.returncode == 0:
            actions.append("pkg aur add ok")
        else:
            actions.append(fail("pkg aur add failed: " + (proc.stderr or proc.stdout).strip()[:400]))
    return actions


def restore_themes(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool) -> list[str]:
    meta_path = cat_dir / "meta.json"
    actions = []
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for theme in meta.get("themes") or []:
            if theme.get("url"):
                if dry:
                    actions.append(f"theme install {theme['url']}")
                else:
                    tid = theme.get("id") or ""
                    # omarchy-theme-install rm -rf's the destination BEFORE it
                    # clones, so an offline install destroys the existing theme
                    # with nothing to put back. Take an undo copy first.
                    if safe_segment(tid):
                        existing = home / ".config/omarchy/themes" / tid
                        if existing.exists() or existing.is_symlink():
                            backup_existing(existing, undo, home)
                    proc = run(["omarchy", "theme", "install", theme["url"]])
                    if proc.returncode == 0 or "already" in (proc.stdout + proc.stderr).lower():
                        actions.append(f"theme install {tid} ok")
                    else:
                        actions.append(fail(f"theme install {tid} failed: " + (proc.stderr or proc.stdout).strip()[:200]))
    actions.extend(restore_file_tree(cat_dir, home, old_home, undo, dry))
    return actions


def restore_look(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool, manifest: dict) -> list[str]:
    actions = restore_file_tree(cat_dir, home, old_home, undo, dry)
    meta_path = cat_dir / "meta.json"
    theme = manifest.get("theme")
    font = manifest.get("font")
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        theme = meta.get("theme") or theme
        font = meta.get("font") or font
    if dry:
        if theme:
            actions.append(f"theme set {theme}")
        if font:
            actions.append(f"font set {font}")
        return actions
    if theme:
        proc = run(["omarchy", "theme", "set", theme])
        actions.append("theme set ok" if proc.returncode == 0 else fail(f"theme set {theme} failed (missing?)"))
    if font:
        proc = run(["omarchy", "font", "set", font])
        actions.append("font set ok" if proc.returncode == 0 else fail(f"font set {font} failed (missing?)"))
    return actions


def shell_is_running() -> bool:
    return run(["omarchy", "shell", "-q", "shell", "ping"]).returncode == 0


def restore_bar(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool) -> list[str]:
    shell_src = cat_dir / "files/.config/omarchy/shell.json"
    if not shell_src.is_file():
        # packed relative without leading handling
        matches = list((cat_dir / "files").rglob("shell.json")) if (cat_dir / "files").is_dir() else []
        shell_src = matches[0] if matches else shell_src
    if not shell_src.is_file():
        return ["no shell.json in this imprint, bar left alone"]
    dest = home / ".config/omarchy/shell.json"
    if dry:
        return ["shell.json via config-edit"]
    text = rewrite_text(shell_src.read_text(encoding="utf-8"), old_home, str(home))

    # Never write dest directly while the shell is up. `config-edit` exists to
    # merge against a live snapshot; writing first would make the snapshot
    # reflect our own clobber and silently drop every concurrent edit.
    snap_fd, snap_name = tempfile.mkstemp(prefix="imprint-snap-", suffix=".json")
    edit_fd, edit_name = tempfile.mkstemp(prefix="imprint-edit-", suffix=".json")
    os.close(snap_fd)
    os.close(edit_fd)
    snap, edited = Path(snap_name), Path(edit_name)
    try:
        edited.write_text(text, encoding="utf-8")
        snap_proc = run(["omarchy", "shell", "config-edit", "snapshot", str(snap)])
        if snap_proc.returncode == 0:
            if dest.exists():
                backup_existing(dest, undo, home)
            apply = run(
                [
                    "omarchy",
                    "shell",
                    "config-edit",
                    "apply",
                    str(snap),
                    str(edited),
                    "--allow-layout-change",
                ]
            )
            if apply.returncode == 0:
                return ["shell.json via config-edit"]
            detail = (apply.stderr or apply.stdout).strip()[:200]
            return [fail(f"shell.json NOT applied, config-edit refused: {detail}")]
        # A failed snapshot is not proof the shell is stopped -- it could be an
        # IPC error or a timeout. Only write directly when the shell really is
        # down, otherwise refuse and say so.
        if shell_is_running():
            detail = (snap_proc.stderr or snap_proc.stdout).strip()[:200]
            return [fail(f"shell.json NOT applied: shell is up but snapshot failed: {detail}")]
        if dest.exists():
            backup_existing(dest, undo, home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return ["shell.json written directly (shell not running)"]
    finally:
        for path in (snap, edited):
            try:
                path.unlink()
            except OSError:
                pass


def restore_services(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool) -> list[str]:
    actions = restore_file_tree(cat_dir, home, old_home, undo, dry)
    meta_path = cat_dir / "meta.json"
    if dry or not meta_path.is_file():
        return actions
    run(["systemctl", "--user", "daemon-reload"])
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for unit in meta.get("units") or []:
        name = unit.get("name")
        if not name:
            continue
        if unit.get("enabled"):
            proc = run(["systemctl", "--user", "enable", "--now", name])
            if proc.returncode == 0:
                actions.append(f"enable {name} ok")
            else:
                actions.append(fail(f"enable {name} failed: " + (proc.stderr or proc.stdout).strip()[:200]))
    return actions


def restore_identity(cat_dir: Path, dry: bool, confirm_host: str | None) -> list[str]:
    meta_path = cat_dir / "meta.json"
    if not meta_path.is_file():
        return []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    wanted = meta.get("hostname")
    if not wanted:
        return []
    if dry:
        return [f"hostnamectl set-hostname {wanted}"]
    if confirm_host != wanted:
        return [f"skipped hostname {wanted} (pass --confirm-hostname {wanted})"]
    proc = run(["hostnamectl", "set-hostname", wanted])
    if proc.returncode == 0:
        return ["hostname ok"]
    return [fail("hostname failed: " + (proc.stderr or proc.stdout).strip()[:200])]



def restore_scripts(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool) -> list[str]:
    actions = restore_file_tree(cat_dir, home, old_home, undo, dry)
    meta_path = cat_dir / "meta.json"
    if not meta_path.is_file():
        return actions
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for entry in meta.get("links") or []:
        link_rel, target_rel = entry.get("link") or "", entry.get("target") or ""
        if not link_rel or not target_rel:
            continue
        try:
            link = contained(home, (home / link_rel).parent) / Path(link_rel).name
            target = contained(home, home / target_rel)
        except UnsafePath:
            actions.append(fail(f"refused link outside home: {link_rel!r} -> {target_rel!r}"))
            continue
        if dry:
            actions.append(f"ln -s ~/{target_rel} ~/{link_rel}")
            continue
        if not target.exists():
            actions.append(fail(
                f"~/{link_rel} not linked: ~/{target_rel} is missing"
                + (f" (restore Projects, or clone {entry['url']})" if entry.get("url") else "")
            ))
            continue
        if link.exists() or link.is_symlink():
            backup_existing(link, undo, home)
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        actions.append(f"linked ~/{link_rel} -> ~/{target_rel}")
    return actions


def restore_projects(cat_dir: Path, home: Path, dry: bool) -> list[str]:
    meta_path = cat_dir / "meta.json"
    if not meta_path.is_file():
        return []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    actions = []
    for repo in meta.get("repos") or []:
        rel = repo.get("path") or ""
        url = repo.get("url") or ""
        if not rel:
            continue
        try:
            target = contained(home, home / rel)
        except UnsafePath:
            actions.append(fail(f"refused project path outside home: {rel!r}"))
            continue
        branch = repo.get("branch") or ""
        if not url:
            actions.append(f"skip {rel}: no git remote, nothing to clone from")
            continue
        if dry:
            actions.append(f"git clone {url} ~/{rel}" + (f" -b {branch}" if branch else ""))
            if repo.get("patch"):
                actions.append(f"  then git apply {len(repo.get('changed') or [])} uncommitted files")
            continue
        if target.exists():
            actions.append(f"{rel} already present, left alone")
        else:
            cmd = ["git", "clone"]
            if branch:
                cmd += ["-b", branch]
            cmd += [url, str(target)]
            proc = run(cmd)
            if proc.returncode != 0:
                actions.append(fail(f"clone failed {rel}: " + (proc.stderr or proc.stdout).strip()[:200]))
                continue
            actions.append(f"cloned {rel} from source")
        rel_patch = repo.get("patch") or ""
        if rel_patch:
            try:
                patch = contained(cat_dir, cat_dir / rel_patch)
            except UnsafePath:
                actions.append(fail(f"refused patch path {rel_patch!r}"))
                continue
            if patch.is_file():
                proc = run(["git", "-C", str(target), "apply", "--3way", str(patch)])
                if proc.returncode == 0:
                    actions.append(f"reapplied uncommitted work to {rel}")
                else:
                    actions.append(fail(f"patch failed for {rel}: " + (proc.stderr or proc.stdout).strip()[:200]))
    for rel in meta.get("withoutRemote") or []:
        actions.append(f"WARNING {rel} has no remote and was not captured as content")
    return actions


def restore_toolchains(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool) -> list[str]:
    actions = restore_file_tree(cat_dir, home, old_home, undo, dry)
    meta_path = cat_dir / "meta.json"
    if not meta_path.is_file():
        return actions
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    steps: list[list[str]] = []
    if meta.get("mise"):
        steps.append(["mise", "install", "--yes"])
    for crate in meta.get("cargo") or []:
        steps.append(["cargo", "install", crate])
    for tool in meta.get("go") or []:
        module = tool.get("module")
        if module:
            steps.append(["go", "install", f"{module}@latest"])
    for pkg in meta.get("npmGlobal") or []:
        steps.append(["npm", "install", "-g", pkg])
    if dry:
        return actions + [" ".join(step) for step in steps]
    for step in steps:
        if not shutil.which(step[0]):
            actions.append(fail(f"{step[0]} not installed, skipped: " + " ".join(step)))
            continue
        proc = run(step)
        if proc.returncode == 0:
            actions.append("ok: " + " ".join(step))
        else:
            actions.append(fail("failed: " + " ".join(step) + " -- " + (proc.stderr or proc.stdout).strip()[:200]))
    for name in meta.get("unresolvedGo") or []:
        actions.append(f"WARNING go tool {name} has no resolvable module path")
    return actions


def restore_system(cat_dir: Path, home: Path, dry: bool, allow: bool) -> list[str]:
    meta_path = cat_dir / "meta.json"
    if not meta_path.is_file():
        return []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    etc_root = cat_dir / "etc"
    # The archive is unpacked into a temp dir that disappears when restore
    # returns, so stage the payload somewhere the user can actually review and
    # re-run later.
    stage = home / ".local/state/imprint" / f"system-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    stage_etc = stage / "etc"
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", "# Written by imprint. Review before running."]
    installs = []
    if etc_root.is_dir():
        for path in sorted(etc_root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(etc_root)
            if not dry:
                copy_file(path, stage_etc / rel)
            installs.append((path, Path("/etc") / rel))
            lines.append(f'install -Dm644 {shlex.quote(str(stage_etc / rel))} /etc/{rel}')
    units = [u for u in (meta.get("enabledUnits") or []) if safe_segment(u)]
    if installs:
        lines.append("systemctl daemon-reload")
    for unit in units:
        lines.append(f"systemctl enable {shlex.quote(unit)} || echo \"could not enable {unit}\" >&2")
    summary = [f"{len(installs)} /etc files, {len(units)} system units to enable"]
    if dry:
        return summary + [f"install /etc/{p.relative_to(etc_root)}" for p, _ in installs[:8]] + \
               ([f"... {len(installs) - 8} more"] if len(installs) > 8 else []) + \
               [f"systemctl enable {u}" for u in units[:8]] + \
               ([f"... {len(units) - 8} more units"] if len(units) > 8 else [])
    stage.mkdir(parents=True, exist_ok=True)
    script = stage / "restore-system.sh"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(script, 0o755)
    if not allow:
        return summary + [
            "system layer NOT applied: needs root, pass --allow-system to run it",
            f"review the generated script at {script}",
        ]
    if run(["sudo", "-n", "true"]).returncode != 0:
        return summary + [fail(f"no non-interactive sudo; run it yourself: sudo {script}")]
    proc = run(["sudo", "-n", "bash", str(script)])
    if proc.returncode == 0:
        return summary + ["system layer applied"]
    return summary + [fail("system layer failed: " + (proc.stderr or proc.stdout).strip()[:400])]


def restore_category(
    cid: str,
    cat_dir: Path,
    home: Path,
    old_home: str,
    undo: Path,
    dry: bool,
    manifest: dict,
    args,
) -> list[str]:
    if cid == "plugins":
        return restore_plugins(cat_dir, home, old_home, undo, dry)
    if cid == "packages":
        return restore_packages(cat_dir, dry)
    if cid == "themes":
        return restore_themes(cat_dir, home, old_home, undo, dry)
    if cid == "look":
        return restore_look(cat_dir, home, old_home, undo, dry, manifest)
    if cid == "bar":
        return restore_bar(cat_dir, home, old_home, undo, dry)
    if cid == "services":
        return restore_services(cat_dir, home, old_home, undo, dry)
    if cid == "identity":
        return restore_identity(cat_dir, dry, getattr(args, "confirm_hostname", None))
    if cid == "scripts":
        return restore_scripts(cat_dir, home, old_home, undo, dry)
    if cid == "projects":
        return restore_projects(cat_dir, home, dry)
    if cid == "toolchains":
        return restore_toolchains(cat_dir, home, old_home, undo, dry)
    if cid == "system":
        return restore_system(cat_dir, home, dry, bool(getattr(args, "allow_system", False)))
    actions = restore_file_tree(cat_dir, home, old_home, undo, dry)
    if cid == "secrets" and not dry:
        ssh = home / ".ssh"
        if ssh.is_dir():
            # mkdir used the ambient umask; sshd and ssh both want 0700 here.
            os.chmod(ssh, 0o700)
            actions.append("chmod 700 ~/.ssh")
    return actions


def cmd_restore(args) -> int:
    ensure_session_env()
    archive = Path(args.archive).expanduser()
    if not archive.exists():
        raise SystemExit(f"missing archive: {archive}")
    home = Path.home()
    ids = parse_only(args.only) if args.only else None
    dry = bool(args.dry_run)
    with tempfile.TemporaryDirectory(prefix="imprint-restore-") as tmp:
        root = open_imprint(archive, Path(tmp) / "open")
        manifest = load_manifest(root)
        available = [cid for cid in (manifest.get("categories") or {}) if (root / "categories" / cid).exists()]
        if ids is None:
            ids = [cid for cid in available if category_by_id(cid)["default"]]
        ids = [cid for cid in ids if cid in available]
        if not ids:
            raise SystemExit("no selected categories are present in this imprint")
        old_home = manifest.get("home") or ""
        undo_root = home / ".local/state/imprint"
        undo = new_undo_dir(undo_root)
        if not dry:
            undo.mkdir(parents=True, exist_ok=False)
            write_json(undo / "source.json", {"archive": str(archive), "manifest": manifest, "categories": ids})
        FAILURES.clear()
        report = {"ok": True, "dryRun": dry, "categories": {}, "undo": None if dry else str(undo)}
        if getattr(args, "upgrade", False):
            if dry:
                report["upgrade"] = "omarchy update -y"
            else:
                print("  upgrading the machine first", file=sys.stderr)
                proc = run(["omarchy", "update", "-y"])
                if proc.returncode == 0:
                    report["upgrade"] = "omarchy update ok"
                else:
                    # Arch does not support partial upgrades; installing on top
                    # of a failed update is how you get a broken machine.
                    report["upgrade"] = fail(
                        "omarchy update failed: " + (proc.stderr or proc.stdout).strip()[:300]
                    )
                    report["ok"] = False
                    report["categories"] = {}
                    json.dump(report, sys.stdout, indent=2)
                    sys.stdout.write("\n")
                    return 1
        # Plugins and packages before bar/look so the layout has somewhere to land.
        order = [
            cid
            for cid in (
                "packages", "toolchains", "projects", "system",
                "hooks", "themes", "plugins", "scripts",
                "hyprland", "wallpapers", "look", "bar",
            )
            if cid in ids
        ]
        order += [cid for cid in ids if cid not in order]
        for cid in order:
            print(f"  restoring {cid}", file=sys.stderr)
            actions = restore_category(
                cid,
                root / "categories" / cid,
                home,
                old_home,
                undo,
                dry,
                manifest,
                args,
            )
            report["categories"][cid] = actions
        if not dry:
            reload_proc = run(["hyprctl", "reload"])
            if reload_proc.returncode != 0:
                report["categories"].setdefault("_final", []).append(
                    fail("hyprctl reload failed: " + (reload_proc.stderr or reload_proc.stdout).strip()[:200])
                )
            shell_proc = run(["omarchy", "restart", "shell"])
            if shell_proc.returncode != 0:
                report["categories"].setdefault("_final", []).append(
                    fail("omarchy restart shell failed: " + (shell_proc.stderr or shell_proc.stdout).strip()[:200])
                )
        report["ok"] = not FAILURES
        report["failures"] = list(FAILURES)
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0 if not FAILURES else 1


def cmd_info(args) -> int:
    archive = Path(args.archive).expanduser()
    with tempfile.TemporaryDirectory(prefix="imprint-info-") as tmp:
        root = open_imprint(archive, Path(tmp) / "open")
        manifest = load_manifest(root)
        if args.json:
            json.dump(manifest, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            brief = root / "BRIEF.md"
            if brief.is_file():
                sys.stdout.write(brief.read_text(encoding="utf-8"))
            else:
                sys.stdout.write(render_brief(manifest))
    return 0


def cmd_brief(args) -> int:
    return cmd_info(args)


def cmd_verify(args) -> int:
    archive = Path(args.archive).expanduser()
    with tempfile.TemporaryDirectory(prefix="imprint-verify-") as tmp:
        root = open_imprint(archive, Path(tmp) / "open")
        manifest = load_manifest(root)
        missing = []
        problems = []
        schema = manifest.get("schema")
        if not isinstance(schema, int) or schema > SCHEMA:
            problems.append(f"unsupported schema {schema!r} (this build understands {SCHEMA})")
        if not manifest.get("hostname"):
            problems.append("manifest has no hostname")
        for cid in manifest.get("categories") or {}:
            cat_dir = root / "categories" / cid
            if not cat_dir.exists():
                missing.append(cid)
                continue
            if not any(cat_dir.iterdir()):
                problems.append(f"category {cid} is present but empty")
        result = {
            "ok": not missing and not problems,
            "schema": schema,
            "missing": missing,
            "problems": problems,
            "hostname": manifest.get("hostname"),
        }
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if result["ok"] else 1


def cmd_diff(args) -> int:
    archive = Path(args.archive).expanduser()
    home = Path.home()
    with tempfile.TemporaryDirectory(prefix="imprint-diff-") as tmp:
        root = open_imprint(archive, Path(tmp) / "open")
        manifest = load_manifest(root)
        lines = [f"Imprint {manifest.get('hostname')} vs this machine ({hostname()})"]
        for cid in manifest.get("categories") or {}:
            files_root = root / "categories" / cid / "files"
            if not files_root.is_dir():
                continue
            changed = []
            for path in iter_files(files_root):
                rel = path.relative_to(files_root)
                live = home / rel
                if not live.exists():
                    changed.append(f"  missing  {rel}")
                    continue
                packed = read_text_safe(path)
                current = read_text_safe(live)
                if packed is not None and current is not None:
                    if rewrite_text(packed, manifest.get("home") or "", str(home)) != current:
                        changed.append(f"  changed  {rel}")
                else:
                    try:
                        if path.stat().st_size != live.stat().st_size:
                            changed.append(f"  changed  {rel}")
                    except OSError:
                        changed.append(f"  changed  {rel}")
            if changed:
                lines.append(f"[{cid}]")
                lines.extend(changed[:30])
                if len(changed) > 30:
                    lines.append(f"  ... {len(changed) - 30} more")
        sys.stdout.write("\n".join(lines) + "\n")
    return 0


def cmd_undo(args) -> int:
    root = Path.home() / ".local/state/imprint"
    if not root.is_dir():
        raise SystemExit("no imprint undo history")
    undos = sorted([p for p in root.iterdir() if p.is_dir() and p.name.startswith("undo-")])
    if not undos:
        raise SystemExit("no imprint undo history")
    chosen = Path(args.undo_dir).expanduser() if args.undo_dir else undos[-1]
    if not chosen.is_dir():
        raise SystemExit(f"missing undo dir {chosen}")
    home = Path.home()
    restored = []
    # Deliberately not iter_files: its skip list (.bak, .git, __pycache__ ...)
    # would silently drop files that backup_existing genuinely saved.
    for path in sorted(chosen.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        if path.name == "source.json" and path.parent == chosen:
            continue
        try:
            rel = path.relative_to(chosen)
        except ValueError:
            continue
        dest = home / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy_file(path, dest)
        restored.append(str(rel))
    json.dump({"ok": True, "undo": str(chosen), "files": restored[:100], "count": len(restored)}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="imprint-engine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("categories")
    sub.add_parser("facts")
    save = sub.add_parser("save")
    save.add_argument("--only", default="")
    save.add_argument("--all", action="store_true")
    save.add_argument("-o", "--output", default="")
    restore = sub.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("--only", default="")
    restore.add_argument("--dry-run", action="store_true")
    restore.add_argument("--confirm-hostname", default="")
    restore.add_argument("--allow-system", action="store_true",
                         help="apply the system layer (/etc + systemctl enable) with sudo")
    restore.add_argument("--upgrade", action="store_true",
                         help="run `omarchy update -y` before restoring anything")
    info = sub.add_parser("info")
    info.add_argument("archive")
    info.add_argument("--json", action="store_true")
    brief = sub.add_parser("brief")
    brief.add_argument("archive")
    brief.add_argument("--json", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("archive")
    diff = sub.add_parser("diff")
    diff.add_argument("archive")
    undo = sub.add_parser("undo")
    undo.add_argument("--undo-dir", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "categories": cmd_categories,
        "facts": cmd_facts,
        "save": cmd_save,
        "restore": cmd_restore,
        "info": cmd_info,
        "brief": cmd_brief,
        "verify": cmd_verify,
        "diff": cmd_diff,
        "undo": cmd_undo,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
