# PyInstaller spec for the playlist creator window: a one-folder, windowed build with the unused Qt parts removed.
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821  # SPECPATH is provided by PyInstaller

# Qt modules the window does not import; excluding them keeps their libraries and plugins out of the bundle
UNUSED_QT_MODULES = [
    "PySide6.QtNetwork",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtPdf",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtDBus",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtXml",
    "PySide6.QtPrintSupport",
    "PySide6.QtConcurrent",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtUiTools",
]
# Libraries and data that PyInstaller still collects for Qt but a Fusion-styled widget window never loads
UNUSED_QT_FILES = ("opengl32sw", "d3dcompiler", "Qt6Pdf", "Qt6Network", "Qt6Qml", "Qt6Quick", "Qt6OpenGL", "Qt6VirtualKeyboard")
# The window renders SVG in its style sheet and loads its .ico; both need the Qt image plugins kept below
UNUSED_QT_PLUGIN_DIRS = ("generic", "networkinformation", "tls", "iconengines", "styles")
KEPT_IMAGE_PLUGINS = ("qico", "qsvg")
UNUSED_PLATFORM_PLUGINS = ("qdirect2d", "qminimal", "qoffscreen")

a = Analysis(  # noqa: F821
    [str(ROOT / "modules" / "playlist_creator" / "gui.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "seeds" / "templates"), "seeds/templates"),
        *copy_metadata("spotify_tools"),  # The About dialog reads the version from the package metadata
    ],
    excludes=[*UNUSED_QT_MODULES, "tkinter"],
    hiddenimports=["PySide6.QtSvg"],  # Needed by the qsvg plugin that draws the combo box arrow
)


def is_unused(dest_name: str) -> bool:
    """True for Qt files that the window never loads: translations, unused libraries and unused plugins."""
    parts = Path(dest_name).parts
    if "translations" in parts:
        return True
    if "imageformats" in parts:
        return Path(dest_name).stem not in KEPT_IMAGE_PLUGINS
    if "plugins" in parts and any(part in UNUSED_QT_PLUGIN_DIRS for part in parts):
        return True
    if "platforms" in parts and Path(dest_name).stem.startswith(UNUSED_PLATFORM_PLUGINS):
        return True
    return Path(dest_name).name.startswith(UNUSED_QT_FILES)


a.binaries = [entry for entry in a.binaries if not is_unused(entry[0])]
a.datas = [entry for entry in a.datas if not is_unused(entry[0])]

pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="PlaylistCreator",
    console=False,
    icon=str(ROOT / "assets" / "icon.ico"),
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name="PlaylistCreator",
    upx=False,
)
