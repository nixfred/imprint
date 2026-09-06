#!/usr/bin/env python3
"""Imprint — save and restore an Omarchy machine personality."""

from __future__ import annotations

import argparse
import json
import os
import re
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
        "summary": "Git URLs, first-party clones, local plugin trees",
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
    if not old_home or old_home == new_home:
        return text
    return text.replace(old_home, new_home)


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink() and not src.exists():
        dest.symlink_to(os.readlink(src))
        return
    shutil.copy2(src, dest, follow_symlinks=True)


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
            yield Path(dirpath) / name


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


def copy_into_category(cat_dir: Path, src: Path, home: Path) -> str | None:
    if not src.exists() and not src.is_symlink():
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
            records.append(info)
    meta = {"plugins": records, "count": len(records)}
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


def collect_scripts(cat_dir: Path, home: Path) -> dict:
    kept = []
    skipped = []
    for folder in (home / "bin", home / ".local/bin"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() and not path.is_symlink():
                continue
            if is_skipped_name(path.name):
                continue
            if script_should_keep(path):
                noted = copy_into_category(cat_dir, path, home)
                if noted:
                    kept.append(noted)
            else:
                skipped.append({"path": rel_under_home(path, home), "size": path.stat().st_size if path.exists() else 0})
    meta = {"files": kept, "skipped_binaries": skipped}
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
                lines.append(f"- `{plug['id']}` git `{plug['url']}`")
                lines.append(f"  `omarchy plugin add {plug['url']}{enable}`")
            elif plug.get("kind") == "clone":
                lines.append(f"- `{plug['id']}` cloned from `{plug.get('clonedFrom')}`")
            else:
                lines.append(f"- `{plug['id']}` local tree")
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
    if src.is_dir() and not src.is_symlink():
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
        if dry:
            done.append(str(rel))
            continue
        backup_existing(dest, undo, home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy_file(path, dest)
        if is_probably_text(dest):
            text = dest.read_text(encoding="utf-8")
            rewritten = rewrite_text(text, old_home, str(home))
            if rewritten != text:
                dest.write_text(rewritten, encoding="utf-8")
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
        target = plugins_root / pid
        if dry:
            actions.append(f"plugin {pid} ({plug.get('kind')})")
            continue
        if target.exists() or target.is_symlink():
            backup_existing(target, undo, home)
        if plug.get("kind") == "git" and plug.get("url"):
            proc = run(["omarchy", "plugin", "add", plug["url"], "--yes"])
            if proc.returncode != 0 and "already" not in (proc.stdout + proc.stderr).lower():
                actions.append(f"plugin add failed {pid}: {(proc.stderr or proc.stdout).strip()[:200]}")
            else:
                actions.append(f"plugin add {pid}")
            # Overlay saved tree so local edits survive.
            tree = cat_dir / plug.get("tree", "")
            if tree.is_dir():
                overlay_tree(tree, target, old_home, home)
        else:
            tree = cat_dir / plug.get("tree", "")
            if tree.is_dir():
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
        if is_probably_text(target):
            text = target.read_text(encoding="utf-8")
            rewritten = rewrite_text(text, old_home, str(new_home))
            if rewritten != text:
                target.write_text(rewritten, encoding="utf-8")


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
        actions.append("pkg add " + ("ok" if proc.returncode == 0 else "failed"))
        if proc.returncode != 0:
            actions.append((proc.stderr or proc.stdout).strip()[:400])
    if aur:
        proc = run(["omarchy", "pkg", "aur", "add", *aur])
        actions.append("pkg aur add " + ("ok" if proc.returncode == 0 else "failed"))
        if proc.returncode != 0:
            actions.append((proc.stderr or proc.stdout).strip()[:400])
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
                    proc = run(["omarchy", "theme", "install", theme["url"]])
                    actions.append(
                        f"theme install {theme['id']} "
                        + ("ok" if proc.returncode == 0 or "already" in (proc.stdout + proc.stderr).lower() else "failed")
                    )
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
        actions.append("theme set " + ("ok" if proc.returncode == 0 else theme + " missing"))
    if font:
        proc = run(["omarchy", "font", "set", font])
        actions.append("font set " + ("ok" if proc.returncode == 0 else font + " missing"))
    return actions


def restore_bar(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool) -> list[str]:
    shell_src = cat_dir / "files/.config/omarchy/shell.json"
    if not shell_src.is_file():
        # packed relative without leading handling
        matches = list((cat_dir / "files").rglob("shell.json")) if (cat_dir / "files").is_dir() else []
        shell_src = matches[0] if matches else shell_src
    dest = home / ".config/omarchy/shell.json"
    if dry:
        return ["shell.json"]
    if dest.exists():
        backup_existing(dest, undo, home)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = shell_src.read_text(encoding="utf-8")
    dest.write_text(rewrite_text(text, old_home, str(home)), encoding="utf-8")
    snap = Path(tempfile.mkstemp(prefix="imprint-snap-", suffix=".json")[1])
    edited = Path(tempfile.mkstemp(prefix="imprint-edit-", suffix=".json")[1])
    try:
        shutil.copy2(dest, edited)
        snap_proc = run(["omarchy", "shell", "config-edit", "snapshot", str(snap)])
        if snap_proc.returncode == 0:
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
            return ["shell.json copied; config-edit apply failed, file is in place"]
        return ["shell.json copied (shell not running or snapshot failed)"]
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
            actions.append(f"enable {name} " + ("ok" if proc.returncode == 0 else "failed"))
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
    return ["hostname " + ("ok" if proc.returncode == 0 else "failed")]


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
    return restore_file_tree(cat_dir, home, old_home, undo, dry)


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
        undo = home / ".local/state/imprint" / f"undo-{now_stamp()}"
        if not dry:
            undo.mkdir(parents=True, exist_ok=True)
            write_json(undo / "source.json", {"archive": str(archive), "manifest": manifest, "categories": ids})
        report = {"ok": True, "dryRun": dry, "categories": {}, "undo": None if dry else str(undo)}
        # Plugins and packages before bar/look so the layout has somewhere to land.
        order = [cid for cid in ("packages", "themes", "plugins", "scripts", "hyprland", "look", "bar") if cid in ids]
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
            run(["hyprctl", "reload"])
            run(["omarchy", "restart", "shell"])
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


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
        for cid in manifest.get("categories") or {}:
            if not (root / "categories" / cid).exists():
                missing.append(cid)
        result = {"ok": not missing, "schema": manifest.get("schema"), "missing": missing, "hostname": manifest.get("hostname")}
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
                if is_probably_text(path) and is_probably_text(live):
                    a = rewrite_text(path.read_text(encoding="utf-8"), manifest.get("home") or "", str(home))
                    b = live.read_text(encoding="utf-8")
                    if a != b:
                        changed.append(f"  changed  {rel}")
                else:
                    if path.stat().st_size != live.stat().st_size:
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
    for path in iter_files(chosen):
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
