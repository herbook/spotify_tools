"""Create a desktop shortcut that opens the playlist creator window without a console.

Run it with the Python environment the project is installed in; the shortcut points at that environment.
"""

import os
import subprocess
import sys
from pathlib import Path

from modules.paths import APP_ROOT

SHORTCUT_NAME = "Playlist creator.lnk"
ICON_PATH = APP_ROOT / "assets" / "icon.ico"
# Windows PowerShell resolves the real Desktop folder, including one redirected to OneDrive; values are passed through
# the environment so no quoting is needed
POWERSHELL_SCRIPT = """
$link = (New-Object -ComObject WScript.Shell).CreateShortcut(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) $env:SHORTCUT_NAME))
$link.TargetPath = $env:SHORTCUT_TARGET
$link.Arguments = '-m modules.playlist_creator.gui'
$link.WorkingDirectory = $env:SHORTCUT_WORKDIR
if ($env:SHORTCUT_ICON) { $link.IconLocation = $env:SHORTCUT_ICON }
$link.Description = 'Spotify playlist creator'
$link.Save()
$link.FullName
"""


def create_shortcut() -> str:
    """Create the desktop shortcut and return its path."""
    if sys.platform != "win32":
        msg = "Desktop shortcuts are only supported on Windows"
        raise SystemExit(msg)

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        msg = f"{pythonw} not found; run this with the project's Windows Python environment"
        raise SystemExit(msg)

    env = {
        **os.environ,
        "SHORTCUT_NAME": SHORTCUT_NAME,
        "SHORTCUT_TARGET": str(pythonw),
        "SHORTCUT_WORKDIR": str(APP_ROOT),
        "SHORTCUT_ICON": str(ICON_PATH) if ICON_PATH.exists() else "",
    }
    result = subprocess.run(  # noqa: S603
        ["powershell", "-NoProfile", "-Command", POWERSHELL_SCRIPT],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = f"Could not create the shortcut: {result.stderr.strip()}"
        raise SystemExit(msg)
    return result.stdout.strip()


if __name__ == "__main__":
    print(f"Shortcut created: {create_shortcut()}")
