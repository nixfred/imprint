#!/usr/bin/env python3
"""Imprint — save and restore an Omarchy machine personality."""

from __future__ import annotations

import argparse
import curses
import difflib
import errno
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

KIND = "omarchy-imprint"
VERSION = "1.0.0"
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
        "id": "fonts",
        "title": "Fonts and icons",
        "summary": "Your own fonts, icon themes and cursors under ~/.local/share",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "home",
        "title": "Home documents",
        "summary": "Hand-written notes and instructions at the top of your home dir",
        "default": True,
        "risk": "portable",
    },
    {
        "id": "configs",
        "title": "Other app configs",
        "summary": "Everything else under ~/.config that no other category claims",
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



# ------------------------------------------------------------- presentation --
# gum spin was doing this job. It queries the terminal for capabilities and
# never reads the answers back, so the replies land in the shell as stray
# characters after imprint exits -- and a spinner hides the one thing worth
# watching, which is what is actually being collected.

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
_FALLBACK_FG = {"green": "\033[38;5;114m", "blue": "\033[38;5;111m", "amber": "\033[38;5;179m",
                "red": "\033[38;5;174m", "grey": "\033[38;5;245m", "accent": "\033[38;5;111m"}


def theme_dir() -> Path | None:
    """Where the active Omarchy theme lives, user themes winning over stock."""
    name = current_theme()
    if not name:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    roots = [Path.home() / ".config/omarchy/themes",
             Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "themes"]
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in (root / slug, root / name):
            if (candidate / "colors.toml").is_file():
                return candidate
        for entry in root.iterdir():
            if entry.is_dir() and re.sub(r"[^a-z0-9]+", "-", entry.name.lower()).strip("-") == slug:
                if (entry / "colors.toml").is_file():
                    return entry
    return None


def theme_colours() -> dict:
    """The active theme's palette, so imprint looks like the rest of the desktop."""
    directory = theme_dir()
    if directory is None:
        return {}
    try:
        import tomllib
        with (directory / "colors.toml").open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v.startswith("#")}


def hex_rgb(value: str) -> tuple[int, int, int] | None:
    value = (value or "").lstrip("#")
    if len(value) != 6:
        return None
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return None


def truecolour(value: str) -> str:
    rgb = hex_rgb(value)
    return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m" if rgb else ""


def xterm256(value: str) -> int:
    """Nearest xterm-256 index, for curses which cannot take a hex string."""
    rgb = hex_rgb(value)
    if not rgb:
        return 4
    r, g, b = rgb
    if abs(r - g) < 10 and abs(g - b) < 10:
        return max(232, min(255, 232 + round((r - 8) / 247 * 23)))
    idx = lambda c: 0 if c < 48 else 1 if c < 115 else min(5, (c - 35) // 40)
    return 16 + 36 * idx(r) + 6 * idx(g) + idx(b)


def _build_palette() -> dict:
    colours = theme_colours()
    if not colours:
        return dict(_FALLBACK_FG)
    pick = lambda *names: next((colours[n] for n in names if n in colours), "")
    out = {}
    for key, names in (("green", ("green",)), ("red", ("red",)),
                       ("amber", ("orange", "yellow")), ("blue", ("blue", "accent")),
                       ("grey", ("dark_foreground", "muted")), ("accent", ("accent", "blue"))):
        code = truecolour(pick(*names))
        out[key] = code or _FALLBACK_FG.get(key, "")
    return out


class _Palette:
    """Built on first use: the theme lookup needs helpers defined further down,
    and shelling out to `omarchy theme current` at import time would be rude."""

    def __init__(self):
        self._resolved = None

    def _load(self) -> dict:
        if self._resolved is None:
            try:
                self._resolved = _build_palette()
            except Exception:
                self._resolved = dict(_FALLBACK_FG)
        return self._resolved

    def __getitem__(self, key):
        return self._load()[key]

    def get(self, key, default=""):
        return self._load().get(key, default)


FG = _Palette()


def theme_accent() -> int:
    colours = theme_colours()
    return xterm256(colours.get("accent") or colours.get("blue") or "#5f87ff")
TICK, CROSS, DOTS = "\u2713", "\u2717", "\u22ef"


def colour_ok(stream) -> bool:
    return (stream.isatty() and not os.environ.get("NO_COLOR")
            and os.environ.get("TERM") not in (None, "", "dumb"))


class Progress:
    """A single self-updating line, plus one settled line per finished item."""

    BAR_WIDTH = 28

    def __init__(self, total: int, title: str, stream=None):
        self.stream = stream or sys.stderr
        self.total = max(0, total)
        self.done = 0
        self.title = title
        self.colour = colour_ok(self.stream)
        self.live = self.colour
        self.started = time.monotonic()
        self.failed = 0
        if self.title:
            self._raw(f"\n{self._c(BOLD, self.title)}\n\n")

    def _c(self, code, text):
        return f"{code}{text}{RESET}" if self.colour else text

    def _raw(self, text):
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            pass

    def _bar(self) -> str:
        if not self.total:
            return ""
        filled = int(self.BAR_WIDTH * self.done / self.total)
        return "\u2588" * filled + self._c(FG["grey"], "\u2591" * (self.BAR_WIDTH - filled))

    def update(self, label: str) -> None:
        """Show what is happening right now, on a line that will be overwritten."""
        if not self.live:
            return
        counter = f"{self.done}/{self.total}" if self.total else str(self.done)
        line = f"  {self._bar()}  {self._c(DIM, counter)}  {label}"
        self._raw("\r\033[2K" + line)

    def item(self, label: str, detail: str = "", ok: bool = True) -> None:
        """Settle one item onto its own line, above the bar."""
        self.done += 1
        if not ok:
            self.failed += 1
        mark = self._c(FG["green"], TICK) if ok else self._c(FG["red"], CROSS)
        width = 34
        if len(label) > width:
            label = label[: width - 1] + "\u2026"
        detail = self._c(FG["grey"], detail) if detail else ""
        line = f"  {mark} {label:<{width}} {detail}"
        if self.live:
            self._raw("\r\033[2K" + line + "\n")
            self.update("")
        else:
            self._raw(line + "\n")

    def finish(self, summary: str = "", detail: str = "") -> None:
        if self.live:
            self._raw("\r\033[2K")
        secs = time.monotonic() - self.started
        if summary:
            self._raw(f"\n  {self._c(BOLD, summary)}\n")
        parts = [p for p in (detail, f"{secs:.1f}s") if p]
        if self.failed:
            parts.append(f"{self.failed} failed")
        self._raw(self._c(FG["grey"], "  " + " \u00b7 ".join(parts)) + "\n\n")


def emit(result: dict, args) -> None:
    """JSON is for scripts. A person watching has already seen the progress."""
    if getattr(args, "json", False) or not sys.stdout.isatty():
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")


def note(text: str, colour: str = "") -> None:
    stream = sys.stderr
    body = f"{FG.get(colour, '')}{text}{RESET}" if colour and colour_ok(stream) else text
    try:
        stream.write(f"  {body}\n")
        stream.flush()
    except (OSError, ValueError):
        pass


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


# Churn, not configuration: regenerated on next run and pure noise in a diff.
NOISE_SUFFIXES = (".log", ".sock", ".pid", ".lock", ".tmp", ".swp", ".part")
NOISE_DIR_NAMES = {"Cache", "cache", "logs", "Crash Reports", "GPUCache", "ShaderCache",
                   "Code Cache", "blob_storage", "Service Worker"}
# Runtime singletons an app recreates on launch; copying them confuses it.
NOISE_EXACT = {"SingletonCookie", "SingletonLock", "SingletonSocket", ".lock", "lockfile"}


def is_skipped_name(name: str) -> bool:
    if name in EXCLUDE_DIR_NAMES or name in NOISE_DIR_NAMES or name in NOISE_EXACT:
        return True
    if name.endswith(NOISE_SUFFIXES):
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
    remote = tracking_remote(path)
    url = run_ok(["git", "-C", str(path), "remote", "get-url", remote]).strip()
    if not url and remote != "origin":
        url = run_ok(["git", "-C", str(path), "remote", "get-url", "origin"]).strip()
    return https_git_url(url)


def tracking_remote(path: Path) -> str:
    """The remote the current branch tracks, else origin.

    A plugin can sit on a branch that only exists on a fork while `origin`
    still points at the upstream it was forked from. Recording origin would
    send a restore looking for a branch that is not there.
    """
    upstream = run_ok(["git", "-C", str(path), "rev-parse", "--abbrev-ref",
                       "--symbolic-full-name", "@{upstream}"]).strip()
    if upstream and "/" in upstream:
        name = upstream.split("/", 1)[0]
        if name:
            return name
    return "origin"


def git_branch_of(path: Path) -> str:
    name = run_ok(["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    return "" if name in {"", "HEAD"} else name


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
    extra_aur = sorted(pkg for pkg in explicit if pkg in aur and not is_stock_package(pkg, base)
                       and wanted("packages", f"aur:{pkg}"))
    extra_repo = sorted(
        pkg for pkg in explicit if pkg not in aur and not is_stock_package(pkg, base)
        and wanted("packages", f"repo:{pkg}")
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


# When the picker returns a partial choice inside a category, the collectors
# narrow to it. Empty means "everything in the category", as before.
SUBSELECT: dict[str, set] = {}


def wanted(category: str, key: str) -> bool:
    allowed = SUBSELECT.get(category)
    return allowed is None or key in allowed


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


SHELL_SAFE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@:+-]*$")


def shell_segment(name: str) -> bool:
    """A name out of an archive that ends up inside a generated shell script.

    Quoting it is the real defence and the plan does that now, but a plugin id,
    a systemd unit or a theme is a short mechanical name. `demo$(rm -rf ~)` is
    not one, and an archive claiming otherwise is malformed. Keeping those out
    of the script entirely means one missed pair of quotes is not a machine.
    """
    return bool(name) and len(name) <= 128 and bool(SHELL_SAFE.match(name))


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


# Files that could not be read or written, with the reason. A save or a
# restore that meets one bad file finishes the other few thousand and says so
# at the end; neither dies partway and leaves the user guessing which half ran.
SKIPPED: list[dict] = []


def why(exc: BaseException) -> str:
    return getattr(exc, "strerror", None) or str(exc) or exc.__class__.__name__


def out_of_space(exc: OSError) -> bool:
    return exc.errno in (errno.ENOSPC, errno.EDQUOT)


def staging_is_full(exc: OSError) -> SystemExit:
    """Never step over this one. Skipping a few thousand files because the
    staging disk filled would write a plausible-looking archive with most of
    the machine missing from it."""
    return SystemExit(
        f"no space left while staging the archive in {tempfile.gettempdir()}: {why(exc)}\n"
        "free some room there, or put the staging directory on a bigger disk:\n"
        "  TMPDIR=/var/tmp imprint save")


def try_copy(src: Path, dest: Path) -> bool:
    """One unreadable file must not cost a whole save. A root-owned file under
    ~/.config, a file deleted while the walk was running, a mount that went
    away mid-copy -- each is recorded and stepped over."""
    try:
        copy_file(src, dest)
        return True
    except OSError as exc:
        if out_of_space(exc):
            raise staging_is_full(exc)
        SKIPPED.append({"path": str(src), "action": "read", "reason": why(exc)})
        return False
    except UnicodeError as exc:
        SKIPPED.append({"path": str(src), "action": "read", "reason": why(exc)})
        return False


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


def copyable(path: Path) -> bool:
    """Regular files and symlinks only. A socket, fifo or device node cannot be
    copied -- ~/.config/cliamp/cliamp.sock crashed a whole save."""
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) or stat.S_ISLNK(mode)


def iter_files(root: Path):
    if not root.exists() and not root.is_symlink():
        return
    if root.is_file() or root.is_symlink():
        if copyable(root):
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if not is_skipped_name(name)]
        for name in filenames:
            if is_skipped_name(name):
                continue
            candidate = Path(dirpath) / name
            if not copyable(candidate) or not packable_symlink(candidate):
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
    if not src.is_dir() and not copyable(src):
        return None
    rel = rel_under_home(src, home)
    dest = stage_path(cat_dir, rel)
    if src.is_dir() and not src.is_symlink():
        count = 0
        for file_path in iter_files(src):
            file_rel = rel_under_home(file_path, home)
            if try_copy(file_path, stage_path(cat_dir, file_rel)):
                count += 1
        return f"{rel}/ ({count} files)" if count else None
    if src.is_dir() and src.is_symlink():
        real = src.resolve()
        for file_path in iter_files(real):
            try:
                inner = file_path.relative_to(real)
            except ValueError:
                continue
            try_copy(file_path, stage_path(cat_dir, str(Path(rel) / inner)))
        return f"{rel}/ (symlink -> {real})"
    return rel if try_copy(src, dest) else None


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
    branch = git_branch_of(real)
    dirty = git_dirty(real)
    if url:
        kind = "git"
    elif cloned_from:
        kind = "clone"
    else:
        kind = "local"
    kinds = manifest.get("kinds") if isinstance(manifest.get("kinds"), list) else []
    if not kinds and isinstance(listing.get("kinds"), list):
        kinds = listing["kinds"]
    return {
        "kinds": kinds,
        "id": listing.get("id") or manifest.get("id") or plugin_dir.name,
        "name": listing.get("name") or manifest.get("name") or plugin_dir.name,
        "kind": kind,
        "url": url,
        "commit": commit,
        "branch": branch,
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
        "imprintVersion": VERSION,
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


DCONF_PATH = "/org/gnome/desktop/interface/"
# Correct on this display, wrong on the next one.
DCONF_SKIP_KEYS = {"text-scaling-factor", "cursor-size"}


def collect_dconf(cat_dir: Path) -> list[str]:
    dump = run_ok(["dconf", "dump", DCONF_PATH])
    if not dump.strip():
        return []
    kept = []
    for line in dump.splitlines():
        key = line.split("=", 1)[0].strip()
        if key in DCONF_SKIP_KEYS:
            continue
        kept.append(line)
    if not any("=" in line for line in kept):
        return []
    (cat_dir / "dconf-interface.ini").write_text("\n".join(kept) + "\n", encoding="utf-8")
    return [line.split("=", 1)[0].strip() for line in kept if "=" in line]


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
        "dconfKeys": collect_dconf(cat_dir),
        "dconfPath": DCONF_PATH,
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
    # shell.toml plus the per-plugin settings files (dock, sandman, workspace
    # names, ...). These are settings you chose, not regenerable state.
    omarchy = home / ".config/omarchy"
    if omarchy.is_dir():
        for extra in sorted(omarchy.glob("*.json")) + sorted(omarchy.glob("*.toml")):
            if extra.name == "shell.json" or is_skipped_name(extra.name):
                continue
            noted = copy_into_category(cat_dir, extra, home)
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


def shell_plugin_state(home: Path) -> tuple[dict, set, set, bool]:
    """Placement and disabled set straight from shell.json.

    `omarchy plugin list` needs a live session; over SSH it returns nothing and
    every plugin then looks disabled. shell.json is readable either way, so it
    is the fallback and the source of bar placement.
    """
    path = home / ".config/omarchy/shell.json"
    if not path.is_file():
        return {}, set(), set(), False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, set(), set(), False
    placement = {}
    layout = ((data.get("bar") or {}).get("layout")) or {}
    for section, items in layout.items():
        for index, item in enumerate(items or []):
            pid = item.get("id") if isinstance(item, dict) else item
            if pid:
                placement[pid] = {"section": section, "index": index}
    # Per Omarchy's PluginRegistry: enabled means the id is referenced in
    # shell.json -- a bar.layout entry, a top-level plugins[] entry, or bar.id.
    # Only checking the layout misses panels, overlays and services.
    referenced = set(placement)
    for item in data.get("plugins") or []:
        pid = item.get("id") if isinstance(item, dict) else item
        if pid:
            referenced.add(pid)
    bar_id = (data.get("bar") or {}).get("id")
    if bar_id:
        referenced.add(bar_id)
    disabled = set()
    for pid in data.get("disabledPlugins") or []:
        if isinstance(pid, str):
            disabled.add(pid)
    return placement, referenced, disabled, True


def plugin_units(home: Path) -> dict:
    """Map plugin id -> user units that run code from that plugin's directory.

    A widget can be backed by a systemd --user daemon. Restoring the plugin
    without its unit leaves a widget with nothing feeding it.
    """
    units: dict[str, list[str]] = {}
    root = home / ".config/systemd/user"
    if not root.is_dir():
        return units
    for unit in sorted(root.glob("*.service")):
        text = read_text(unit)
        if "omarchy/plugins" not in text:
            continue
        for line in text.splitlines():
            if "omarchy/plugins/" not in line:
                continue
            tail = line.split("omarchy/plugins/", 1)[1]
            pid = tail.split("/", 1)[0].strip().strip('"').strip("'")
            if pid:
                units.setdefault(pid, [])
                if unit.name not in units[pid]:
                    units[pid].append(unit.name)
    return units


def bundle_unpushed(repo: Path, branch: str, cat_dir: Path, pid: str) -> tuple[str, int]:
    """Pack commits that exist only on this machine into a git bundle.

    A plugin parked on a local branch cannot be re-cloned from anywhere. The
    bundle makes it reproducible without forcing a push to someone's remote.
    """
    if not branch:
        return "", 0
    base = run_ok(["git", "-C", str(repo), "rev-parse", "--abbrev-ref",
                   "--symbolic-full-name", "@{upstream}"]).strip()
    if not base:
        base = run_ok(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "origin/HEAD"]).strip()
    if not base:
        return "", 0
    count = run_ok(["git", "-C", str(repo), "rev-list", "--count", f"{base}..{branch}"]).strip()
    ahead = int(count) if count.isdigit() else 0
    if ahead <= 0:
        return "", 0
    rel = Path("bundles") / f"{pid}.bundle"
    dest = cat_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = run(["git", "-C", str(repo), "bundle", "create", str(dest), f"{base}..{branch}"])
    if proc.returncode != 0 or not dest.is_file():
        # Fall back to a self-contained bundle of the whole branch.
        proc = run(["git", "-C", str(repo), "bundle", "create", str(dest), branch])
    if not dest.is_file():
        return "", ahead
    return str(rel), ahead


def source_repo_index(home: Path) -> dict:
    """manifest id -> checkouts under the project roots that build that plugin.

    A plugin installed by copying files out of a repo loses all trace of where
    it came from, so it gets packed as an opaque tree. The repo is usually
    sitting right there in ~/Projects.
    """
    index: dict[str, list[Path]] = {}
    for root_name in PROJECT_ROOTS:
        root = home / root_name
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            manifest = entry / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                pid = json.loads(manifest.read_text(encoding="utf-8")).get("id")
            except (OSError, json.JSONDecodeError):
                continue
            if pid:
                index.setdefault(pid, []).append(entry)
    return index


def trees_match(a: Path, b: Path) -> bool:
    """Same deployed files and sizes. Repo-only extras (README, deploy.sh,
    assets) are ignored, since they are not part of what gets installed."""
    def snapshot(root: Path) -> dict:
        out = {}
        for f in iter_files(root):
            try:
                out[str(f.relative_to(root))] = f.stat().st_size
            except (OSError, ValueError):
                pass
        return out
    have, want = snapshot(b), snapshot(a)
    shared = set(have) & set(want)
    if not shared or set(have) - shared:
        return False
    return all(have[k] == want[k] for k in shared)


def collect_plugins(cat_dir: Path, home: Path) -> dict:
    plugins_root = home / ".config/omarchy/plugins"
    listing = {item.get("id"): item for item in plugin_list() if item.get("id")}
    placement, referenced, disabled, have_shell = shell_plugin_state(home)
    live = bool(listing)
    unit_map = plugin_units(home)
    repo_index = source_repo_index(home)
    records = []
    if plugins_root.is_dir():
        for plugin_dir in sorted(plugins_root.iterdir()):
            if not plugin_dir.is_dir() and not plugin_dir.is_symlink():
                continue
            if is_skipped_name(plugin_dir.name):
                continue
            if not wanted("plugins", plugin_dir.name):
                continue
            info = classify_plugin(plugin_dir, listing.get(plugin_dir.name))
            pid_name = plugin_dir.name
            if not live and have_shell:
                # No live session: derive enabled the way PluginRegistry does.
                # A bar-widget counts only when it sits in bar.layout; panels,
                # overlays and services count from plugins[]. Being listed in
                # plugins[] does not enable a bar widget.
                key = info["id"] or pid_name
                kinds = info.get("kinds") or []
                if pid_name in disabled or key in disabled:
                    info["enabled"] = False
                elif "bar-widget" in kinds:
                    info["enabled"] = key in placement or pid_name in placement
                else:
                    info["enabled"] = key in referenced or pid_name in referenced
            info["placement"] = placement.get(info["id"]) or placement.get(pid_name) or {}
            info["units"] = unit_map.get(info["id"]) or unit_map.get(pid_name) or []
            # Carry the unit itself. Restoring the whole `services` category to
            # get one daemon would also drag over machine-specific units that
            # must not run on another box.
            for unit_name in info["units"]:
                src = home / ".config/systemd/user" / unit_name
                if src.is_file():
                    copy_file(src, cat_dir / "units" / unit_name)
            real = Path(info["source"])
            # Recipe, not payload: anything with a git remote is re-installed
            # from source at restore. Only trees with no upstream get packed,
            # because nothing else could bring them back.
            if info["kind"] == "git" and info["url"]:
                # ...except uncommitted local work, which upstream does not have.
                # Pack just the changed files as an overlay, not the whole tree.
                bundle_rel, ahead = bundle_unpushed(real, info.get("branch") or "", cat_dir, info["id"])
                info["bundle"] = bundle_rel
                info["unpushed"] = ahead
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
            # Recover provenance for a plugin that was copied out of a repo.
            for candidate in repo_index.get(info["id"], []):
                url = git_remote(candidate)
                if not url:
                    continue
                info["sourceRepo"] = rel_under_home(candidate, home)
                info["sourceRepoUrl"] = url
                info["sourceRepoCommit"] = git_head(candidate)
                info["sourceRepoMatches"] = trees_match(candidate, plugin_dir)
                if info["sourceRepoMatches"]:
                    break
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
        "withSourceRepo": sorted(p["id"] for p in records if p.get("sourceRepoUrl")),
        "packedWithNoSource": sorted(
            p["id"] for p in records if p.get("packed") and not p.get("sourceRepoUrl")),
        "enabled": sorted(p["id"] for p in records if p.get("enabled")),
        "enabledSource": "plugin list" if live else ("shell.json" if have_shell else "unknown"),
        # Every id the source shell reports as OFF, built-ins included. Without
        # this the target keeps its own copy of a widget the source turned off,
        # which is how vic ended up with two workspace indicators.
        "disabledIds": sorted(i for i, item in listing.items() if not item.get("enabled")) if live else [],
    }
    if not live and not have_shell:
        print("  WARNING: could not determine which plugins are enabled", file=sys.stderr)
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_themes(cat_dir: Path, home: Path) -> dict:
    roots = [home / ".config/omarchy/themes"]
    stock_root = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "themes"
    # A stock theme is only collected when it was explicitly chosen: it normally
    # arrives with Omarchy, but offering it in the picker and then quietly not
    # capturing it would be a lie.
    if SUBSELECT.get("themes") and stock_root.is_dir():
        roots.append(stock_root)
    records = []
    seen: set[str] = set()
    for themes_root in roots:
        if not themes_root.is_dir():
            continue
        for theme_dir in sorted(themes_root.iterdir()):
            if not theme_dir.is_dir() or theme_dir.name in seen:
                continue
            if not wanted("themes", theme_dir.name):
                continue
            seen.add(theme_dir.name)
            url = git_remote(theme_dir)
            is_stock = theme_dir.parent == stock_root
            rec = {
                "id": theme_dir.name,
                "url": url,
                "commit": git_head(theme_dir),
                "kind": "git" if url else ("stock" if is_stock else "local"),
                "stock": is_stock,
            }
            if not url:
                if is_stock:
                    # Stock themes live outside $HOME, so stage them by hand.
                    dest_root = cat_dir / "stock" / theme_dir.name
                    count = 0
                    for f in iter_files(theme_dir):
                        try:
                            copy_file(f, dest_root / f.relative_to(theme_dir))
                            count += 1
                        except (OSError, ValueError):
                            pass
                    rec["copied"] = f"stock/{theme_dir.name} ({count} files)"
                else:
                    rec["copied"] = copy_into_category(cat_dir, theme_dir, home)
            records.append(rec)
    meta = {"themes": records, "current": current_theme(),
            "stockIncluded": sorted(r["id"] for r in records if r.get("stock"))}
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
            if not wanted("scripts", rel_under_home(path, home)):
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


# `source x` is usually guarded: `[[ -r x ]] && source x`, so anchoring to the
# start of the line finds nothing. Match after a line start or a shell operator.
SOURCE_RE = re.compile(r"(?:^|&&|\|\||;|\bthen\b)\s*(?:source|\.)\s+(\S+)", re.MULTILINE)


def sourced_files(path: Path, home: Path) -> list[Path]:
    """Files a shell rc sources from under $HOME.

    Copying .bashrc without these gives a restored shell that errors on every
    login. Anything outside $HOME belongs to a package, not to this machine.
    """
    text = read_text(path)
    if not text:
        return []
    found = []
    for raw in SOURCE_RE.findall(text):
        candidate = raw.strip().strip('"').strip("'")
        if "$" in candidate and "$HOME" not in candidate:
            continue  # unresolvable variable, e.g. $OMARCHY_PATH
        candidate = candidate.replace("$HOME", str(home))
        if candidate.startswith("~"):
            candidate = str(home) + candidate[1:]
        if not candidate.startswith("/"):
            continue
        target = Path(candidate)
        try:
            target.relative_to(home)
        except ValueError:
            continue
        if target.is_file():
            found.append(target)
    return found


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
        ".bash_logout",
        ".inputrc",
        ".config/tmux",
        ".config/environment.d",
        ".config/user-dirs.dirs",
    ):
        noted = copy_into_category(cat_dir, home / rel, home)
        if noted:
            files.append(noted)

    followed = []
    for rc in (".bashrc", ".bash_profile"):
        for extra in sourced_files(home / rc, home):
            noted = copy_into_category(cat_dir, extra, home)
            if noted and noted not in files:
                files.append(noted)
                followed.append(noted)

    # git hooks are configured by path, so the path is where to look
    hooks_dir = run_ok(["git", "config", "--global", "--get", "core.hooksPath"]).strip()
    hooks_rel = ""
    if hooks_dir:
        hooks_path = Path(hooks_dir.replace("~", str(home)))
        if not hooks_path.is_absolute():
            hooks_path = home / hooks_path
        # An absolute hooksPath outside $HOME is not this user's to carry, and
        # staging it would write outside the category tree.
        try:
            hooks_path.resolve().relative_to(home.resolve())
            inside = True
        except (ValueError, OSError):
            inside = False
        if inside:
            noted = copy_into_category(cat_dir, hooks_path, home)
            if noted:
                files.append(noted)
                hooks_rel = noted
        else:
            hooks_rel = f"{hooks_path} (outside home, not collected)"
    meta = {"files": files, "sourcedByShell": followed, "gitHooks": hooks_rel}
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
    # Shell history and the gh token live here rather than in a default save:
    # history routinely contains pasted tokens and one-off passwords.
    for rel in (".bash_history", ".zsh_history", ".python_history", ".config/gh"):
        noted = copy_into_category(cat_dir, home / rel, home)
        if noted:
            files.append(noted)
    meta = {
        "files": files,
        "private_keys_present_not_copied": skipped,
        "note": "Private keys stay on the source machine unless you copy them yourself. "
                "Shell history and gh credentials are here because they leak secrets.",
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
    flags = [f"--root={SOURCE_SYSTEM_ROOT}"] if SOURCE_SYSTEM_ROOT != Path("/") else []
    text = run_ok(["systemctl", *flags, "list-unit-files", "--state=enabled", "--no-legend"])
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


SOURCE_SYSTEM_ROOT = Path("/")


def collect_system(cat_dir: Path, home: Path) -> dict:
    etc = SOURCE_SYSTEM_ROOT / "etc"
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
        "sourceRoot": str(SOURCE_SYSTEM_ROOT),
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
            rel = rel_under_home(entry, home)
            if not wanted("projects", rel):
                continue
            url = git_remote(entry)
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


# ~/.config entries other categories already own, or that must never travel.
CONFIG_CLAIMED = {
    "hypr", "omarchy", "foot", "kitty", "alacritty", "ghostty", "mimeapps.list",
    "xdg-terminals.list", "starship.toml", "git", "btop", "lazygit", "systemd",
    "nvim", "mise", "bash", "environment.d", "tmux", "user-dirs.dirs",
}
# Browser profiles and credential stores: gigabytes, and full of live sessions.
CONFIG_SKIP = {
    "BraveSoftware", "chromium", "chromium-headless", "google-chrome",
    "google-chrome-beta", "google-chrome-for-testing", "google-chrome-unstable",
    "microsoft-edge", "microsoft-edge-dev", "mozilla", "opera", "vivaldi", "zen",
    "gh", "rclone", "omarchy-tesla", "x-api", "Codex", "claude", "keyrings",
}
CONFIG_MAX_BYTES = 8 * 1024 * 1024


def collect_configs(cat_dir: Path, home: Path) -> dict:
    """The long tail of ~/.config. Skips what other categories own, browser
    profiles, and credential stores; everything skipped is reported."""
    root = home / ".config"
    kept, skipped = [], []
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            name = entry.name
            if name in CONFIG_CLAIMED or is_skipped_name(name):
                continue
            if not wanted("configs", name):
                continue
            if name in CONFIG_SKIP:
                skipped.append({"path": name, "why": "browser profile or credential store"})
                continue
            size = dir_size(entry) if entry.is_dir() else (
                entry.stat().st_size if entry.is_file() else 0)
            if size > CONFIG_MAX_BYTES:
                skipped.append({"path": name, "why": f"{human_size(size)}, over the cap"})
                continue
            noted = copy_into_category(cat_dir, entry, home)
            if noted:
                kept.append(noted)
    meta = {"files": kept, "skipped": skipped, "cap": human_size(CONFIG_MAX_BYTES)}
    write_json(cat_dir / "meta.json", meta)
    return meta


def collect_fonts(cat_dir: Path, home: Path) -> dict:
    """dconf records an icon and cursor theme by name; without the assets a
    restored machine silently falls back to defaults."""
    files = []
    for rel in (".local/share/fonts", ".local/share/icons", ".icons",
                ".local/share/cursors", ".fonts", ".config/fontconfig"):
        noted = copy_into_category(cat_dir, home / rel, home)
        if noted:
            files.append(noted)
    meta = {"files": files}
    write_json(cat_dir / "meta.json", meta)
    return meta


HOME_DOC_SUFFIXES = {".md", ".txt", ".org", ".rst"}


def collect_home(cat_dir: Path, home: Path) -> dict:
    """Hand-written files sitting loose in $HOME that nothing else backs up."""
    files, skipped = [], []
    for entry in sorted(home.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if is_skipped_name(entry.name) or entry.suffix.lower() not in HOME_DOC_SUFFIXES:
            continue
        if not wanted("home", entry.name):
            continue
        try:
            if entry.stat().st_size > TEXT_LIMIT * 4:
                skipped.append(entry.name)
                continue
        except OSError:
            continue
        noted = copy_into_category(cat_dir, entry, home)
        if noted:
            files.append(noted)
    meta = {"files": files, "skipped_oversized": skipped}
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
    "configs": collect_configs,
    "fonts": collect_fonts,
    "home": collect_home,
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
        f"imprint = \"{manifest.get('imprintVersion') or 'before 1.0.0'}\"",
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
            elif plug.get("sourceRepoUrl"):
                match = "" if plug.get("sourceRepoMatches") else " (installed copy has drifted)"
                lines.append(f"- `{plug['id']}` packed; source repo `{plug['sourceRepoUrl']}`{match}")
            else:
                lines.append(f"- `{plug['id']}` local tree, packed in this archive (no source repo found)")
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
    skipped = manifest.get("skipped") or []
    if skipped:
        total = manifest.get("skippedCount") or len(skipped)
        lines += ["", f"## Not in this archive ({total} unreadable)", "",
                  "These files were on the machine but could not be read when it "
                  "was saved. Nothing here restores them.", ""]
        for item in skipped[:20]:
            lines.append(f"- `{item.get('path')}` — {item.get('reason')}")
        if total > 20:
            lines.append(f"- ... {total - 20} more")
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


def collect_selected(staging: Path, home: Path, ids: list[str],
                     progress: "Progress | None" = None) -> dict:
    categories = {}
    for cid in ids:
        collector = COLLECTORS[cid]
        title = category_by_id(cid)["title"]
        if progress:
            progress.update(title)
        cat_dir = staging / "categories" / cid
        cat_dir.mkdir(parents=True, exist_ok=True)
        meta = collector(cat_dir, home)
        meta = dict(meta or {})
        meta["bytes"] = dir_size(cat_dir)
        categories[cid] = meta
        if progress:
            progress.item(title, human_size(meta["bytes"]))
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


def suggest_under_home(dest: Path) -> Path | None:
    """/home/google/imprints is almost always a slip for ~/google/imprints --
    a removable disk or an rclone mount lives under the home directory, and
    one deleted character in the prompt moves it a level up."""
    home = Path.home()
    for i in range(1, len(dest.parts)):
        candidate = home.joinpath(*dest.parts[i:])
        probe = candidate
        while probe != home and not probe.exists():
            probe = probe.parent
        if probe != home and probe.is_dir():
            return candidate
    return None


def check_dest(dest: Path, create: bool = False) -> None:
    """Fail before collecting, not after. A save spends minutes packing
    hundreds of megabytes; finding out at the end that the destination was
    never writable throws all of that away.

    A directory the user named is never conjured up. `create` is only true for
    the one path imprint chooses itself when no output was given: a typo in a
    path should stop the run, not quietly grow a tree of empty directories in
    a place nobody meant to write to.
    """
    parent = dest.parent
    if not parent.is_dir():
        if parent.exists():
            raise SystemExit(f"{parent} is a file, not a directory")
        if not create:
            lines = [f"no such directory: {parent}"] + path_advice(parent)
            lines.append("imprint does not create directories -- make it first:")
            lines.append(f"  mkdir -p {shlex.quote(str(parent))}")
            raise SystemExit("\n".join(lines))
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SystemExit("\n".join(
                [f"cannot create {parent}: {why(exc)}"] + path_advice(parent)))
    if dest.exists():
        if dest.is_dir():
            raise SystemExit(f"{dest} is a directory, not an archive path")
        if not os.access(dest, os.W_OK):
            raise SystemExit(f"cannot overwrite {dest}: permission denied")
    probe = parent / f".imprint-write-test-{os.getpid()}"
    try:
        probe.touch()
    except OSError as exc:
        raise SystemExit(f"cannot write into {parent}: {why(exc)}")
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def deepest_existing(path: Path) -> Path:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def path_advice(path: Path) -> list[str]:
    """What to tell someone whose path is not there: where the name stopped
    being real, and the same path under $HOME when that one does exist."""
    lines = []
    deepest = deepest_existing(path)
    if deepest != path:
        lines.append(f"the deepest part that exists is {deepest}")
    hint = suggest_under_home(path)
    if hint is not None and hint != path:
        lines.append(f"did you mean {hint} ?")
    return lines


def known_archives() -> list[Path]:
    found = []
    remembered, _ = default_archive_dir(Path.home())
    roots = [remembered, Path.home() / "imprints", Path.home() / "backups"]
    for root in dict.fromkeys(roots):
        try:
            if root.is_dir():
                found += [p for p in root.glob("*.tar.zst") if p.is_file()]
        except OSError:
            continue
    try:
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass
    return found


def archive_menu() -> list[str]:
    """A wrong archive path is nearly always a typo of one that is right here."""
    found = known_archives()
    if not found:
        return ["there are no archives in ~/imprints or ~/backups yet -- "
                "`imprint save` makes one"]
    lines = ["archives on this machine:"]
    lines += [f"  {path}" for path in found[:5]]
    if len(found) > 5:
        lines.append(f"  ... {len(found) - 5} more in ~/imprints")
    return lines


def need_archive(raw: str) -> Path:
    """A path the user typed for an imprint that should already exist.
    Everything that can be wrong with it is said here, once, with what to do."""
    path = Path(raw).expanduser()
    if path.is_dir():
        if (path / "manifest.json").is_file():
            return path                      # an imprint already unpacked
        raise SystemExit("\n".join(
            [f"{path} is a directory, not an imprint archive"] + archive_menu()))
    if not path.exists():
        raise SystemExit("\n".join(
            [f"no imprint archive at {path}"] + path_advice(path) + archive_menu()))
    if not os.access(path, os.R_OK):
        raise SystemExit(f"cannot read {path}: permission denied "
                         f"(it belongs to uid {path.stat().st_uid})")
    if path.stat().st_size == 0:
        raise SystemExit(f"{path} is empty -- whatever wrote or copied it "
                         "did not finish")
    return path


def need_file(raw: str, what: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_dir():
        raise SystemExit(f"{what}: {path} is a directory, not a file")
    if not path.exists():
        raise SystemExit("\n".join([f"{what}: no such file {path}"] + path_advice(path)))
    if not os.access(path, os.R_OK):
        raise SystemExit(f"{what}: cannot read {path}, permission denied")
    return path


def need_dir(raw: str, what: str) -> Path:
    path = Path(raw).expanduser()
    if path.exists() and not path.is_dir():
        raise SystemExit(f"{what}: {path} is a file, not a directory")
    if not path.exists():
        raise SystemExit("\n".join([f"{what}: no such directory {path}"] + path_advice(path)))
    if not os.access(path, os.R_OK | os.X_OK):
        raise SystemExit(f"{what}: cannot read {path}, permission denied")
    return path


def write_archive(staging: Path, dest: Path) -> None:
    check_dest(dest)
    fd, name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".partial", dir=dest.parent)
    os.close(fd)
    tmp = Path(name)  # mode 0600 before the first archived byte is written
    try:
        with tarfile.open(tmp, "w:zst") as tar:
            tar.add(staging, arcname=".")
        tmp.replace(dest)
    except OSError as exc:
        # A half-written archive on a slow mount is worse than none: it looks
        # like a backup. Take it with us on the way out.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise SystemExit(f"cannot write {dest}: {exc.strerror or exc}")


def extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:*") as tar:
            tar.extractall(dest, filter="data")
    except tarfile.TarError as exc:
        raise SystemExit(f"{archive} is not a readable imprint archive: {exc}")
    except OSError as exc:
        raise SystemExit(f"cannot read {archive}: {exc}")


def integrity_index(root: Path) -> dict:
    """Index every archived leaf except the self-referential manifest."""
    result = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == "manifest.json" or (path.is_dir() and not path.is_symlink()):
            continue
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            result[rel] = {"type": "symlink", "target": os.readlink(path)}
        elif path.is_file():
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            result[rel] = {"type": "file", "size": path.stat().st_size,
                           "mode": mode & 0o777, "sha256": digest}
        else:
            raise SystemExit(f"unsupported archive file type: {rel}")
    return result


def check_integrity(root: Path, manifest: dict) -> None:
    expected = manifest.get("integrity")
    if expected is None:
        return  # Old archives are readable, but have no content integrity claim.
    if not isinstance(expected, dict):
        raise SystemExit("invalid integrity index")
    actual = integrity_index(root)
    if set(expected) != set(actual):
        raise SystemExit("archive integrity: missing or unexpected files")
    for rel, record in expected.items():
        if not isinstance(record, dict):
            raise SystemExit(f"archive integrity: invalid record {rel}")
        observed = dict(actual[rel])
        if record.get("type") == "file":
            mode = record.get("mode")
            if type(mode) is not int or not 0 <= mode <= 0o777:
                raise SystemExit(f"archive integrity: invalid mode {rel}")
            # tar's safe data filter normalizes permissions; the original safe
            # mode is provenance and is restored only after bytes verify.
            observed["mode"] = mode
        if record != observed:
            raise SystemExit(f"archive integrity mismatch: {rel}")


def load_manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        raise SystemExit("not an imprint: missing manifest.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"manifest.json is not readable: {exc}")
    if not isinstance(data, dict):
        raise SystemExit("manifest.json is not an object")
    if data.get("kind") != KIND:
        raise SystemExit(f"not an imprint: kind={data.get('kind')}")
    schema = data.get("schema")
    if schema is not None and (type(schema) is not int or not 1 <= schema <= SCHEMA):
        raise SystemExit(f"unsupported schema {schema!r}")
    check_integrity(root, data)
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


def osc8(url: str, label: str = "") -> str:
    """OSC 8 hyperlink: clickable in foot, kitty, ghostty, alacritty and others,
    and degrades to plain text where it is not supported."""
    label = label or url
    if not sys.stdout.isatty():
        return f"{label} ({url})" if label != url else url
    return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"


REPO_URL = "https://github.com/nixfred/imprint"
SITE_URL = "https://nixfred.com"


def cmd_about(_args) -> int:
    colours = theme_colours()
    accent = truecolour(colours.get("accent") or "") or FG.get("blue", "")
    grey = FG.get("grey", "")
    tint = colour_ok(sys.stdout)
    c = lambda code, text: f"{code}{text}{RESET}" if tint and code else text
    lines = [
        "",
        c(BOLD, "Imprint"),
        c(grey, "Stamp one Omarchy machine onto another."),
        "",
        "Fred Nix",
        "  " + c(accent, osc8(REPO_URL, "github.com/nixfred/imprint")),
        "  " + c(accent, osc8(SITE_URL, "nixfred.com")),
        "",
        c(grey, f"version {VERSION}  ·  schema {SCHEMA}  ·  "
                f"{len(CATEGORIES)} categories  ·  MIT"),
        c(grey, f"next save: {default_archive_dir(Path.home())[0]}"),
        c(grey, f"this machine: {hostname()}  ·  Omarchy {omarchy_version()}  ·  theme {current_theme()}"),
        "",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def cmd_default_output(_args) -> int:
    directory, note = default_archive_dir(Path.home())
    if note:
        sys.stderr.write(f"  {note}\n")
    sys.stdout.write(str(directory / f"imprint-{hostname()}-{now_stamp()}.tar.zst") + "\n")
    return 0


def cmd_facts(_args) -> int:
    json.dump(machine_facts(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def state_dir() -> Path:
    return Path.home() / ".local/state/imprint"


def read_state() -> dict:
    try:
        data = json.loads((state_dir() / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(data: dict) -> None:
    """Remembering is a convenience; it never costs a save. A state directory
    that cannot be written just means the next run asks again."""
    try:
        path = state_dir() / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def remember_save_dir(directory: Path) -> None:
    state = read_state()
    state["lastSaveDir"] = str(directory)
    write_state(state)


def default_archive_dir(home: Path) -> tuple[Path, str]:
    """Where the next save goes when no path was given: wherever the last one
    went. A remembered directory that is gone -- an unplugged disk, an rclone
    remote that is not mounted this morning -- falls back to ~/imprints and
    says so out loud, because writing the file into an empty mountpoint would
    look like it worked."""
    fallback = home / "imprints"
    remembered = read_state().get("lastSaveDir")
    if not remembered or not isinstance(remembered, str):
        return fallback, ""
    directory = Path(remembered)
    if directory == fallback or directory.is_dir():
        return directory, ""
    return fallback, (f"the last imprint went to {directory}, which is not there "
                      f"now -- this one goes to {fallback}")


def default_archive_path(home: Path, host: str) -> Path:
    return default_archive_dir(home)[0] / f"imprint-{host}-{now_stamp()}.tar.zst"


def include_helpers(staging: Path, home: Path, paths: list[str], categories: dict) -> None:
    """Exact opt-in helper files, never recursive agent/application state."""
    if not paths:
        return
    if "scripts" not in categories:
        raise SystemExit("--include-file requires the scripts category")
    records = []
    for rel in dict.fromkeys(paths):
        parts = rel.split("/")
        allowed = ("bin/", ".local/bin/", ".local/share/", ".hermes/scripts/")
        if (not rel.startswith(allowed) or any(not safe_segment(p) for p in parts)
                or any(is_skipped_name(p) for p in parts)):
            raise SystemExit(f"refused helper path: {rel}")
        # Open every component without following symlinks. fstat and read use
        # the same descriptor, so a concurrent source rename cannot swap it.
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in parts[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(source, "rb") as stream:
                st = os.fstat(stream.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o7000 or st.st_size > SCRIPT_LIMIT:
                    raise SystemExit(f"refused helper type, mode or size: {rel}")
                data = stream.read(SCRIPT_LIMIT + 1)
        finally:
            os.close(fd)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise SystemExit(f"helper is not UTF-8: {rel}")
        if len(data) > SCRIPT_LIMIT or b"\0" in data or not text.startswith("#!"):
            raise SystemExit(f"helper must be a bounded text script with a shebang: {rel}")
        if re.search(r'''(?im)["']?(?:api[_-]?key|access[_-]?token|password|passwd|secret|token)["']?\s*[:=]\s*["'][^"'\n]+["']''', text):
            raise SystemExit(f"possible embedded credential in helper: {rel}; review and externalize it")
        refs = []
        forms = [str(home / rel), "~/" + rel, "$HOME/" + rel, "${HOME}/" + rel, "%h/" + rel]
        for path in iter_files(staging / "categories"):
            body = read_text_safe(path)
            if body is not None and any(form in body for form in forms):
                refs.append(path.relative_to(staging).as_posix())
        dest = staging / "categories/scripts/files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        dest.chmod(stat.S_IMODE(st.st_mode))
        records.append({"path": rel, "source": str(home / rel), "referencedBy": refs,
                        "sha256": hashlib.sha256(data).hexdigest(), "mode": stat.S_IMODE(st.st_mode)})
    categories["scripts"]["includedHelpers"] = records
    write_json(staging / "categories/scripts/meta.json", categories["scripts"])


def cmd_save(args) -> int:
    global SOURCE_SYSTEM_ROOT
    SOURCE_SYSTEM_ROOT = strict_target(Path(getattr(args, "system_root", "/")))
    home = Path.home()
    if getattr(args, "select", ""):
        path = need_file(args.select, "cannot read the selection file")
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"cannot read the selection file {path}: {exc}")
        SUBSELECT.clear()
        for cid, keys in (spec.get("subselections") or {}).items():
            SUBSELECT[cid] = set(keys)
    ids = parse_only(args.only) if args.only else default_category_ids()
    if args.all:
        ids = [item["id"] for item in CATEGORIES]
    typed = bool(args.output)
    if typed:
        dest = Path(args.output).expanduser()
    else:
        directory, note = default_archive_dir(home)
        if note:
            sys.stderr.write(f"  {note}\n")
        dest = directory / f"imprint-{hostname()}-{now_stamp()}.tar.zst"
    if dest.suffixes[-2:] != [".tar", ".zst"] and not str(dest).endswith(".tar.zst"):
        dest = dest.with_name(dest.name + ".tar.zst") if dest.suffix == "" else dest
    check_dest(dest, create=not typed)
    SKIPPED.clear()
    with tempfile.TemporaryDirectory(prefix="imprint-") as tmp:
        staging = Path(tmp) / "imprint"
        staging.mkdir()
        progress = Progress(len(ids), f"Collecting {hostname()}")
        categories = collect_selected(staging, home, ids, progress)
        include_helpers(staging, home, getattr(args, "include_file", []) or [], categories)
        manifest = machine_facts()
        manifest["categories"] = {cid: strip_heavy(categories[cid]) for cid in ids}
        manifest["archiveName"] = dest.name
        # The archive says what it could not take. Nobody restoring it should
        # have to discover the gap by finding the file missing months later.
        if SKIPPED:
            manifest["skipped"] = SKIPPED[:200]
            manifest["skippedCount"] = len(SKIPPED)
        write_json(staging / "manifest.json", manifest)
        (staging / "BRIEF.md").write_text(render_brief(manifest), encoding="utf-8")
        copy_tool(staging)
        manifest["integrity"] = integrity_index(staging)
        write_json(staging / "manifest.json", manifest)
        progress.update("writing the archive")
        write_archive(staging, dest)
        size_now = dest.stat().st_size
        progress.finish(f"Wrote {dest}",
                        f"{human_size(size_now)} \u00b7 {len(ids)} categories")
    remember_save_dir(dest.parent)
    report_skipped()
    size = dest.stat().st_size
    result = {"ok": True, "path": str(dest), "bytes": size, "categories": ids}
    if SKIPPED:
        result["skipped"] = SKIPPED
    emit(result, args)
    return 0


def report_skipped() -> None:
    if not SKIPPED:
        return
    count = len(SKIPPED)
    noun = "file" if count == 1 else "files"
    sys.stderr.write(f"\n  {count} {noun} could not be read and are not in the archive:\n")
    for item in SKIPPED[:5]:
        sys.stderr.write(f"    {item['path']} -- {item['reason']}\n")
    if count > 5:
        sys.stderr.write(f"    ... {count - 5} more, all listed in the manifest\n")


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


def restore_file_tree(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool,
                      category: str = "", skip_rel: str = "") -> list[str]:
    files_root = cat_dir / "files"
    done = []
    if not files_root.is_dir():
        return done
    for path in iter_files(files_root):
        rel = path.relative_to(files_root)
        if skip_rel and str(rel) == skip_rel:
            continue
        if category and not wanted(category, str(rel)):
            continue
        dest = home / rel
        try:
            contained(home, dest.parent)
        except UnsafePath:
            done.append(f"refused {rel} (escapes home)")
            continue
        if dry:
            done.append(str(rel))
            continue
        # A file that cannot be written must not abandon the restore halfway
        # through and leave a home directory half from each machine. Say which
        # one refused, keep going, and let FAILURES carry it to the exit code.
        try:
            backup_existing(dest, undo, home)
        except OSError as exc:
            done.append(fail(f"~/{rel} left as it was: what is there now could "
                             f"not be backed up ({why(exc)})"))
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            copy_file(path, dest)
        except OSError as exc:
            done.append(fail(f"cannot write ~/{rel}: {why(exc)}"))
            continue
        rewrite_in_place(dest, old_home, str(home))
        done.append(str(rel))
    return done


def sync_git_checkout(target: Path, plug: dict, cat_dir: Path | None = None) -> str:
    """Bring an already-installed git plugin to the branch/commit recorded.

    `omarchy plugin add` refuses an id that is already in use, so without this
    an out-of-date plugin stays out of date while the restore reports success.
    """
    want_commit = (plug.get("commit") or "").strip()
    want_branch = (plug.get("branch") or "").strip()
    url = (plug.get("url") or "").strip()
    pid = plug.get("id")
    if not (target / ".git").exists():
        return f"{pid} is not a git checkout here, left as is"
    have = git_head(target)
    if want_commit and have == want_commit:
        return f"{pid} already at {want_commit[:12]}"
    has_overlay = bool(plug.get("overlay"))
    if git_dirty(target) and not has_overlay:
        return fail(f"{pid} is at {have[:12]} but the imprint has {want_commit[:12]}; "
                    "it has uncommitted changes here, so it was left alone")
    if not url:
        return fail(f"{pid} is out of date and has no recorded source to update from")

    # The wanted branch may live on a fork while origin points at upstream.
    remote = "imprint-src"
    existing = run_ok(["git", "-C", str(target), "remote", "get-url", remote]).strip()
    if existing != url:
        if existing:
            run(["git", "-C", str(target), "remote", "set-url", remote, url])
        else:
            run(["git", "-C", str(target), "remote", "add", remote, url])
    fetched = run(["git", "-C", str(target), "fetch", remote, "--quiet"])
    if fetched.returncode != 0:
        return fail(f"{pid}: could not fetch {url}: "
                    + (fetched.stderr or fetched.stdout).strip()[:160])

    def have_commit() -> bool:
        return bool(want_commit) and run(
            ["git", "-C", str(target), "cat-file", "-e", want_commit + "^{commit}"]).returncode == 0

    rel_bundle = plug.get("bundle") or ""
    if rel_bundle and cat_dir is not None and not have_commit():
        try:
            bundle = contained(cat_dir, cat_dir / rel_bundle)
        except UnsafePath:
            bundle = None
        if bundle is not None and bundle.is_file():
            ref = want_branch or "HEAD"
            run(["git", "-C", str(target), "fetch", str(bundle),
                 f"{ref}:refs/remotes/imprint-bundle/{ref}", "--force"])

    if have_commit():
        target_ref = want_commit
    elif want_branch and run(["git", "-C", str(target), "rev-parse", "--verify", "--quiet",
                              f"{remote}/{want_branch}"]).returncode == 0:
        target_ref = f"{remote}/{want_branch}"
    else:
        return fail(
            f"{pid}: commit {want_commit[:12]} is not on {url}"
            + (f" and branch {want_branch!r} is not published there" if want_branch else "")
            + " -- the source machine has unpushed work; push it to make this reproducible")

    # With an overlay the local modifications are the source machine's own work,
    # reapplied right after this, so discarding them here is safe and required
    # to move the checkout at all. The pre-restore copy is in the undo dir.
    # --force also clobbers untracked files that the target commit contains,
    # which is how a checkout aborts after an earlier overlay left copies behind.
    force = ["--force"] if has_overlay else []
    def do_checkout():
        if want_branch:
            return run(["git", "-C", str(target), "checkout", *force, "-B", want_branch, target_ref])
        return run(["git", "-C", str(target), "checkout", *force, "--detach", target_ref])

    proc = do_checkout()
    if proc.returncode != 0:
        # Untracked files that the target commit also tracks are stale copies from
        # an earlier restore. Drop exactly those and retry; leave anything else.
        listing = run_ok(["git", "-C", str(target), "ls-files", "--others",
                          "--exclude-standard", "-z"])
        removed = 0
        for name in listing.split("\0"):
            if not name:
                continue
            if run(["git", "-C", str(target), "cat-file", "-e",
                    f"{target_ref}:{name}"]).returncode == 0:
                try:
                    (target / name).unlink()
                    removed += 1
                except OSError:
                    pass
        if removed:
            proc = do_checkout()
    if proc.returncode != 0:
        return fail(f"{pid}: checkout of {target_ref} failed: "
                    + (proc.stderr or proc.stdout).strip()[:160])
    now = git_head(target)
    return (f"updated {pid} {have[:12]} -> {now[:12]}"
            + (f" on {want_branch}" if want_branch else ""))


def restore_plugins(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool) -> list[str]:
    meta_path = cat_dir / "meta.json"
    if not meta_path.is_file():
        return []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    plugins_root = home / ".config/omarchy/plugins"
    actions = []
    live = {item.get("id"): item for item in plugin_list() if item.get("id")}
    for plug in meta.get("plugins") or []:
        pid = plug.get("id")
        if not pid:
            continue
        # An archive is untrusted input: an id like "../../../Documents" would
        # otherwise be rmtree'd and replaced.
        if not shell_segment(pid):
            actions.append(f"refused unsafe plugin id {pid!r}")
            continue
        if not wanted("plugins", pid):
            continue
        target = plugins_root / pid
        if dry:
            # Say exactly what apply would run, not just the category name.
            if plug.get("kind") == "git" and plug.get("url"):
                enable = " --enable" if plug.get("enabled") else ""
                actions.append(f"omarchy plugin add {plug['url']} --yes{enable}")
                if plug.get("branch"):
                    actions.append(f"  then checkout {plug['branch']} @ {(plug.get('commit') or '')[:12]}")
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
            # Deliberately no --enable: `plugin add --enable` appends the widget
            # wherever it likes. The reconcile pass below enables every plugin
            # uniformly, with the section and index recorded from the source.
            proc = run(["omarchy", "plugin", "add", plug["url"], "--yes"])
            blob = (proc.stdout + proc.stderr).lower()
            if proc.returncode == 0:
                actions.append(f"installed {pid} from source")
                if plug.get("branch") or plug.get("commit"):
                    moved = sync_git_checkout(target, plug, cat_dir)
                    if not moved.endswith("already at " + (plug.get("commit") or "")[:12]):
                        actions.append(moved)
            elif "already" in blob:
                # Do not claim an install we did not perform. Say what is there,
                # and whether it actually came from the recorded source.
                have = git_remote(target)
                want = plug["url"]
                if have and have.rstrip("/").removesuffix(".git") != want.rstrip("/").removesuffix(".git"):
                    # Not necessarily wrong: the source machine may track a fork
                    # while this one still points at upstream. The recorded URL
                    # wins, and sync fetches from it under its own remote.
                    actions.append(f"{pid} points at {have} here, imprint recorded {want}; syncing from the recorded one")
                    actions.append(sync_git_checkout(target, plug, cat_dir))
                elif have:
                    actions.append(sync_git_checkout(target, plug, cat_dir))
                else:
                    actions.append(f"{pid} already present (no git remote to compare), left as is")
            else:
                actions.append(fail(f"plugin add failed {pid}: {(proc.stderr or proc.stdout).strip()[:200]}"))
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
                note = ""
                if plug.get("sourceRepoUrl"):
                    note = (f" (source: {plug['sourceRepoUrl']}"
                            + ("" if plug.get("sourceRepoMatches") else ", installed copy had drifted") + ")")
                actions.append(f"plugin tree {pid}{note}")
            elif plug.get("kind") == "clone" and plug.get("clonedFrom"):
                run(["omarchy", "plugin", "clone", plug["clonedFrom"]])
                actions.append(f"plugin clone {plug['clonedFrom']}")

    # Installing the code is not the same as showing it. Enabled state and bar
    # placement live in shell.json, so an installed-but-disabled plugin is
    # invisible. Reconcile through the shell's own IPC, which mutates inside
    # the owning process instead of rewriting the file underneath it.
    if not dry:
        run(["omarchy-shell", "shell", "rescanPlugins"])
        live = {item.get("id"): item for item in plugin_list() if item.get("id")}
    to_enable = [p for p in (meta.get("plugins") or [])
                 if p.get("enabled") and shell_segment(p.get("id") or "")
                 and wanted("plugins", p.get("id") or "")]
    for plug in to_enable:
        pid = plug["id"]
        if dry:
            place = plug.get("placement") or {}
            where = f" --section {place['section']} --index {place['index']}" if place else ""
            actions.append(f"omarchy plugin enable {pid}{where}")
            continue
        place = plug.get("placement") or {}
        if live.get(pid, {}).get("enabled"):
            # Deliberately not repositioning. Absolute indices from the source
            # bar cannot converge onto a target bar holding a different set of
            # widgets -- each move shifts the rest, so it oscillates instead of
            # settling. Whole-bar order is the `bar` category's job.
            actions.append(f"{pid} already enabled")
            continue
        cmd = ["omarchy", "plugin", "enable", pid]
        if place.get("section"):
            cmd += ["--section", str(place["section"])]
            if isinstance(place.get("index"), int):
                cmd += ["--index", str(place["index"])]
        proc = run(cmd)
        if proc.returncode == 0:
            actions.append(f"enabled {pid}" + (f" at {place['section']}[{place['index']}]" if place else ""))
        else:
            actions.append(fail(f"could not enable {pid}: " + (proc.stderr or proc.stdout).strip()[:200]))

    # Turn OFF what the source had off. Only ids the source shell actually
    # reported are touched, so plugins unique to this machine are left alone.
    # Disabling a clone hands the bar slot back to its built-in source, so this
    # settles over a couple of rounds rather than one.
    # Only chase full parity when the whole category was taken. Someone who
    # picked two plugins out of seventy did not ask for 55 unrelated disables.
    narrowed = bool(SUBSELECT.get("plugins"))
    want_off = set() if narrowed else set(meta.get("disabledIds") or [])
    if narrowed and meta.get("disabledIds"):
        actions.append("selection was narrowed, so the source's disabled list is not applied")
    if want_off and not dry:
        for _round in range(3):
            current = {item.get("id"): item for item in plugin_list() if item.get("id")}
            turn_off = [i for i in sorted(want_off) if current.get(i, {}).get("enabled")]
            if not turn_off:
                break
            for pid in turn_off:
                if not shell_segment(pid):
                    continue
                proc = run(["omarchy", "plugin", "disable", pid])
                if proc.returncode == 0:
                    actions.append(f"disabled {pid} (off on the source machine)")
                else:
                    actions.append(fail(f"could not disable {pid}: "
                                        + (proc.stderr or proc.stdout).strip()[:200]))
    elif want_off and dry:
        actions.append(f"would disable up to {len(want_off)} ids the source has off")

    # A widget backed by a user daemon is dead without its unit.
    for plug in meta.get("plugins") or []:
        if not wanted("plugins", plug.get("id") or ""):
            continue
        for unit in plug.get("units") or []:
            if not shell_segment(unit):
                continue
            if dry:
                actions.append(f"{plug.get('id')} needs user unit {unit}")
                continue
            if run(["systemctl", "--user", "cat", unit]).returncode != 0:
                packed = cat_dir / "units" / unit
                if packed.is_file():
                    dest = home / ".config/systemd/user" / unit
                    backup_existing(dest, undo, home)
                    copy_file(packed, dest)
                    rewrite_in_place(dest, old_home, str(home))
                    run(["systemctl", "--user", "daemon-reload"])
                    proc = run(["systemctl", "--user", "enable", "--now", unit])
                    actions.append(f"installed and enabled {unit} for {plug.get('id')}"
                                   if proc.returncode == 0 else
                                   fail(f"installed {unit} but could not enable it: "
                                        + (proc.stderr or proc.stdout).strip()[:200]))
                    continue
                actions.append(fail(
                    f"{plug.get('id')} needs user unit {unit}, which is not installed here"
                    " and is not in this archive"))
            elif run(["systemctl", "--user", "is-active", unit]).returncode != 0:
                proc = run(["systemctl", "--user", "restart", unit])
                actions.append(f"started {unit} for {plug.get('id')}" if proc.returncode == 0
                               else fail(f"{unit} for {plug.get('id')} would not start: "
                                         + (proc.stderr or proc.stdout).strip()[:200]))
            else:
                # New code on disk, old daemon in memory: restart or it serves stale data.
                proc = run(["systemctl", "--user", "restart", unit])
                actions.append(f"restarted {unit} onto the restored {plug.get('id')} code"
                               if proc.returncode == 0 else
                               fail(f"could not restart {unit}: " + (proc.stderr or proc.stdout).strip()[:200]))
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
    repo = [p for p in (meta.get("repo") or []) if wanted("packages", f"repo:{p}")]
    aur = [p for p in (meta.get("aur") or []) if wanted("packages", f"aur:{p}")]
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
            if not wanted("themes", theme.get("id") or ""):
                continue
            if theme.get("url"):
                if dry:
                    actions.append(f"theme install {theme['url']}")
                else:
                    tid = theme.get("id") or ""
                    # omarchy-theme-install rm -rf's the destination BEFORE it
                    # clones, so an offline install destroys the existing theme
                    # with nothing to put back. Take an undo copy first.
                    if shell_segment(tid):
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
    dconf_file = cat_dir / "dconf-interface.ini"
    if dconf_file.is_file():
        if dry:
            actions.append(f"dconf load {DCONF_PATH} ({len(dconf_file.read_text().splitlines())} lines)")
        elif not shutil.which("dconf"):
            actions.append("dconf not installed, GTK appearance skipped")
        else:
            proc = subprocess.run(["dconf", "load", DCONF_PATH],
                                  input=dconf_file.read_text(encoding="utf-8"),
                                  text=True, capture_output=True)
            actions.append("dconf appearance loaded" if proc.returncode == 0
                           else fail("dconf load failed: " + (proc.stderr or proc.stdout).strip()[:200]))
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


def restore_bar(cat_dir: Path, home: Path, old_home: str, undo: Path, dry: bool,
                keys: list[str] | None = None) -> list[str]:
    shell_src = cat_dir / "files/.config/omarchy/shell.json"
    if not shell_src.is_file():
        # packed relative without leading handling
        matches = list((cat_dir / "files").rglob("shell.json")) if (cat_dir / "files").is_dir() else []
        shell_src = matches[0] if matches else shell_src
    # Everything except shell.json is a plain file; shell.json needs config-edit.
    others = restore_file_tree(cat_dir, home, old_home, undo, dry,
                               skip_rel=".config/omarchy/shell.json")
    if not shell_src.is_file():
        return others + ["no shell.json in this imprint, bar left alone"]
    dest = home / ".config/omarchy/shell.json"
    if not keys:
        return others + [fail("shell.json NOT applied: select individual settings with --shell-key /object/key")]
    if dry:
        return others + ["shell.json via config-edit"]
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
        snap_proc = run(["omarchy", "shell", "config-edit", "snapshot", str(snap)])
        if snap_proc.returncode == 0:
            try:
                archived = json.loads(text)
                merged = json.loads(snap.read_text())
                for key in keys:
                    if not key.startswith("/") or key in {"/", "/bar", "/plugins", "/disabledPlugins"} or key.startswith(("/bar/layout", "/bar/id", "/plugins/", "/disabledPlugins/")):
                        raise ValueError("layout and membership are manual; choose a non-layout leaf setting")
                    parts = [p.replace("~1", "/").replace("~0", "~") for p in key[1:].split("/")]
                    src, dst = archived, merged
                    for part in parts[:-1]:
                        src, dst = src[part], dst[part]
                    if not isinstance(dst, dict) or isinstance(src[parts[-1]], (dict, list)):
                        raise ValueError("select a scalar leaf setting, not a whole object or array")
                    dst[parts[-1]] = src[parts[-1]]
                edited.write_text(json.dumps(merged), encoding="utf-8")
            except (ValueError, KeyError, TypeError) as exc:
                return others + [fail(f"shell.json NOT applied: {exc}")]
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
                ]
            )
            if apply.returncode == 0:
                verify = run(["omarchy", "shell", "config-edit", "snapshot", str(snap)])
                try:
                    persisted = json.loads(dest.read_text()) == merged
                    live = json.loads(snap.read_text()) == merged
                except (OSError, ValueError):
                    persisted = live = False
                if verify.returncode or not persisted or not live:
                    return others + [fail("shell.json accepted but live/disk persistence verification failed")]
                return others + ["selected shell settings via config-edit; live and disk verified"]
            detail = (apply.stderr or apply.stdout).strip()[:200]
            return others + [fail(f"shell.json NOT applied, config-edit refused: {detail}")]
        detail = (snap_proc.stderr or snap_proc.stdout).strip()[:200]
        return others + [fail(f"shell.json NOT applied: live snapshot unavailable: {detail}")]
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
    reload = run(["systemctl", "--user", "daemon-reload"])
    if reload.returncode:
        return actions + [fail("user daemon-reload failed; enablement not attempted")]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for unit in meta.get("units") or []:
        name = unit.get("name")
        if not name:
            continue
        if unit.get("enabled"):
            proc = run(["systemctl", "--user", "enable", name])
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
    actions = restore_file_tree(cat_dir, home, old_home, undo, dry, category="scripts")
    meta_path = cat_dir / "meta.json"
    if not meta_path.is_file():
        return actions
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for entry in meta.get("links") or []:
        link_rel, target_rel = entry.get("link") or "", entry.get("target") or ""
        if not link_rel or not target_rel:
            continue
        if not wanted("scripts", link_rel):
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


def restore_system(cat_dir: Path, home: Path, dry: bool, allow: bool,
                   system_root: str = "/") -> list[str]:
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
            etc_target = strict_target(Path(system_root) / "etc" / rel)
            installs.append((path, etc_target))
            lines.append(f'install -Dm644 {shlex.quote(str(stage_etc / rel))} {shlex.quote(str(etc_target))}')
    units = [u for u in (meta.get("enabledUnits") or []) if shell_segment(u)]
    alt_root = system_root not in ("", "/")
    root_flag = f" --root={shlex.quote(system_root)}" if alt_root else ""
    if installs and not alt_root:
        lines.append("systemctl daemon-reload")
    for unit in units:
        lines.append(f"systemctl{root_flag} enable {shlex.quote(unit)} "
                     f"|| echo \"could not enable {unit}\" >&2")
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
    if alt_root:
        # Applying into an alternate root needs no privileges and cannot touch
        # the running system, which is how this path gets verified.
        Path(system_root).mkdir(parents=True, exist_ok=True)
        proc = run(["bash", str(script)])
        if proc.returncode != 0:
            return summary + [fail(f"system layer failed against {system_root}: "
                                   + (proc.stderr or proc.stdout).strip()[:400])]
        return summary + [f"system layer applied into {system_root}"] + \
            verify_units(units, system_root, alt_root)
    if run(["sudo", "-n", "true"]).returncode != 0:
        return summary + [fail(f"no non-interactive sudo; run it yourself: sudo {script}")]
    proc = run(["sudo", "-n", "bash", str(script)])
    if proc.returncode != 0:
        return summary + [fail("system layer failed: " + (proc.stderr or proc.stdout).strip()[:400])]
    return summary + ["system layer applied"] + verify_units(units, system_root, alt_root)


def verify_units(units: list[str], system_root: str, alt_root: bool) -> list[str]:
    """Check what actually ended up enabled.

    The generated script logs an enable failure to stderr and carries on, so
    without this a restore onto a box missing the packages reports success while
    leaving every service off. `systemctl is-enabled` is the only query that
    honours --root (cat does not), and its output distinguishes a missing unit
    from a present-but-disabled one.
    """
    root_flag = [f"--root={system_root}"] if alt_root else []
    # States that mean "nothing more to do": static and indirect units cannot be
    # enabled by name and are not a failure.
    fine = {"enabled", "enabled-runtime", "static", "indirect", "alias", "generated", "transient"}
    missing, not_enabled = [], []
    for unit in units:
        proc = run(["systemctl", *root_flag, "is-enabled", unit])
        state = (proc.stdout or proc.stderr).strip().splitlines()
        state = state[-1].strip() if state else ""
        if state == "not-found":
            missing.append(unit)
        elif state not in fine:
            not_enabled.append(f"{unit} ({state or 'unknown'})")
    out = []
    if missing:
        out.append(fail(
            f"{len(missing)} units have no unit file here, so they were not enabled "
            f"(is the package installed?): " + ", ".join(missing[:6])
            + (f" ... +{len(missing) - 6} more" if len(missing) > 6 else "")))
    if not_enabled:
        out.append(fail(
            f"{len(not_enabled)} units are present but did not enable: "
            + ", ".join(not_enabled[:6])
            + (f" ... +{len(not_enabled) - 6} more" if len(not_enabled) > 6 else "")))
    if not missing and not not_enabled and units:
        out.append(f"all {len(units)} system units verified enabled")
    return out


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
        return restore_bar(cat_dir, home, old_home, undo, dry, getattr(args, "shell_key", []))
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
        return restore_system(cat_dir, home, dry, bool(getattr(args, "allow_system", False)),
                              getattr(args, "system_root", "/") or "/")
    actions = restore_file_tree(cat_dir, home, old_home, undo, dry)
    if cid == "secrets" and not dry:
        ssh = home / ".ssh"
        if ssh.is_dir():
            # mkdir used the ambient umask; sshd and ssh both want 0700 here.
            os.chmod(ssh, 0o700)
            actions.append("chmod 700 ~/.ssh")
    return actions


def load_selection(args) -> None:
    spec_path = getattr(args, "select", "")
    if not spec_path:
        return
    path = need_file(spec_path, "cannot read the selection file")
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read the selection file {path}: {exc}")
    SUBSELECT.clear()
    for cid, keys in (spec.get("subselections") or {}).items():
        SUBSELECT[cid] = set(keys)


def preview_changes(root: Path, home: Path, manifest: dict, ids: list[str]) -> dict:
    """What a restore would actually alter, before anything is touched.

    A dry run lists the operations; this classifies them, so the difference
    between "rewrites 40 files" and "rewrites 2 and leaves 38 alone" is visible
    before you agree to it.
    """
    old_home = manifest.get("home") or ""
    files_new, files_changed, files_same = [], [], []
    for cid in ids:
        files_root = root / "categories" / cid / "files"
        if not files_root.is_dir():
            continue
        for path in iter_files(files_root):
            rel = path.relative_to(files_root)
            if cid == "scripts" and not wanted("scripts", str(rel)):
                continue
            live = home / rel
            entry = f"~/{rel}"
            if not live.exists() and not live.is_symlink():
                files_new.append(entry)
                continue
            packed = read_text_safe(path)
            current = read_text_safe(live)
            if packed is not None and current is not None:
                same = rewrite_text(packed, old_home, str(home)) == current
            else:
                try:
                    same = path.stat().st_size == live.stat().st_size
                except OSError:
                    same = False
            (files_same if same else files_changed).append(entry)

    out = {
        "filesNew": sorted(files_new),
        "filesChanged": sorted(files_changed),
        "filesUnchanged": len(files_same),
    }

    cats = manifest.get("categories") or {}
    if "plugins" in ids:
        meta_path = root / "categories/plugins/meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        live = {i.get("id"): i for i in plugin_list() if i.get("id")}
        install, update, enable, disable = [], [], [], []
        root_dir = home / ".config/omarchy/plugins"
        for plug in meta.get("plugins") or []:
            pid = plug.get("id")
            if not pid or not wanted("plugins", pid):
                continue
            target = root_dir / pid
            if not target.exists():
                install.append(pid)
            elif plug.get("commit") and git_head(target) != plug["commit"]:
                update.append(f"{pid} {git_head(target)[:8]}\u2192{plug['commit'][:8]}")
            if plug.get("enabled") and not live.get(pid, {}).get("enabled"):
                enable.append(pid)
        for pid in meta.get("disabledIds") or []:
            if live.get(pid, {}).get("enabled"):
                disable.append(pid)
        out["pluginsInstall"] = sorted(install)
        out["pluginsUpdate"] = sorted(update)
        out["pluginsEnable"] = sorted(enable)
        out["pluginsDisable"] = sorted(disable)
    if "packages" in ids:
        pkg = cats.get("packages") or {}
        have = set(run_ok(["pacman", "-Qq"]).split())
        missing = [p for p in (pkg.get("repo") or []) + (pkg.get("aur") or [])
                   if p not in have and wanted("packages", f"repo:{p}") | wanted("packages", f"aur:{p}")]
        out["packagesInstall"] = sorted(missing)
    if "projects" in ids:
        meta_path = root / "categories/projects/meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        clone = [r["path"] for r in (meta.get("repos") or [])
                 if r.get("path") and wanted("projects", r["path"])
                 and not (home / r["path"]).exists()]
        out["projectsClone"] = sorted(clone)
    if "identity" in ids:
        meta_path = root / "categories/identity/meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        if meta.get("hostname") and meta["hostname"] != hostname():
            out["hostname"] = f"{hostname()} \u2192 {meta['hostname']}"
    return out


def render_preview(changes: dict, ids: list[str]) -> str:
    grey, amber, green, red = (FG.get("grey", ""), FG.get("amber", ""),
                               FG.get("green", ""), FG.get("red", ""))
    tint = colour_ok(sys.stderr)
    c = lambda code, text: f"{code}{text}{RESET}" if tint and code else text
    lines = ["", c(BOLD, "This restore will change:"), ""]

    def block(title, items, colour, limit=12):
        if not items:
            return
        lines.append(f"  {c(colour, title)}  {c(grey, f'({len(items)})')}")
        for entry in items[:limit]:
            lines.append(f"      {entry}")
        if len(items) > limit:
            lines.append(c(grey, f"      \u2026 and {len(items) - limit} more"))
        lines.append("")

    block("overwrite existing files", changes.get("filesChanged") or [], amber)
    block("create new files", changes.get("filesNew") or [], green)
    block("install plugins", changes.get("pluginsInstall") or [], green)
    block("move plugins to another commit", changes.get("pluginsUpdate") or [], amber)
    block("enable plugins", changes.get("pluginsEnable") or [], green)
    block("DISABLE plugins currently on", changes.get("pluginsDisable") or [], red)
    block("install packages", changes.get("packagesInstall") or [], green)
    block("clone projects", changes.get("projectsClone") or [], green)
    if changes.get("hostname"):
        lines.append(f"  {c(red, 'rename this machine')}  {changes['hostname']}")
        lines.append("")

    unchanged = changes.get("filesUnchanged") or 0
    touched = (len(changes.get("filesChanged") or []) + len(changes.get("filesNew") or [])
               + len(changes.get("pluginsInstall") or []) + len(changes.get("pluginsUpdate") or [])
               + len(changes.get("pluginsEnable") or []) + len(changes.get("pluginsDisable") or [])
               + len(changes.get("packagesInstall") or []) + len(changes.get("projectsClone") or []))
    if not touched:
        lines.append(f"  {c(green, 'Nothing to change - this machine already matches the imprint.')}")
        lines.append("")
    lines.append(c(grey, f"  {len(ids)} categories \u00b7 {unchanged} files already identical"))
    lines.append("")
    return "\n".join(lines)


def strict_target(path: Path) -> Path:
    """A lexical absolute target, with no existing symlink components."""
    if not path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"target must be absolute without traversal: {path}")
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise SystemExit(f"refused symlink target: {parent}")
    if path.exists() and not (path.is_file() or path.is_dir()):
        raise SystemExit(f"refused special target: {path}")
    return path


def atomic_bytes(dest: Path, data: bytes, mode: int) -> None:
    """Write via a no-follow directory descriptor and atomic rename."""
    strict_target(dest)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    temp = ".imprint-" + os.urandom(12).hex()
    try:
        for part in dest.parent.parts[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temp, dest.name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass
        os.close(fd)


def file_recovery(args, preview: bool = False) -> int:
    """The category payload path without any recipe or activation side effects."""
    home = strict_target(Path(getattr(args, "target_home", "") or Path.home()))
    if not home.is_dir():
        raise SystemExit(f"target home must already exist: {home}")
    load_selection(args)
    with tempfile.TemporaryDirectory(prefix="imprint-files-") as tmp:
        root = open_imprint(need_archive(args.archive), Path(tmp) / "open")
        manifest = load_manifest(root)
        if not isinstance(manifest.get("integrity"), dict):
            raise SystemExit("files-only recovery requires an integrity-indexed archive; make a new save")
        ids = parse_only(args.only) if args.only else []
        if not ids:
            raise SystemExit("files-only requires explicit --only categories")
        system_root = strict_target(Path(getattr(args, "system_root", "/")))
        if "system" in ids:
            if not system_root.is_dir():
                raise SystemExit("system root must already exist")
            if not preview and not getattr(args, "dry_run", False) and not getattr(args, "allow_system", False):
                raise SystemExit("system files require --allow-system")
        requested = set(getattr(args, "file", []) or [])
        found = set()
        operations = {}
        for cid in ids:
            files = root / "categories" / cid / ("etc" if cid == "system" else "files")
            for src in sorted(iter_files(files)):
                rel = src.relative_to(files).as_posix()
                if cid == "system":
                    rel = "etc/" + rel
                if requested and rel not in requested:
                    continue
                if not wanted(cid, rel):
                    continue
                found.add(rel)
                if rel == ".config/omarchy/shell.json":
                    raise SystemExit("shell.json needs a selective live config-edit merge, not files-only recovery")
                if rel.startswith(".local/state/imprint/"):
                    raise SystemExit("refused recovery state overwrite")
                dest = strict_target((system_root if cid == "system" else home) / rel)
                if dest.is_dir() or src.is_symlink():
                    raise SystemExit(f"files-only requires regular files: {rel}")
                data = src.read_bytes()
                text = read_text_safe(src)
                if text is not None:
                    data = rewrite_text(text, manifest.get("home") or "", str(home)).encode("utf-8")
                if src.suffix == ".py":
                    try:
                        compile(data, rel, "exec")  # syntax only; never execute recovered code
                    except (SyntaxError, ValueError) as exc:
                        raise SystemExit(f"invalid Python configuration {rel}: {exc}")
                if src.suffix == ".json":
                    try:
                        json.loads(data)
                    except (ValueError, UnicodeError) as exc:
                        raise SystemExit(f"invalid JSON configuration {rel}: {exc}")
                if src.suffix == ".sh" or data.startswith((b"#!/bin/sh", b"#!/bin/bash", b"#!/usr/bin/env bash")):
                    checked = subprocess.run(["bash", "--noprofile", "--norc", "-n"],
                                             input=data, capture_output=True, env={"PATH": os.defpath})
                    if checked.returncode:
                        raise SystemExit(f"invalid shell syntax: {rel}")
                mode = manifest["integrity"][src.relative_to(root).as_posix()]["mode"]
                previous = dest.read_bytes() if dest.exists() else None
                old_mode = stat.S_IMODE(dest.stat().st_mode) if dest.exists() else None
                diff = ""
                if text is not None:
                    diff = "".join(difflib.unified_diff(
                        (previous or b"").decode("utf-8", errors="replace").splitlines(True),
                        data.decode("utf-8").splitlines(True), fromfile=str(dest), tofile="archive (mapped)"))
                entry = {"source": src.relative_to(root).as_posix(), "destination": str(dest),
                         "path": rel, "scope": "system" if cid == "system" else "home",
                         "mode": mode, "previousMode": old_mode,
                         "sha256": hashlib.sha256(data).hexdigest(), "diff": diff,
                         "changed": previous != data or old_mode != mode}
                if rel in operations and operations[rel][1] != data:
                    raise SystemExit(f"conflicting category payloads: {rel}")
                operations[rel] = (entry, data, previous)
        if requested - found:
            raise SystemExit("selected files not present: " + ", ".join(sorted(requested - found)))
        if not operations:
            raise SystemExit("no regular payload files selected; files-only does not execute recipes")
        entries = [entry for entry, _, _ in operations.values()]
        report = {"ok": True, "files": entries, "targetHome": str(home), "undo": None,
                  "activation": "not performed; validate applications and reload/restart explicitly"}
        if preview or getattr(args, "dry_run", False):
            emit(report, args)
            return 0
        state = strict_target(home / ".local/state/imprint")
        undo = new_undo_dir(state)
        undo.mkdir(parents=True, mode=0o700)
        report["undo"] = str(undo)
        journal = {"targetHome": str(home), "systemRoot": str(system_root), "files": []}
        for entry, _, previous in operations.values():
            if not entry["changed"]:
                continue
            backup = str(len(journal["files"]))
            if previous is not None:
                atomic_bytes(undo / backup, previous, 0o600)
            journal["files"].append({"path": entry["path"], "scope": entry["scope"],
                                     "backup": backup if previous is not None else None,
                                     "mode": entry["previousMode"]})
        atomic_bytes(undo / "recovery.json", json.dumps(journal).encode(), 0o600)
        try:
            for entry, data, _ in operations.values():
                if entry["changed"]:
                    atomic_bytes(Path(entry["destination"]), data, entry["mode"])
        except (OSError, SystemExit) as exc:
            report["ok"] = False
            report["error"] = str(exc)
            try:
                undo_file_recovery(undo, home, system_root, quiet=True)
                report["rollback"] = "restored original files"
            except (OSError, SystemExit) as rollback_error:
                report["rollback"] = "FAILED: " + str(rollback_error)
            emit(report, args)
            return 1
        emit(report, args)
        return 0


def undo_file_recovery(chosen: Path, home: Path, system_root: Path = Path("/"), quiet: bool = False) -> int:
    journal = json.loads((chosen / "recovery.json").read_text())
    if journal["targetHome"] != str(home):
        raise SystemExit("undo target differs from the recorded target home")
    if journal.get("systemRoot", "/") != str(system_root):
        raise SystemExit("undo system root differs from the recorded root")
    failures = []
    for record in reversed(journal["files"]):
        rel = record["path"]
        try:
            if Path(rel).is_absolute() or any(not safe_segment(p) for p in rel.split("/")):
                raise SystemExit("unsafe undo path")
            dest = strict_target((system_root if record.get("scope") == "system" else home) / rel)
            if record["backup"] is None:
                dest.unlink(missing_ok=True)
            else:
                backup = record["backup"]
                if not safe_segment(backup):
                    raise SystemExit("unsafe undo backup")
                data = (chosen / backup).read_bytes()
                # A failed replacement may leave its destination untouched. Do not
                # rewrite it; still inspect every record in case rename succeeded
                # before the forward directory fsync failed.
                if (dest.exists() and dest.read_bytes() == data
                        and stat.S_IMODE(dest.stat().st_mode) == record["mode"]):
                    continue
                atomic_bytes(dest, data, record["mode"])
        except (OSError, SystemExit) as exc:
            failures.append(f"{rel}: {exc}")
    if failures:
        raise SystemExit("partial rollback; unrecovered files: " + "; ".join(failures))
    if not quiet:
        json.dump({"ok": True, "undo": str(chosen), "count": len(journal["files"])}, sys.stdout)
        sys.stdout.write("\n")
    return 0


def cmd_preview(args) -> int:
    if getattr(args, "files_only", False):
        return file_recovery(args, preview=True)
    ensure_session_env()
    load_selection(args)
    archive = need_archive(args.archive)
    home = Path.home()
    with tempfile.TemporaryDirectory(prefix="imprint-preview-") as tmp:
        root = open_imprint(archive, Path(tmp) / "open")
        manifest = load_manifest(root)
        available = [cid for cid in (manifest.get("categories") or {})
                     if (root / "categories" / cid).exists()]
        ids = parse_only(args.only) if args.only else available
        ids = [cid for cid in ids if cid in available]
        if not ids:
            raise SystemExit("no selected categories are present in this imprint")
        changes = preview_changes(root, home, manifest, ids)
        if getattr(args, "json", False):
            json.dump({"ok": True, "categories": ids, "changes": changes}, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stderr.write(render_preview(changes, ids))
    return 0


def cmd_restore(args) -> int:
    if getattr(args, "files_only", False):
        return file_recovery(args)
    if getattr(args, "target_home", "") or getattr(args, "file", []):
        raise SystemExit("--target-home and --file require --files-only")
    ensure_session_env()
    load_selection(args)
    archive = need_archive(args.archive)
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
                note("upgrading the machine first (omarchy update -y)", "amber")
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
        progress = Progress(len(order), ("Restoring onto " if not dry else "Rehearsing on ") + hostname())
        for cid in order:
            title = category_by_id(cid)["title"]
            progress.update(title)
            before = len(FAILURES)
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
            progress.item(title, f"{len(actions)} actions", ok=len(FAILURES) == before)
        report["activation"] = "restart-required where applicable; no automatic desktop reload or restart"
        progress.finish("Restore complete" if not FAILURES
                        else f"Restore finished with {len(FAILURES)} problem(s)",
                        f"{len(order)} categories")
        report["ok"] = not FAILURES
        report["failures"] = list(FAILURES)
        for problem in FAILURES[:6]:
            note(problem[:160], "red")
        emit(report, args)
    return 0 if not FAILURES else 1


def cmd_info(args) -> int:
    archive = need_archive(args.archive)
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



def sh(value: str) -> str:
    return shlex.quote(str(value))


PAYLOAD_VAR = "$PLAN_PAYLOAD"


def payload_ref(root: Path, path: Path) -> str:
    """Reference a file inside the plan payload without baking in its location,
    so the plan directory can be moved or copied to another machine."""
    try:
        return f'"{PAYLOAD_VAR}"/{shlex.quote(str(path.relative_to(root)))}'
    except ValueError:
        return sh(path)


class Plan:
    """An ordered list of shell steps, each one safe to re-run."""

    def __init__(self) -> None:
        self.steps: list[dict] = []

    def add(self, phase: str, title: str, body: list[str], *, note: str = "") -> None:
        self.steps.append({"phase": phase, "title": title, "body": body, "note": note})

    def phases(self) -> list[str]:
        seen = []
        for step in self.steps:
            if step["phase"] not in seen:
                seen.append(step["phase"])
        return seen


def plan_packages(plan: Plan, meta: dict) -> None:
    repo = [p for p in (meta.get("repo") or []) if wanted("packages", f"repo:{p}")]
    aur = [p for p in (meta.get("aur") or []) if wanted("packages", f"aur:{p}")]
    if repo:
        plan.add("packages", f"{len(repo)} repo packages",
                 ["omarchy pkg add " + " ".join(sh(x) for x in repo)],
                 note="pkg add is already a no-op for packages that are present")
    if aur:
        plan.add("packages", f"{len(aur)} AUR packages",
                 ["omarchy pkg aur add " + " ".join(sh(x) for x in aur)])


def plan_toolchains(plan: Plan, meta: dict, root: Path) -> None:
    src = root / "categories/toolchains/files/.config/mise/config.toml"
    if src.is_file():
        plan.add("toolchains", "mise tool versions", [
            'mkdir -p "$HOME/.config/mise"',
            f'cp {payload_ref(root, src)} "$HOME/.config/mise/config.toml"',
            "mise install --yes",
        ])
    for tool in meta.get("go") or []:
        if tool.get("module"):
            plan.add("toolchains", f"go tool {tool['name']}",
                     [f"go install {sh(tool['module'])}@latest"])
    for crate in meta.get("cargo") or []:
        plan.add("toolchains", f"cargo {crate}", [f"cargo install {sh(crate)}"])
    for pkg in meta.get("npmGlobal") or []:
        plan.add("toolchains", f"npm -g {pkg}", [f"npm install -g {sh(pkg)}"])


def plan_projects(plan: Plan, meta: dict, root: Path) -> None:
    for repo in meta.get("repos") or []:
        rel, url = repo.get("path"), repo.get("url")
        if not rel or not wanted("projects", rel):
            continue
        if not url:
            plan.add("projects", f"{rel} (NO REMOTE)", [
                f'echo "cannot restore ~/{rel}: no git remote recorded" >&2',
            ], note="this checkout exists nowhere else")
            continue
        branch = repo.get("branch") or ""
        body = [f'if [ ! -d "$HOME"/{sh(rel)}/.git ]; then',
                f'  git clone {"-b " + sh(branch) + " " if branch else ""}{sh(url)} "$HOME"/{sh(rel)}',
                'else',
                f'  echo "~/{rel} already present, left alone"',
                'fi']
        patch = repo.get("patch")
        if patch:
            pfile = root / "categories/projects" / patch
            ref = payload_ref(root, pfile)
            body += [f'if [ -f {ref} ]; then',
                     f'  git -C "$HOME"/{sh(rel)} apply --3way {ref} || '
                     f'echo "patch for {rel} did not apply cleanly" >&2',
                     'fi']
        plan.add("projects", rel, body)


def plan_themes(plan: Plan, meta: dict, root: Path) -> None:
    for theme in meta.get("themes") or []:
        if theme.get("url"):
            plan.add("themes", f"theme {theme.get('id')}",
                     [f"omarchy theme install {sh(theme['url'])} || true"])


def plan_plugins(plan: Plan, meta: dict, root: Path) -> None:
    cat = root / "categories/plugins"
    for plug in meta.get("plugins") or []:
        pid = plug.get("id")
        if not pid or not shell_segment(pid):
            continue
        # Quoted, because pid is archive metadata and this string is executed.
        dest = '"$HOME"/.config/omarchy/plugins/' + sh(pid)
        body: list[str] = []
        if plug.get("kind") == "git" and plug.get("url"):
            # plugin add clones into a temp dir before noticing the id is taken,
            # so skip it outright when the plugin is already there.
            body.append(f'[ -d {dest} ] || omarchy plugin add {sh(plug["url"])} --yes || true')
            if not (plug.get("branch") or plug.get("commit")):
                # Nothing downstream would notice a failed clone for this one.
                body.append(f'[ -d {dest} ] || '
                            f'{{ echo {sh(pid + " was not installed")} >&2; exit 1; }}')
            branch, commit = plug.get("branch") or "", plug.get("commit") or ""
            if branch or commit:
                body.append(f'git -C {dest} remote get-url imprint-src >/dev/null 2>&1 '
                            f'|| git -C {dest} remote add imprint-src {sh(plug["url"])}')
                bundle = plug.get("bundle")
                if bundle:
                    bfile = payload_ref(root, cat / bundle)
                    body.append(f'[ -f {bfile} ] && git -C {dest} fetch {bfile} '
                                f'{sh(branch)}:refs/remotes/imprint-bundle/{sh(branch)} --force || true')
                body.append(f'git -C {dest} fetch imprint-src --quiet || true')
                ref = commit or f"imprint-src/{branch}"
                overlay = plug.get("overlay")
                # -f when an overlay follows: the local modifications and the
                # untracked files a previous overlay left behind are exactly what
                # we are about to rewrite, and without it the checkout aborts.
                force = " -f" if overlay else ""
                body.append(f'imprint_checkout {dest} {sh(branch)} {sh(ref)}{force} || '
                            f'{{ echo {sh(f"could not put {pid} on {branch}")} >&2; '
                            f'exit 1; }}')
            else:
                overlay = plug.get("overlay")
            if overlay:
                body.append(f'cp -a {payload_ref(root, cat / overlay)}/. {dest}/')
        else:
            tree = plug.get("tree")
            if tree:
                body += [f'mkdir -p {dest}', f'cp -a {payload_ref(root, cat / tree)}/. {dest}/']
            elif plug.get("clonedFrom"):
                body.append(f'omarchy plugin clone {sh(plug["clonedFrom"])} || true')
        for unit in plug.get("units") or []:
            if not shell_segment(unit):
                continue
            ufile = payload_ref(root, cat / "units" / unit)
            body += [f'if [ -f {ufile} ]; then',
                     f'  install -Dm644 {ufile} "$HOME"/.config/systemd/user/{sh(unit)}',
                     f'  systemctl --user daemon-reload',
                     f'  systemctl --user enable --now {sh(unit)}',
                     f'  systemctl --user restart {sh(unit)}',
                     'fi']
        if body:
            plan.add("plugins", pid, body)

    enable = [p for p in (meta.get("plugins") or []) if p.get("enabled") and shell_segment(p.get("id") or "")]
    if enable:
        body = ['omarchy shell -q shell ping >/dev/null 2>&1 || '
                '{ echo "shell unreachable, enabled state NOT applied" >&2; exit 1; }',
                'omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true']
        for plug in enable:
            place = plug.get("placement") or {}
            extra = ""
            if place.get("section"):
                extra = f" --section {sh(place['section'])}"
                if isinstance(place.get("index"), int):
                    extra += f" --index {place['index']}"
            body.append(f'omarchy plugin enable {sh(plug["id"])}{extra} >/dev/null 2>&1 || true')
        plan.add("activate", f"enable {len(enable)} plugins where the source had them", body)
    off = [i for i in (meta.get("disabledIds") or []) if shell_segment(i)]
    if off:
        body = ['omarchy shell -q shell ping >/dev/null 2>&1 || '
                '{ echo "shell unreachable, disables NOT applied" >&2; exit 1; }',
                "for _ in 1 2 3; do"]
        for pid in off:
            body.append(f'  omarchy plugin disable {sh(pid)} >/dev/null 2>&1 || true')
        body.append("done")
        plan.add("activate", f"disable {len(off)} ids the source had off", body,
                 note="repeated because disabling a clone hands the slot back to its built-in")


def plan_files(plan: Plan, cid: str, root: Path) -> None:
    files_root = root / "categories" / cid / "files"
    if not files_root.is_dir():
        return
    paths = [p.relative_to(files_root).as_posix() for p in iter_files(files_root)
             if p.relative_to(files_root).as_posix() != ".config/omarchy/shell.json"]
    if not paths:
        return
    selected = " ".join("--file " + sh(p) for p in paths)
    plan.add("files", f"{cid}: {len(paths)} files", [
        f'python3 "$PLAN_PAYLOAD/tool/imprint-engine.py" restore "$PLAN_PAYLOAD" --only {sh(cid)} --files-only {selected}',
    ], note="verified selective payload recovery with home rewrite and undo; shell settings require --shell-key separately")


def build_plan(root: Path, manifest: dict, ids: list[str]) -> Plan:
    plan = Plan()
    cats = manifest.get("categories") or {}
    plan.add("preflight", "check this is an Omarchy machine", [
        'command -v omarchy >/dev/null || { echo "omarchy not found" >&2; exit 1; }',
        'command -v git >/dev/null || { echo "git not found" >&2; exit 1; }',
    ])
    plan.add("preflight", "check the Omarchy shell is reachable", [
        'if ! omarchy shell -q shell ping >/dev/null 2>&1; then',
        '  echo "the Omarchy shell is not answering; plugin enable/disable cannot take effect" >&2',
        '  echo "run this from inside the desktop session, not over a bare ssh login" >&2',
        '  exit 1',
        'fi',
    ], note="enable/disable go through the shell's IPC, so a dead shell means silent no-ops")
    if "packages" in ids:
        plan.add("upgrade", "bring the machine up to date first", [
            "omarchy update -y",
        ], note="Arch does not support partial upgrades; installing onto a stale system is how it breaks")
    order = ["packages", "toolchains", "projects", "themes", "plugins"]
    for cid in order:
        if cid not in ids:
            continue
        meta = cats.get(cid) or {}
        if cid == "packages":
            plan_packages(plan, meta)
        elif cid == "toolchains":
            plan_toolchains(plan, meta, root)
            plan_files(plan, cid, root)
        elif cid == "projects":
            plan_projects(plan, meta, root)
        elif cid == "themes":
            plan_themes(plan, meta, root)
            plan_files(plan, cid, root)
        elif cid == "plugins":
            plan_plugins(plan, meta, root)
    for cid in ids:
        if cid in order or cid in {"system", "identity", "secrets"}:
            continue
        plan_files(plan, cid, root)
    if "system" in ids:
        plan.add("system", "root-owned changes", [
            'echo "run: imprint restore <archive> --only system --allow-system" >&2',
        ], note="/etc and systemctl enable need root and are deliberately not inlined here")
    plan.add("activate", "report pending activation", [
        'printf "%s\n" "restart-required: validate restored configuration before explicit reload/restart"',
    ])
    return plan


def render_plan_script(plan: Plan, manifest: dict, archive: Path, root: Path) -> str:
    out = [
        "#!/usr/bin/env bash",
        "# Generated by imprint. Review before running -- this changes your machine.",
        f"# Source machine : {manifest.get('hostname')} ({manifest.get('omarchy')})",
        f"# Imprint created: {manifest.get('created')}",
        f"# Archive        : {archive}",
        f"# Payload        : {root}",
        "#",
        "# Every step is written to be safe to re-run. Steps that may legitimately",
        "# fail end in `|| true`; anything else failing is counted and reported.",
        "set -uo pipefail",
        "",
        'PLAN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        ': "${PLAN_PAYLOAD:=$PLAN_DIR/payload}"; export PLAN_PAYLOAD',
        "",
        "# The omarchy CLI and the shell IPC need a desktop session. Without this",
        "# every enable/disable silently does nothing, which is easy to miss.",
        ': "${OMARCHY_PATH:=/usr/share/omarchy}"; export OMARCHY_PATH',
        ': "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"; export XDG_RUNTIME_DIR',
        'if [ -z "${WAYLAND_DISPLAY:-}" ]; then',
        '  for _s in "$XDG_RUNTIME_DIR"/wayland-*; do',
        '    case "$_s" in *.lock) continue ;; esac',
        '    [ -e "$_s" ] || continue',
        '    WAYLAND_DISPLAY=$(basename "$_s"); export WAYLAND_DISPLAY; break',
        '  done',
        'fi',
        'if [ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ] && command -v hyprctl >/dev/null 2>&1; then',
        "  _his=$(hyprctl instances 2>/dev/null | awk '/^instance /{print $2}' | tr -d ':' | head -1)",
        '  [ -n "$_his" ] && { HYPRLAND_INSTANCE_SIGNATURE=$_his; export HYPRLAND_INSTANCE_SIGNATURE; }',
        'fi',
        "",
        "fail=0",
        "failed_steps=()",
        "",
    ]
    for phase in plan.phases():
        out.append("echo")
        out.append(f"echo '### {phase}'")
        for st in (x for x in plan.steps if x["phase"] == phase):
            out.append("echo")
            out.append(f"echo {sh('== ' + st['title'])}")
            if st["note"]:
                out.append("# " + " ".join(st["note"].split()))
            # A subshell with -e so the status reflects the whole step, not
            # just its last line -- and its status is captured, never tested by
            # `if`, because bash suspends errexit inside a condition and the
            # step would sail past its own first failure.
            out.append("( set -e")
            out.extend("  " + line for line in st["body"])
            out.append(")")
            out.append("step_rc=$?")
            out.append('if [ "$step_rc" -ne 0 ]; then')
            out.append("  fail=$((fail+1))")
            out.append(f"  failed_steps+=({sh(st['title'])})")
            out.append("fi")
        out.append("")
    out += [
        'if [ "$fail" -gt 0 ]; then',
        '  echo >&2',
        '  echo "$fail step(s) failed:" >&2',
        '  printf \'  - %s\\n\' "${failed_steps[@]}" >&2',
        '  exit 1',
        'fi',
        'echo',
        'echo "plan applied cleanly"',
    ]
    return "\n".join(out) + "\n"


def step_id(index: int, step: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", step["title"].lower()).strip("-")[:40] or "step"
    return f"{index:03d}-{step['phase']}-{slug}"


def body_hash(body: list[str]) -> str:
    import hashlib
    return hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest()[:16]


def step_record(index: int, step: dict) -> dict:
    return {
        "id": step_id(index, step),
        "phase": step["phase"],
        "title": step["title"],
        "note": step.get("note", ""),
        "body": step["body"],
        "hash": body_hash(step["body"]),
    }


def session_preamble() -> list[str]:
    """Same desktop-session bootstrap the generated script uses."""
    return [
        ': "${OMARCHY_PATH:=/usr/share/omarchy}"; export OMARCHY_PATH',
        ': "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"; export XDG_RUNTIME_DIR',
        'if [ -z "${WAYLAND_DISPLAY:-}" ]; then',
        '  for _s in "$XDG_RUNTIME_DIR"/wayland-*; do',
        '    case "$_s" in *.lock) continue ;; esac',
        '    [ -e "$_s" ] || continue',
        '    WAYLAND_DISPLAY=$(basename "$_s"); export WAYLAND_DISPLAY; break',
        '  done',
        'fi',
        'if [ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ] && command -v hyprctl >/dev/null 2>&1; then',
        "  _his=$(hyprctl instances 2>/dev/null | awk '/^instance /{print $2}' | tr -d ':' | head -1)",
        '  [ -n "$_his" ] && { HYPRLAND_INSTANCE_SIGNATURE=$_his; export HYPRLAND_INSTANCE_SIGNATURE; }',
        'fi',
        '',
        '# Check out a recorded ref, clearing only the untracked files that the',
        '# target commit itself tracks -- those are stale copies an earlier restore',
        '# left behind. Genuine local additions are never touched.',
        'imprint_checkout() {',
        '  local dest=$1 branch=$2 ref=$3 force=${4:-}',
        '  if git -C "$dest" checkout $force -B "$branch" "$ref" 2>/dev/null; then return 0; fi',
        '  local f',
        '  while IFS= read -r -d "" f; do',
        '    if git -C "$dest" cat-file -e "$ref:$f" 2>/dev/null; then rm -f -- "$dest/$f"; fi',
        '  done < <(git -C "$dest" ls-files --others --exclude-standard -z)',
        '  git -C "$dest" checkout $force -B "$branch" "$ref"',
        '}',
    ]


def cmd_plan(args) -> int:
    archive = need_archive(args.archive)
    # Check in private scratch space before creating/replacing any plan output.
    # Never execute archived code merely to ask which arguments it supports.
    with tempfile.TemporaryDirectory(prefix="imprint-plan-check-") as tmp:
        checked = open_imprint(archive, Path(tmp) / "open")
        manifest = load_manifest(checked)
        embedded = checked / "tool/imprint-engine.py"
        if (not isinstance(manifest.get("integrity"), dict)
                or not embedded.is_file()
                or embedded.read_bytes() != Path(__file__).resolve().read_bytes()):
            raise SystemExit("plan requires an integrity-indexed archive with this installed engine; "
                             "upgrade Imprint and make a new save with this installed version")
    out_dir = Path(args.output).expanduser() if args.output else (
        Path.home() / ".local/state/imprint" / f"plan-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    if args.output:
        # A directory the user named must already be there, like any other.
        need_dir(args.output, "cannot write the plan")
        if not os.access(out_dir, os.W_OK):
            raise SystemExit(f"cannot write the plan into {out_dir}: permission denied")
    else:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SystemExit("\n".join(
                [f"cannot create the plan directory {out_dir}: {why(exc)}"]
                + path_advice(out_dir)))
    payload = out_dir / "payload"
    if archive.is_dir():
        root = archive
    else:
        if payload.exists():
            shutil.rmtree(payload)
        extract_archive(archive, payload)
        root = payload if (payload / "manifest.json").is_file() else open_imprint(archive, payload)
    manifest = load_manifest(root)
    available = [cid for cid in (manifest.get("categories") or {}) if (root / "categories" / cid).exists()]
    ids = parse_only(args.only) if args.only else available
    ids = [cid for cid in ids if cid in available]
    if not ids:
        raise SystemExit("no selected categories are present in this imprint")
    plan = build_plan(root, manifest, ids)
    script = out_dir / "restore.sh"
    script.write_text(render_plan_script(plan, manifest, archive, root), encoding="utf-8")
    os.chmod(script, 0o755)
    write_json(out_dir / "plan.json", {
        "archive": str(archive), "payload": str(root), "hostname": manifest.get("hostname"),
        "categories": ids, "phases": plan.phases(),
        "preamble": session_preamble(),
        "steps": [step_record(i, x) for i, x in enumerate(plan.steps)],
    })
    result = {"ok": True, "plan": str(out_dir), "script": str(script),
              "steps": len(plan.steps), "phases": plan.phases(), "categories": ids}
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0



JOURNAL_NAME = "journal.json"


def load_journal(plan_dir: Path) -> dict:
    path = plan_dir / JOURNAL_NAME
    if not path.is_file():
        return {"runs": 0, "steps": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"runs": 0, "steps": {}}
    if not isinstance(data.get("steps"), dict):
        data["steps"] = {}
    return data


def save_journal(plan_dir: Path, journal: dict) -> None:
    # Written after every step so an interrupted run can be resumed.
    write_json(plan_dir / JOURNAL_NAME, journal)


def run_step(step: dict, preamble: list[str], plan_dir: Path, payload: Path) -> tuple[int, str]:
    script = "\n".join([
        "#!/usr/bin/env bash",
        "set -uo pipefail",
        f"export PLAN_PAYLOAD={shlex.quote(str(payload))}",
        *preamble,
        "set -e",
        *step["body"],
    ]) + "\n"
    # A fixed name, so a killed run leaves one stale file rather than a pile.
    path = plan_dir / ".imprint-step.sh"
    try:
        path.write_text(script, encoding="utf-8")
        proc = run(["bash", str(path)])
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode, tail[-1200:]
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def failed_now(journal: dict) -> list:
    return [k for k, v in journal.get("steps", {}).items() if v.get("state") == "failed"]


def cmd_apply(args) -> int:
    plan_dir = need_dir(args.plan, "cannot read the plan")
    plan_file = plan_dir / "plan.json"
    if not plan_file.is_file():
        raise SystemExit(f"{plan_dir} holds no plan.json, so it is not a plan "
                         "directory -- `imprint plan <archive>` writes one")
    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{plan_file} is not readable as a plan: {exc}")
    steps = plan.get("steps") or []
    preamble = plan.get("preamble") or []
    if not steps:
        raise SystemExit("this plan has no steps")

    payload = plan_dir / "payload"
    if not payload.is_dir():
        recorded = Path(plan.get("payload") or "")
        if not recorded.is_dir():
            raise SystemExit(
                f"the plan's payload is missing (looked in {payload} and {recorded}); "
                "re-run `imprint plan` to rebuild it")
        payload = recorded

    known_phases = {st["phase"] for st in steps}
    if args.phase and args.phase not in known_phases:
        raise SystemExit(f"no phase {args.phase!r} in this plan; it has: "
                         + ", ".join(sorted(known_phases)))
    try:
        (plan_dir / ".imprint-write-test").write_text("", encoding="utf-8")
        (plan_dir / ".imprint-write-test").unlink()
    except OSError as exc:
        raise SystemExit(f"cannot write the journal into {plan_dir}: {exc}")

    journal = load_journal(plan_dir) if args.resume else {"runs": 0, "steps": {}}
    if args.restart:
        journal = {"runs": 0, "steps": {}}
    journal["runs"] = int(journal.get("runs") or 0) + 1
    journal["plan"] = str(plan_file)
    journal["startedAt"] = iso_now()

    todo, skipped = [], []
    for step in steps:
        if args.phase and step["phase"] != args.phase:
            continue
        prior = (journal["steps"].get(step["id"]) or {})
        # A step whose body changed since it succeeded is not done any more.
        if (prior.get("state") == "succeeded" and prior.get("hash") == step["hash"]
                and not args.recheck):
            skipped.append(step)
            continue
        todo.append(step)

    progress = Progress(len(todo), f"Applying {len(todo)} step(s)"
                        + (f", {len(skipped)} already done" if skipped else ""))
    for step in todo:
        journal["steps"][step["id"]] = {
            "phase": step["phase"], "title": step["title"], "hash": step["hash"],
            "state": "running", "startedAt": iso_now(),
        }
        save_journal(plan_dir, journal)
        if args.dry_run:
            journal["steps"][step["id"]].update(state="pending", finishedAt=iso_now(),
                                                note="dry run, not executed")
            save_journal(plan_dir, journal)
            progress.item(step["title"], f"[{step['phase']}] would run")
            continue
        progress.update(f"[{step['phase']}] {step['title']}")
        code, tail = run_step(step, preamble, plan_dir, payload)
        entry = journal["steps"][step["id"]]
        entry["finishedAt"] = iso_now()
        entry["exit"] = code
        entry["output"] = tail
        entry["state"] = "succeeded" if code == 0 else "failed"
        save_journal(plan_dir, journal)
        progress.item(step["title"], step["phase"], ok=code == 0)
        if code != 0:
            fail(f"[{step['phase']}] {step['title']}: exit {code}")
            if args.stop_on_failure:
                break

    progress.finish("Plan applied" if not failed_now(journal) else "Plan stopped with failures",
                    f"{len(todo)} step(s) run"
                    + (f" \u00b7 {len(skipped)} already done" if skipped else ""))
    done = [k for k, v in journal["steps"].items() if v.get("state") == "succeeded"]
    failed = [(k, v) for k, v in journal["steps"].items() if v.get("state") == "failed"]
    pending = [s["id"] for s in steps
               if journal["steps"].get(s["id"], {}).get("state") not in {"succeeded", "failed"}]
    journal["finishedAt"] = iso_now()
    save_journal(plan_dir, journal)

    report = {
        "ok": not failed,
        "plan": str(plan_dir),
        "journal": str(plan_dir / JOURNAL_NAME),
        "run": journal["runs"],
        "dryRun": bool(args.dry_run),
        "ranNow": len(todo),
        "skippedAlreadyDone": len(skipped),
        "succeeded": len(done),
        "failed": [{"id": k, "title": v.get("title"), "exit": v.get("exit"),
                    "output": (v.get("output") or "")[-300:]} for k, v in failed],
        "stillPending": pending,
    }
    emit(report, args)
    if failed:
        for k, v in failed[:6]:
            note(f"{v.get('title')}: exit {v.get('exit')}", "red")
        note(f"resume with: imprint apply {plan_dir} --resume", "amber")
    return 0 if not failed else 1



# ---------------------------------------------------------------- picker ----
# gum choose cannot do either half of what this needs: gum 2.x leaves space
# unbound, and it has no notion of a submenu. So the picker is ours.

MARK_ON, MARK_OFF, MARK_PART = "\u25c9", "\u25cb", "\u25d0"   # ◉ ○ ◐
MARK_SUB = "\u25b8"                                           # ▸


class SelectAll:
    """The first row of the top menu. Reflects, and drives, everything below it."""

    key = "__all__"
    label = "Select ALL for backup"
    hint = "everything, including the host-bound and secret categories"
    children: list = []
    parent = None
    is_branch = False

    def __init__(self, siblings):
        self._siblings = siblings

    def state(self) -> str:
        states = {n.state() for n in self._siblings}
        if states == {"on"}:
            return "on"
        if states == {"off"}:
            return "off"
        return "partial"

    def set_all(self, value: bool) -> None:
        for node in self._siblings:
            node.set_all(value)

    @property
    def selected(self) -> bool:
        return self.state() == "on"

    @selected.setter
    def selected(self, value) -> None:
        self.set_all(bool(value))

    def chosen_leaves(self) -> list[str]:
        return []


class ActionRow:
    """The row that actually starts the job. Nothing else confirms, so no
    keystroke on a selection row can kick the run off by accident."""

    key = "__go__"
    children: list = []
    parent = None
    is_branch = False

    def __init__(self, label, siblings):
        self.label = label
        self._siblings = siblings

    @property
    def hint(self) -> str:
        n = sum(1 for x in self._siblings if x.state() != "off")
        return f"{n} categor{'y' if n == 1 else 'ies'} selected" if n else "nothing selected yet"

    def state(self) -> str:
        return "off"

    def set_all(self, value: bool) -> None:
        pass

    selected = False

    def chosen_leaves(self) -> list[str]:
        return []


class Node:
    __slots__ = ("key", "label", "hint", "children", "selected", "parent")

    def __init__(self, key, label, hint="", children=None, selected=False):
        self.key = key
        self.label = label
        self.hint = hint
        self.children = children or []
        self.selected = selected
        self.parent = None
        for child in self.children:
            child.parent = self

    @property
    def is_branch(self) -> bool:
        return bool(self.children)

    def state(self) -> str:
        """on / off / partial -- partial only ever applies to a branch."""
        if not self.is_branch:
            return "on" if self.selected else "off"
        states = [c.state() for c in self.children]
        if all(x == "on" for x in states):
            return "on"
        if all(x == "off" for x in states):
            return "off"
        return "partial"

    def set_all(self, value: bool) -> None:
        if self.is_branch:
            for child in self.children:
                child.set_all(value)
        else:
            self.selected = value

    def chosen_leaves(self) -> list[str]:
        if not self.is_branch:
            return [self.key] if self.selected else []
        out = []
        for child in self.children:
            out.extend(child.chosen_leaves())
        return out


def plugin_children(home: Path) -> list[Node]:
    root = home / ".config/omarchy/plugins"
    if not root.is_dir():
        return []
    listing = {i.get("id"): i for i in plugin_list() if i.get("id")}
    out = []
    for d in sorted(root.iterdir()):
        if not (d.is_dir() or d.is_symlink()) or is_skipped_name(d.name):
            continue
        info = listing.get(d.name) or {}
        hint = "git" if (d / ".git").exists() else "local"
        if info.get("enabled"):
            hint += ", enabled"
        out.append(Node(d.name, d.name, hint, selected=True))
    return out


def project_children(home: Path) -> list[Node]:
    out = []
    for root_name in PROJECT_ROOTS:
        root = home / root_name
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not (d / ".git").exists() or is_skipped_name(d.name):
                continue
            rel = rel_under_home(d, home)
            hint = "no remote" if not git_remote(d) else ("dirty" if git_dirty(d) else "")
            out.append(Node(rel, rel, hint, selected=True))
    return out


def package_children() -> list[Node]:
    data = extra_packages()
    out = [Node(f"repo:{n}", n, "repo", selected=True) for n in data.get("repo") or []]
    out += [Node(f"aur:{n}", n, "aur", selected=True) for n in data.get("aur") or []]
    return out


def theme_children(home: Path) -> list[Node]:
    """Every theme on the machine. Stock ones come back with Omarchy, so they
    are listed but off -- previously they were not listed at all, which made it
    look like themes were missing."""
    out, seen = [], set()
    user_root = home / ".config/omarchy/themes"
    if user_root.is_dir():
        for d in sorted(user_root.iterdir()):
            if not d.is_dir() or is_skipped_name(d.name):
                continue
            seen.add(d.name)
            out.append(Node(d.name, d.name, "git" if git_remote(d) else "yours", selected=True))
    stock_root = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "themes"
    if stock_root.is_dir():
        for d in sorted(stock_root.iterdir()):
            if not d.is_dir() or d.name in seen:
                continue
            out.append(Node(d.name, d.name, "stock, ships with Omarchy", selected=False))
    return out


def script_children(home: Path) -> list[Node]:
    out = []
    for folder in (home / "bin", home / ".local/bin"):
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if is_skipped_name(f.name) or not (f.is_file() or f.is_symlink()):
                continue
            rel = rel_under_home(f, home)
            out.append(Node(rel, rel, "link" if f.is_symlink() else "", selected=True))
    return out


def archive_children(root: Path, cid: str) -> list[Node]:
    """Submenu contents for a restore, read from the archive rather than this
    machine. The per-category meta.json is complete; the manifest is truncated."""
    meta_path = root / "categories" / cid / "meta.json"
    if not meta_path.is_file():
        return []
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[Node] = []
    if cid == "plugins":
        for plug in meta.get("plugins") or []:
            pid = plug.get("id")
            if not pid:
                continue
            bits = [plug.get("kind") or ""]
            if plug.get("enabled"):
                bits.append("enabled")
            if plug.get("overlayFiles"):
                bits.append(f"{len(plug['overlayFiles'])} local edits")
            out.append(Node(pid, pid, ", ".join(b for b in bits if b), selected=True))
    elif cid == "projects":
        for repo in meta.get("repos") or []:
            path = repo.get("path")
            if not path:
                continue
            hint = "no remote" if not repo.get("url") else ("+patch" if repo.get("patch") else "")
            out.append(Node(path, path, hint, selected=True))
    elif cid == "packages":
        out += [Node(f"repo:{n}", n, "repo", selected=True) for n in meta.get("repo") or []]
        out += [Node(f"aur:{n}", n, "aur", selected=True) for n in meta.get("aur") or []]
    elif cid == "themes":
        for theme in meta.get("themes") or []:
            tid = theme.get("id")
            if tid:
                out.append(Node(tid, tid, theme.get("kind") or "", selected=True))
    elif cid == "scripts":
        files_root = root / "categories/scripts/files"
        for f in sorted(iter_files(files_root)) if files_root.is_dir() else []:
            rel = str(f.relative_to(files_root))
            out.append(Node(rel, rel, "", selected=True))
        for link in meta.get("links") or []:
            rel = link.get("link")
            if rel:
                out.append(Node(rel, rel, "link", selected=True))
    return out


SUBMENU_BUILDERS = {
    "plugins": lambda home: plugin_children(home),
    "projects": lambda home: project_children(home),
    "packages": lambda home: package_children(),
    "themes": lambda home: theme_children(home),
    "scripts": lambda home: script_children(home),
}


def build_tree(home: Path, present: set | None, defaults_on: bool = True,
               archive_root: Path | None = None) -> list[Node]:
    nodes = []
    for item in CATEGORIES:
        cid = item["id"]
        if present is not None and cid not in present:
            continue
        # Restoring: default to what the archive carries, since a category is
        # only listed at all when it is in there.
        on = True if archive_root is not None else bool(item.get("default"))
        if not defaults_on:
            on = False
        risk = item.get("risk") or "portable"
        tag = {"host": "this machine", "identity": "hostname", "secrets": "keys"}.get(risk, "")
        children = []
        if archive_root is not None:
            try:
                children = archive_children(archive_root, cid)
            except Exception:
                children = []
        elif cid in SUBMENU_BUILDERS:
            try:
                children = SUBMENU_BUILDERS[cid](home)
            except Exception:
                children = []
        node = Node(cid, item["title"], item["summary"] + (f"  [{tag}]" if tag else ""),
                    children=children, selected=on)
        if children:
            node.set_all(on)
        nodes.append(node)
    return nodes


def _draw(stdscr, rows, cursor, top, trail, header, height, width):
    stdscr.erase()
    crumbs = " / ".join(["All"] + [n.label for n in trail])
    stdscr.addnstr(0, 0, header[:width - 1], width - 1, curses.A_BOLD)
    stdscr.addnstr(1, 0, crumbs[:width - 1], width - 1, curses.A_DIM)
    body = height - 4
    for i in range(body):
        idx = top + i
        if idx >= len(rows):
            break
        node = rows[idx]
        if isinstance(node, ActionRow):
            text = f"   \u25b6 {node.label}   {node.hint}"
            attr = (curses.A_REVERSE if idx == cursor else curses.A_BOLD)
            stdscr.addnstr(3 + i, 0, text[:width - 1].ljust(width - 1), width - 1, attr)
            continue
        st = node.state()
        mark = {"on": MARK_ON, "off": MARK_OFF, "partial": MARK_PART}[st]
        branch = node.is_branch
        count = f" ({len(node.children)})" if branch else ""
        if isinstance(node, SelectAll):
            count = ""
        # The arrow leads the label so a submenu reads as a submenu at a glance,
        # and the label is highlighted rather than left looking like a leaf.
        lead = f"{MARK_SUB} " if branch else "  "
        label = f"{node.label}{count}"
        text = f" {mark} {lead}{label}"
        pad = max(1, 40 - len(text))
        if node.hint:
            text += " " * pad + node.hint
        attr = curses.A_REVERSE if idx == cursor else curses.A_NORMAL
        if branch and idx != cursor:
            attr |= curses.A_BOLD
        stdscr.addnstr(3 + i, 0, text[:width - 1].ljust(width - 1), width - 1, attr)
        if branch and idx != cursor:
            # Tint just the arrow. Wrapped because has_colors() raises unless a
            # screen is up, and a narrow window makes chgat fail.
            try:
                if curses.has_colors():
                    stdscr.chgat(3 + i, 3, 1, curses.color_pair(1) | curses.A_BOLD)
            except (curses.error, AttributeError):
                pass
    hints = ("\u2192 opens \u25b8 or moves down \u00b7 \u2190 backs out or moves up \u00b7 "
             "space toggles \u00b7 t group \u00b7 a all \u00b7 n none \u00b7 q cancel")
    stdscr.addnstr(height - 1, 0, hints[:width - 1], width - 1, curses.A_DIM)
    stdscr.refresh()


def _pick_loop(stdscr, roots, header):
    curses.curs_set(0)
    try:
        curses.use_default_colors()
        curses.init_pair(1, theme_accent(), -1)
    except curses.error:
        pass
    stdscr.keypad(True)
    trail: list[Node] = []
    rows = roots
    cursor = top = 0
    while True:
        height, width = stdscr.getmaxyx()
        body = max(1, height - 4)
        cursor = max(0, min(cursor, len(rows) - 1))
        if cursor < top:
            top = cursor
        if cursor >= top + body:
            top = cursor - body + 1
        _draw(stdscr, rows, cursor, top, trail, header, height, width)
        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            return None
        if key == 27:
            key = decode_escape(stdscr)
        node = rows[cursor] if rows else None
        arrow = arrow_of(key) if key > 255 else ""
        if key in DOWN_KEYS or key == ord("j") or arrow == "down":
            cursor += 1
        elif key in UP_KEYS or key == ord("k") or arrow == "up":
            cursor -= 1
        elif key == curses.KEY_NPAGE:
            cursor += body
        elif key == curses.KEY_PPAGE:
            cursor -= body
        elif key == curses.KEY_HOME:
            cursor = 0
        elif key == curses.KEY_END:
            cursor = len(rows) - 1
        elif key in RIGHT_KEYS or key == ord("l") or arrow == "right":
            # → opens a submenu when the row has one, and otherwise moves down.
            # Both behaviours were asked for; a row with ▸ has an obvious
            # "go in here" affordance, a plain row has nothing to go into.
            if node is not None and node.is_branch:
                trail.append(node)
                rows, cursor, top = node.children, 0, 0
            else:
                cursor += 1
        elif key in LEFT_KEYS or key == ord("h") or arrow == "left":
            # Mirror of →: leave the submenu if we are in one, else move up.
            if trail:
                parent = trail.pop()
                rows = trail[-1].children if trail else roots
                cursor = rows.index(parent) if parent in rows else 0
                top = 0
            else:
                cursor -= 1
        elif key in (ord(" "), curses.KEY_ENTER, 10, 13):
            # Space and enter both mean "act on this row" and nothing else
            # confirms, so selecting everything cannot start the run.
            if node is None:
                continue
            if isinstance(node, ActionRow):
                return roots
            if node.is_branch:
                trail.append(node)
                rows, cursor, top = node.children, 0, 0
            else:
                node.selected = not node.selected
        elif key in (27, curses.KEY_BACKSPACE, 127, 8):
            if trail:
                parent = trail.pop()
                rows = trail[-1].children if trail else roots
                cursor = rows.index(parent) if parent in rows else 0
                top = 0
        elif key == ord("t"):
            if node is not None:
                node.set_all(node.state() != "on")
        elif key == ord("a"):
            for r in rows:
                r.set_all(True)
        elif key == ord("n"):
            for r in rows:
                r.set_all(False)
        elif key in (ord("q"),):
            return None



# Terminals send an arrow as several bytes. nodelay() returns -1 before the rest
# of them have arrived, so the whole sequence read as a bare Escape and arrows
# did nothing at all. Wait briefly for the remainder instead.
CSI_FINAL = {"A": "KEY_UP", "B": "KEY_DOWN", "C": "KEY_RIGHT", "D": "KEY_LEFT",
             "H": "KEY_HOME", "F": "KEY_END"}
CSI_TILDE = {"1": "KEY_HOME", "7": "KEY_HOME", "4": "KEY_END", "8": "KEY_END",
             "5": "KEY_PPAGE", "6": "KEY_NPAGE"}


def _keys(*names) -> tuple:
    """Curses keycodes that exist on this build, by name."""
    return tuple(getattr(curses, n) for n in names if hasattr(curses, n))


# ncurses hands back a distinct code for a modified arrow (ctrl/shift), which
# would otherwise fall through unhandled and look like a dead key.
DOWN_KEYS = _keys("KEY_DOWN", "KEY_SF", "KEY_SNEXT")
UP_KEYS = _keys("KEY_UP", "KEY_SR", "KEY_SPREVIOUS")
RIGHT_KEYS = _keys("KEY_RIGHT", "KEY_SRIGHT")
LEFT_KEYS = _keys("KEY_LEFT", "KEY_SLEFT")


def arrow_of(key: int) -> str:
    """Direction for any arrow variant.

    The named constants are resolved from the tables directly, so this works
    with no screen up. keyname() is only the fallback, for the extended
    ctrl/alt codes ncurses invents from terminfo, which have no constant --
    and it needs an initialised screen, hence the guard.
    """
    if key in DOWN_KEYS:
        return "down"
    if key in UP_KEYS:
        return "up"
    if key in RIGHT_KEYS:
        return "right"
    if key in LEFT_KEYS:
        return "left"
    try:
        name = curses.keyname(key).decode("ascii", "ignore").upper()
    except (ValueError, curses.error):
        return ""
    for token, direction in (("RIT", "right"), ("RIGHT", "right"),
                             ("LFT", "left"), ("LEFT", "left"),
                             ("DN", "down"), ("DOWN", "down"), ("UP", "up")):
        if token in name:
            return direction
    return ""


def decode_escape(stdscr) -> int:
    """Map an escape sequence to a curses key, or 27 for a real lone Escape."""
    stdscr.timeout(90)
    try:
        first = stdscr.getch()
        if first == -1:
            return 27
        intro = chr(first)
        if intro not in ("[", "O"):
            return 27
        seq = ""
        for _ in range(16):
            nxt = stdscr.getch()
            if nxt == -1:
                break
            ch = chr(nxt)
            seq += ch
            if "@" <= ch <= "~":
                break
    finally:
        stdscr.timeout(-1)
    if not seq:
        return 27
    final, params = seq[-1], seq[:-1]
    if final in CSI_FINAL:                       # \x1b[B, \x1bOB, \x1b[1;5B
        return getattr(curses, CSI_FINAL[final])
    if final == "~":                             # \x1b[5~
        name = CSI_TILDE.get(params.split(";")[0])
        if name:
            return getattr(curses, name)
    if final == "u":                             # kitty: \x1b[13;1u
        head = params.split(";")[0]
        if head.isdigit():
            return int(head)
    return 27


def _menu_loop(stdscr, rows, header):
    curses.curs_set(0)
    stdscr.keypad(True)
    try:
        curses.use_default_colors()
        curses.init_pair(1, theme_accent(), -1)
    except curses.error:
        pass
    cursor = 0
    while True:
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        stdscr.addnstr(0, 0, header[:width - 1], width - 1, curses.A_BOLD)
        for i, label in enumerate(rows):
            if 2 + i >= height - 1:
                break
            mark = "\u25b8 " if i == cursor else "  "
            attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
            stdscr.addnstr(2 + i, 0, f" {mark}{label}"[:width - 1].ljust(width - 1), width - 1, attr)
        stdscr.addnstr(height - 1, 0,
                       "\u2192/\u2190 or \u2191/\u2193 move \u00b7 enter choose \u00b7 q cancel"[:width - 1],
                       width - 1, curses.A_DIM)
        stdscr.refresh()
        key = stdscr.getch()
        if key == 27:
            key = decode_escape(stdscr)
            if key == 27:
                return None
        arrow = arrow_of(key) if key > 255 else ""
        if key in DOWN_KEYS or key in RIGHT_KEYS or key in (ord("j"), ord("l")) \
                or arrow in ("down", "right"):
            cursor = (cursor + 1) % len(rows)
        elif key in UP_KEYS or key in LEFT_KEYS or key in (ord("k"), ord("h")) \
                or arrow in ("up", "left"):
            cursor = (cursor - 1) % len(rows)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return cursor
        elif key == ord("q"):
            return None


def with_tty_screen(func, *rest):
    """Run a curses UI on /dev/tty, leaving stdin and stdout for data.

    Both ends matter: stdout carries the answer back to the caller, and stdin
    may be a pipe feeding the menu its items -- curses must not read keys from
    either of them.
    """
    saved_out, saved_in = os.dup(1), os.dup(0)
    tty_fd = os.open("/dev/tty", os.O_RDWR)
    try:
        os.dup2(tty_fd, 1)
        os.dup2(tty_fd, 0)
        return curses.wrapper(func, *rest)
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_in, 0)
        os.close(saved_out)
        os.close(saved_in)
        os.close(tty_fd)


def cmd_menu(args) -> int:
    rows = [line for line in sys.stdin.read().splitlines() if line.strip()]
    if not rows:
        raise SystemExit("no menu items given")
    try:
        open("/dev/tty").close()
    except OSError:
        raise SystemExit("the menu needs a terminal")
    chosen = with_tty_screen(_menu_loop, rows, args.header or "Choose")
    if chosen is None:
        return 1
    sys.stdout.write(rows[chosen] + "\n")
    return 0


def cmd_palette(_args) -> int:
    """Shell-evaluable colours from the active theme, so the wrapper matches."""
    out = []
    for key in ("accent", "green", "amber", "red", "grey", "blue"):
        code = FG.get(key, "")
        out.append(f"IMP_{key.upper()}=$'{code}'" if code else f"IMP_{key.upper()}=''")
    out.append("IMP_RESET=$'\\033[0m'")
    out.append("IMP_BOLD=$'\\033[1m'")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


def cmd_pick(args) -> int:
    # The result goes to stdout so callers can capture it with $(...). curses
    # therefore has to draw somewhere else: /dev/tty, like fzf does.
    try:
        tty = open("/dev/tty", "r+b", buffering=0)
    except OSError:
        raise SystemExit("the picker needs a terminal")
    tty.close()
    home = Path.home()
    present = None
    if args.archive:
        with tempfile.TemporaryDirectory(prefix="imprint-pick-") as tmp:
            root = open_imprint(need_archive(args.archive), Path(tmp) / "open")
            present = set((load_manifest(root).get("categories") or {}).keys())
            categories = build_tree(home, present, archive_root=root)
    else:
        categories = build_tree(home, present)
    roots = [SelectAll(categories)] + categories + [ActionRow(args.action or "Start backup", categories)]
    header = args.header or "What should this imprint carry?"
    result = with_tty_screen(_pick_loop, roots, header)
    if result is None:
        return 1
    chosen, subs = [], {}
    for node in categories:
        if node.state() == "off":
            continue
        chosen.append(node.key)
        if node.is_branch and node.state() == "partial":
            subs[node.key] = node.chosen_leaves()
    json.dump({"categories": chosen, "subselections": subs}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_verify(args) -> int:
    archive = need_archive(args.archive)
    with tempfile.TemporaryDirectory(prefix="imprint-verify-") as tmp:
        try:
            root = open_imprint(archive, Path(tmp) / "open")
            manifest = load_manifest(root)
        except SystemExit as exc:
            json.dump({"ok": False, "problems": [str(exc)]}, sys.stdout)
            sys.stdout.write("\n")
            return 1
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
    archive = need_archive(args.archive)
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
    home = strict_target(Path(getattr(args, "target_home", "") or Path.home()))
    if args.undo_dir:
        chosen = strict_target(need_dir(args.undo_dir, "cannot read the undo"))
    else:
        root = strict_target(home / ".local/state/imprint")
        undos = sorted(root.glob("undo-*")) if root.is_dir() else []
        if not undos:
            raise SystemExit("no imprint undo history")
        chosen = strict_target(undos[-1])
    if (chosen / "recovery.json").is_file():
        journal = json.loads((chosen / "recovery.json").read_text())
        if any(r.get("scope") == "system" for r in journal["files"]) and not getattr(args, "allow_system", False):
            raise SystemExit("system undo requires --allow-system")
        return undo_file_recovery(chosen, home, strict_target(Path(getattr(args, "system_root", "/"))))
    if (chosen / ".config/omarchy/shell.json").exists():
        raise SystemExit("undo contains shell.json: recover individual settings via fresh live config-edit, never a stale whole snapshot")
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
    parser.add_argument("-V", "--version", action="version",
                        version=f"imprint {VERSION}")
    sub = parser.add_subparsers(
        dest="cmd", required=True,
        metavar="{save,restore,preview,plan,apply,info,diff,verify,undo,"
                "categories,facts,about,default-output}")
    sub.add_parser("categories")
    sub.add_parser("facts")
    sub.add_parser("about")
    # What the save prompt fills in for you: the remembered directory and a
    # name stamped with the host and the minute.
    sub.add_parser("default-output")
    save = sub.add_parser("save")
    save.add_argument("--json", action="store_true", help="print the machine-readable report")
    save.add_argument("--only", default="")
    save.add_argument("--include-file", action="append", default=[],
                      help="exact home-relative helper script, requires scripts; repeatable")
    save.add_argument("--system-root", default="/", help="source root for the system category")
    save.add_argument("--all", action="store_true")
    save.add_argument("-o", "--output", default="")
    save.add_argument("--select", default="",
                      help="JSON from `imprint pick`, to narrow within a category")
    restore = sub.add_parser("restore")
    restore.add_argument("--json", action="store_true", help="print the machine-readable report")
    restore.add_argument("archive")
    restore.add_argument("--only", default="")
    restore.add_argument("--select", default="",
                         help="JSON from `imprint pick`, to narrow within a category")
    restore.add_argument("--dry-run", action="store_true")
    restore.add_argument("--shell-key", action="append", default=[], help="archived scalar JSON pointer to merge into fresh live shell config")
    restore.add_argument("--confirm-hostname", default="")
    restore.add_argument("--system-root", default="/",
                         help="apply the system layer into this root instead of / (for verifying)")
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
    planp = sub.add_parser("plan")
    planp.add_argument("archive")
    planp.add_argument("--only", default="")
    planp.add_argument("-o", "--output", default="")
    applyp = sub.add_parser("apply")
    applyp.add_argument("--json", action="store_true", help="print the machine-readable report")
    applyp.add_argument("plan")
    applyp.add_argument("--resume", action="store_true",
                        help="continue an interrupted run, skipping steps already done")
    applyp.add_argument("--restart", action="store_true", help="discard the journal and start over")
    applyp.add_argument("--recheck", action="store_true", help="re-run steps already recorded as done")
    applyp.add_argument("--dry-run", action="store_true")
    applyp.add_argument("--phase", default="", help="only run this phase")
    applyp.add_argument("--stop-on-failure", action="store_true",
                        help="halt at the first failing step instead of carrying on")
    menup = sub.add_parser("menu")
    menup.add_argument("--header", default="")
    sub.add_parser("palette")
    pick = sub.add_parser("pick")
    pick.add_argument("--archive", default="")
    pick.add_argument("--header", default="")
    pick.add_argument("--action", default="", help="label for the row that starts the job")
    prev = sub.add_parser("preview")
    prev.add_argument("archive")
    prev.add_argument("--only", default="")
    prev.add_argument("--select", default="")
    prev.add_argument("--json", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("archive")
    diff = sub.add_parser("diff")
    diff.add_argument("archive")
    undo = sub.add_parser("undo")
    undo.add_argument("--undo-dir", default="")
    undo.add_argument("--target-home", default="")
    undo.add_argument("--system-root", default="/")
    undo.add_argument("--allow-system", action="store_true")
    prev.add_argument("--system-root", default="/")
    prev.add_argument("--allow-system", action="store_true")
    for command in (restore, prev):
        command.add_argument("--files-only", action="store_true",
                             help="recover category payload only; no recipes or activation")
        command.add_argument("--target-home", default="", help="existing explicit destination home")
        command.add_argument("--file", action="append", default=[], help="exact relative payload path; repeatable")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "categories": cmd_categories,
        "facts": cmd_facts,
        "default-output": cmd_default_output,
        "about": cmd_about,
        "save": cmd_save,
        "restore": cmd_restore,
        "info": cmd_info,
        "brief": cmd_brief,
        "plan": cmd_plan,
        "apply": cmd_apply,
        "pick": cmd_pick,
        "menu": cmd_menu,
        "palette": cmd_palette,
        "preview": cmd_preview,
        "verify": cmd_verify,
        "diff": cmd_diff,
        "undo": cmd_undo,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\ncancelled\n")
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
    except OSError as exc:
        # Anything the operating system refuses -- a full disk, a mount that
        # went away, a directory nobody may write to -- reads as a sentence,
        # not as a traceback.
        where = f": {exc.filename}" if exc.filename else ""
        sys.stderr.write(f"imprint: {exc.strerror or exc}{where}\n")
        sys.exit(1)
