"""Locations of the app's files, both when run from the source folder and when frozen by PyInstaller."""

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# Writable data (logs, seeds, the Spotify token cache) sits next to the exe, so the install folder holds everything
APP_ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parents[1]
# Read-only bundled files (assets, seed templates); PyInstaller unpacks them to sys._MEIPASS
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", APP_ROOT))
TOKEN_CACHE_PATH = APP_ROOT / ".cache"
# Spotify and Gemini credentials, shared by the GUI and the command-line tools
AUTH_PATH = Path.home() / ".spotipy"
