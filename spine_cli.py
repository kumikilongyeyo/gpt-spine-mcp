"""spine_cli — thin wrappers around the licensed Spine 4.3 command-line tool.

The Spine editor ships a headless CLI that (on an activated license) can pack
atlases, import a runtime skeleton json into an editable .spine project, export a
project back to runtime, and print project info. We drive it via subprocess.
"""
from __future__ import annotations
import glob
import ntpath
import os
import shutil
import subprocess
import sys


def spine_candidates(platform_name: str | None = None,
                     environ: dict[str, str] | None = None) -> list[str]:
    """Return platform-appropriate Spine CLI locations in priority order."""
    platform_name = platform_name or sys.platform
    environ = environ or os.environ
    configured = environ.get("SPINE_BIN")
    if configured:
        return [os.path.abspath(os.path.expanduser(configured))]
    if platform_name == "win32":
        roots = [environ.get("ProgramFiles"), environ.get("ProgramFiles(x86)"),
                 environ.get("LOCALAPPDATA")]
        candidates = []
        for root in (value for value in roots if value):
            candidates.extend([
                ntpath.join(root, "Spine", "Spine.com"),
                ntpath.join(root, "Spine", "Spine.exe"),
                ntpath.join(root, "Esoteric Software", "Spine", "Spine.com"),
                ntpath.join(root, "Esoteric Software", "Spine", "Spine.exe"),
            ])
        for command in ("Spine.com", "Spine.exe", "Spine"):
            found = shutil.which(command)
            if found:
                candidates.append(found)
        return candidates or [r"C:\Program Files\Spine\Spine.com"]
    if platform_name == "darwin":
        candidates = [
            "/Applications/Spine.app/Contents/MacOS/Spine",
            os.path.expanduser("~/Applications/Spine.app/Contents/MacOS/Spine"),
            os.path.expanduser("~/Applications/Setapp/Spine.app/Contents/MacOS/Spine"),
        ]
        candidates.extend(sorted(glob.glob("/Applications/Spine*.app/Contents/MacOS/Spine"), reverse=True))
        on_path = shutil.which("Spine") or shutil.which("spine")
        if on_path:
            candidates.append(on_path)
        return candidates
    candidates = [os.path.expanduser("~/Spine/Spine.sh"), "/opt/Spine/Spine.sh"]
    on_path = shutil.which("Spine") or shutil.which("spine") or shutil.which("Spine.sh")
    if on_path:
        candidates.append(on_path)
    return candidates


def detect_spine_bin() -> str:
    """Find the Spine executable without requiring shell configuration.

    ``SPINE_BIN`` always wins.  The remaining locations cover the standard
    macOS app bundle, versioned app bundles, Setapp, and a CLI on ``PATH``.
    Returning the conventional app path when nothing exists keeps doctor
    output actionable and preserves the old public ``SPINE_BIN`` behaviour.
    """
    candidates = spine_candidates()
    return next((path for path in candidates if os.path.isfile(path)), candidates[0])


SPINE_BIN = detect_spine_bin()
_NOISE = ("Spine Launcher", "Esoteric Software", "Mac OS X", "Starting:",
          "Spine 4.3", "Licensed to:")


def available() -> bool:
    return os.path.isfile(SPINE_BIN) and os.access(SPINE_BIN, os.X_OK)


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    if not available():
        raise FileNotFoundError(
            f"Spine CLI was not found at {SPINE_BIN!r}. Set SPINE_BIN to the "
            "Spine executable inside your licensed installation."
        )
    return subprocess.run([SPINE_BIN, *args], capture_output=True, text=True, timeout=timeout)


def _clean(out: str) -> str:
    return "\n".join(l for l in out.splitlines() if not any(l.startswith(p) for p in _NOISE)).strip()


def _output(result: subprocess.CompletedProcess) -> str:
    return _clean("\n".join(part for part in (result.stdout, result.stderr) if part))


def version() -> str:
    if not available():
        return "Spine CLI not found at " + SPINE_BIN
    out = _output(_run(["--version"]))
    for line in out.splitlines():
        if "Professional" in line or "Trial" in line or "Essential" in line:
            return line.strip().removeprefix("Starting:").strip()
    return out.strip().splitlines()[-1] if out.strip() else "n/a"


def info(project_or_data: str) -> str:
    """Print bones/slots/animations of a .spine project or skeleton .json."""
    return _output(_run(["-i", project_or_data]))


def pack_atlas(images_dir: str, out_dir: str, name: str) -> dict:
    """Pack a folder of PNGs into <name>.atlas + <name>.png using Spine's packer.

    Pages up to 4096: the 2048 default produced TWO pages for large units, and a
    game export that copies or renames only the first one 404s on the second —
    every Spine animation in the game silently degrading to a static sprite.

    Passed with --set rather than written to disk. The old version dropped a
    pack.json into the caller's own images folder and removed it in a finally,
    so a process killed in between left litter in a directory this tool does not
    own. The CLI takes these as flags, so there is no file to leave behind and
    an existing pack.json of the caller's is neither read nor overwritten."""
    os.makedirs(out_dir, exist_ok=True)
    r = _run(["-i", images_dir, "-o", out_dir, "-n", name, "-p", name,
              "--set", "maxWidth=4096", "--set", "maxHeight=4096", "--set", "pot=false"])
    atlas = os.path.join(out_dir, f"{name}.atlas")
    ok = os.path.exists(atlas)
    pages = 0
    if ok:
        with open(atlas) as f:
            pages = sum(1 for l in f if l.strip().endswith(".png"))
    return {"ok": ok and r.returncode == 0, "atlas": atlas, "pages": pages,
            "png": os.path.join(out_dir, f"{name}.png"), "log": _output(r),
            "returncode": r.returncode}


def make_project(runtime_json: str, out_spine: str) -> dict:
    """Import a runtime skeleton json into an EDITABLE .spine project (-r)."""
    os.makedirs(os.path.dirname(out_spine) or ".", exist_ok=True)
    r = _run(["-i", runtime_json, "-o", out_spine, "-r"])
    return {"ok": os.path.exists(out_spine) and r.returncode == 0,
            "project": out_spine, "log": _output(r), "returncode": r.returncode}


def export_project(project: str, out_dir: str, fmt: str = "json+pack") -> dict:
    """Export a .spine project back to runtime (json/binary [+pack])."""
    os.makedirs(out_dir, exist_ok=True)
    r = _run(["-i", project, "-o", out_dir, "-e", fmt])
    return {"ok": r.returncode == 0, "out_dir": out_dir, "log": _output(r),
            "returncode": r.returncode}
