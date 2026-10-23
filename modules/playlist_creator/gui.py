"""Desktop window for the playlist creator, built with PySide6.

Paste a Spotify playlist URL and import it into the seed text, or type the seed yourself, then press
'Create playlist'. API keys are set under 'Settings' and stored in ~/.spotipy, the same file the
command-line tools read. Start it from the project root with: python -m modules.playlist_creator.gui
"""

import ctypes
import sys
from collections.abc import Callable
from contextlib import nullcontext, redirect_stdout
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from threading import Event

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QIcon,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modules.paths import RESOURCE_ROOT, TOKEN_CACHE_PATH
from modules.playlist_creator.export_playlist_seed import export_seed
from modules.playlist_creator.seed import parse_seed
from modules.playlist_creator.suggest_playlist import (
    DEFAULT_MODEL,
    FREE_TIER_MODELS,
    Cancelled,
    list_models,
)
from modules.playlist_creator.suggest_playlist import run as create_playlist_from_seed
from modules.spotify_api import extract_playlist_id, load_auth, save_auth, spotify_session

AUTH_PATH = Path.home() / ".spotipy"
TEMPLATE_PATH = RESOURCE_ROOT / "seeds" / "templates" / "seed_template_gui.txt"
ICON_PATH = RESOURCE_ROOT / "assets" / "icon.ico"
ARROW_PATH = RESOURCE_ROOT / "assets" / "arrow_down.svg"
APP_ID = "spotify_tools.playlist_creator"  # Lets Windows group the window under its own taskbar icon
LOG_LINK = "log"  # Link target in the result label that opens the log view
TERMINAL_FONTS = ["Ubuntu Mono", "Consolas"]  # Ubuntu Mono is bundled; Consolas is the fallback
FONT_PATH = RESOURCE_ROOT / "assets" / "fonts" / "UbuntuMono-Regular.ttf"
TERMINAL_STYLE = (
    "QPlainTextEdit { background-color: #300a24; color: #8ae234; border: 1px solid #5e2750;"
    " border-radius: 8px; padding: 6px; selection-background-color: #e95420; selection-color: #ffffff; }"
)
ROUNDED_STYLE = """
QPushButton { background-color: #3c3f41; color: #e6e6e6; border: 1px solid #555753; border-radius: 8px;
    padding: 6px 16px; }
QPushButton:hover { background-color: #4a4d50; }
QPushButton:pressed { background-color: #2f3133; }
QPushButton:disabled { background-color: #2f3133; color: #808080; }
QLineEdit { background-color: #1e1e1e; color: #e6e6e6; border: 1px solid #555753; border-radius: 8px;
    padding: 5px 8px; selection-background-color: #2f65ca; }
QTextBrowser { border: 1px solid #555753; border-radius: 8px; padding: 6px; }
QComboBox { background-color: #3c3f41; color: #e6e6e6; border: 1px solid #555753; border-radius: 8px;
    padding: 5px 10px; }
QComboBox:disabled { background-color: #2f3133; color: #808080; }\nQComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background-color: #1e1e1e; color: #e6e6e6; border: 1px solid #555753;
    selection-background-color: #2f65ca; }
"""
SEED_LABEL = "gui_seed.txt"  # Names the log file and the default playlist name, as a seed file would
README_URL = "https://github.com/herbook/spotify_tools"
PACKAGE_NAME = "spotify_tools"  # Distribution name in pyproject.toml, the source of the version shown in About
COPYRIGHT = "Copyright (c) 2026 Herman Böök"
LICENSE_NAME = "PolyForm Strict License 1.0.0"
SPOTIFY_KEYS = ("CLIENT_ID", "CLIENT_SECRET")
ALL_KEYS = (*SPOTIFY_KEYS, "GEMINI_API_KEY")
SECRET_KEYS = ("CLIENT_SECRET", "GEMINI_API_KEY")
DARK_COLORS = {
    QPalette.ColorRole.Window: "#2b2b2b",
    QPalette.ColorRole.WindowText: "#e6e6e6",
    QPalette.ColorRole.Base: "#1e1e1e",
    QPalette.ColorRole.AlternateBase: "#2b2b2b",
    QPalette.ColorRole.ToolTipBase: "#2b2b2b",
    QPalette.ColorRole.ToolTipText: "#e6e6e6",
    QPalette.ColorRole.Text: "#e6e6e6",
    QPalette.ColorRole.PlaceholderText: "#808080",
    QPalette.ColorRole.Button: "#3c3f41",
    QPalette.ColorRole.ButtonText: "#e6e6e6",
    QPalette.ColorRole.BrightText: "#ff5555",
    QPalette.ColorRole.Link: "#4da3ff",
    QPalette.ColorRole.Highlight: "#2f65ca",
    QPalette.ColorRole.HighlightedText: "#ffffff",
}
DISABLED_ROLES = (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText)


def apply_dark_theme(app: QApplication) -> None:
    """Use the Fusion style with a dark palette, so the window looks the same whatever the system theme."""
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in DARK_COLORS.items():
        palette.setColor(role, QColor(color))
    for role in DISABLED_ROLES:
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#808080"))
    app.setPalette(palette)
    app.setStyleSheet(
        ROUNDED_STYLE + f"QComboBox::down-arrow {{ image: url({ARROW_PATH.as_posix()}); width: 12px; height: 12px; }}"
    )


def load_icon() -> QIcon:
    """Load the window and taskbar icon from the .ico file, the same icon the exe and shortcuts use."""
    return QIcon(str(ICON_PATH))


def open_playlist_link(link: str) -> None:
    """Open a Spotify playlist link in the Spotify app if that works, else in the browser."""
    if "open.spotify.com/playlist/" in link:
        app_uri = QUrl(f"spotify:playlist:{extract_playlist_id(link=link)}")
        if QDesktopServices.openUrl(app_uri):
            return
    QDesktopServices.openUrl(QUrl(link))


def load_saved_auth() -> dict[str, str]:
    """Return the credentials saved in ~/.spotipy, or an empty dict when the file does not exist yet."""
    return load_auth(AUTH_PATH) if AUTH_PATH.exists() else {}


class OutputWriter:
    """File-like object that forwards everything written to it, so print() output reaches the window."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        """Store the function that receives the text."""
        self.emit = emit

    def write(self, text: str) -> int:
        """Forward the text and report it as fully written."""
        self.emit(text)
        return len(text)

    def flush(self) -> None:
        """Do nothing; text is forwarded immediately."""


class Worker(QThread):
    """Runs a slow task off the interface thread so the window stays responsive."""

    output = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, task: Callable[[], object], *, capture_output: bool = True) -> None:
        """Store the task to run; with capture_output, its print() output is forwarded to the window."""
        super().__init__()
        self.task = task
        self.capture_output = capture_output

    def run(self) -> None:
        """Run the task and report the result or the error message."""
        try:
            # redirect_stdout is process-wide, so only one worker at a time may capture output
            with redirect_stdout(OutputWriter(self.output.emit)) if self.capture_output else nullcontext():
                result = self.task()
        except SystemExit as e:  # The tools use SystemExit for user-facing errors such as a used-up quota
            self.failed.emit(str(e))
        except Cancelled:
            self.output.emit("\nStopped.\n")
        except Exception as e:  # noqa: BLE001  # Any error must reach the window, not vanish with the thread
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.succeeded.emit(result)


class AboutDialog(QDialog):
    """Shows the app name, version, copyright, license and the repository link."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the dialog; the version is read from the installed package metadata."""
        super().__init__(parent)
        self.setWindowTitle("About Playlist creator")

        info = QLabel(
            f"<b>Playlist creator</b> {version(PACKAGE_NAME)}<br>"
            f"{COPYRIGHT}<br>"
            f"License: {LICENSE_NAME}<br>"
            f'<a href="{README_URL}">GitHub</a>'
        )
        info.setOpenExternalLinks(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(buttons)


class SettingsDialog(QDialog):
    """Form for the Spotify and Gemini credentials."""

    def __init__(self, auth: dict[str, str], parent: QWidget | None = None) -> None:
        """Build the form, filled with the saved credentials."""
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.auth = auth
        self.fields = {}

        form = QFormLayout()
        for key in ALL_KEYS:
            field = QLineEdit(auth.get(key, ""))
            if key in SECRET_KEYS:
                field.setEchoMode(QLineEdit.EchoMode.Password)
            self.fields[key] = field
            form.addRow(key, field)

        hint = QLabel(
            f"Saved in {AUTH_PATH}<br>"
            f'How to get the keys: <a href="{README_URL}#spotify-developer-dashboard">Spotify</a> | '
            f'<a href="{README_URL}#gemini-playlist-creator-only">Gemini</a>'
        )
        hint.setWordWrap(True)
        hint.setOpenExternalLinks(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        about = QLabel('<a href="about">About</a>')
        about.linkActivated.connect(lambda _link: AboutDialog(parent=self).exec())
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(about)
        bottom_row.addStretch()
        bottom_row.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addLayout(bottom_row)

    def save(self) -> None:
        """Write the credentials to ~/.spotipy, keeping any other keys already in the file."""
        self.auth = {**self.auth, **{key: field.text().strip() for key, field in self.fields.items()}}
        save_auth(AUTH_PATH, self.auth)
        self.accept()


class LogDialog(QDialog):
    """Shows a Markdown log file rendered, with its links clickable."""

    def __init__(self, log_file: Path, parent: QWidget | None = None) -> None:
        """Render the log file in a browser view."""
        super().__init__(parent)
        self.setWindowTitle(log_file.name)
        self.resize(1000, 700)

        browser = QTextBrowser()
        browser.setOpenLinks(False)
        browser.anchorClicked.connect(lambda url: open_playlist_link(link=url.toString()))
        browser.setMarkdown(log_file.read_text(encoding="utf-8"))

        layout = QVBoxLayout(self)
        layout.addWidget(browser)


class SpinningRecord(QWidget):
    """A small LP that spins while it is running and is hidden otherwise; clicking it emits `clicked`."""

    clicked = Signal()

    FRAME_MS = 33
    DEGREES_PER_FRAME = 8

    def __init__(self, size: int = 40) -> None:
        """Create the hidden record; it keeps its space in the layout so nothing moves when it appears."""
        super().__init__()
        self.setFixedSize(size, size)
        policy = self.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.setSizePolicy(policy)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.setInterval(self.FRAME_MS)
        self.timer.timeout.connect(self.advance)
        self.hide()

    def start(self) -> None:
        """Show the record and start it spinning."""
        self.timer.start()
        self.show()

    def stop(self) -> None:
        """Stop the record and hide it."""
        self.timer.stop()
        self.hide()

    def advance(self) -> None:
        """Turn the record one step."""
        self.angle = (self.angle + self.DEGREES_PER_FRAME) % 360
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Emit `clicked` on a left click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802
        """Draw the record on a 100 x 100 grid, turned by the current angle."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(self.width() / 100, self.height() / 100)
        painter.rotate(self.angle)
        center = QPointF(0, 0)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#111111"))
        painter.drawEllipse(center, 48, 48)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#2f2f2f"), 1))
        for radius in (41, 34, 27):
            painter.drawEllipse(center, radius, radius)
        # A sheen on one side and a dot on the label make the turning visible
        painter.setPen(QPen(QColor("#6a6a6a"), 2))
        painter.drawArc(QRectF(-44, -44, 88, 88), 30 * 16, 50 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e95420"))
        painter.drawEllipse(center, 17, 17)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPointF(10, 0), 2.5, 2.5)
        painter.setBrush(QColor("#2b2b2b"))
        painter.drawEllipse(center, 3, 3)


class MainWindow(QWidget):
    """Seed editor with an optional playlist import, a create button and a live output view."""

    def __init__(self) -> None:
        """Build the window and load the seed template into the seed editor."""
        super().__init__()
        self.setWindowTitle("Playlist creator")
        self.worker = None
        self.model_worker = None
        self.cancel = None
        self.log_file = None
        self.auth = load_saved_auth()

        self.build_widgets()
        self.build_layout()
        self.refresh_models()

    def build_widgets(self) -> None:
        """Create the controls and connect their actions."""
        terminal_font = QFont()
        terminal_font.setFamilies(TERMINAL_FONTS)
        terminal_font.setPointSize(11)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Spotify playlist URL (optional)")
        self.import_button = QPushButton("Import playlist")
        self.import_button.clicked.connect(self.import_playlist)

        seed_font = QFont(terminal_font)
        seed_font.setPointSizeF(terminal_font.pointSizeF() * 0.9)
        self.seed_edit = QPlainTextEdit(TEMPLATE_PATH.read_text(encoding="utf-8"))
        self.seed_edit.setFont(seed_font)
        self.seed_edit.setStyleSheet(TERMINAL_STYLE)

        self.create_button = QPushButton("Create playlist")
        self.create_button.clicked.connect(self.create_playlist)
        self.model_combo = QComboBox()
        self.model_combo.addItems(FREE_TIER_MODELS)
        self.model_combo.setToolTip("Gemini model")
        self.template_button = QPushButton("Reset playlist seed")
        self.template_button.clicked.connect(self.load_template)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.open_settings)

        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setFont(terminal_font)
        self.output_view.setStyleSheet(TERMINAL_STYLE)
        self.result_label = QLabel()
        self.result_label.linkActivated.connect(self.open_link)
        self.spinner = SpinningRecord()
        self.spinner.clicked.connect(self.stop_task)

    def build_layout(self) -> None:
        """Arrange the controls: URL and seed buttons, seed editor, action row, output view and spinner."""
        url_row = QHBoxLayout()
        url_row.addWidget(self.url_input, alignment=Qt.AlignmentFlag.AlignTop)
        seed_buttons = QVBoxLayout()
        seed_buttons.addWidget(self.import_button)
        seed_buttons.addWidget(self.template_button)
        url_row.addLayout(seed_buttons)
        action_row = QHBoxLayout()
        action_row.addWidget(self.create_button)
        action_row.addWidget(self.model_combo)
        action_row.addStretch()
        action_row.addWidget(self.settings_button)

        layout = QVBoxLayout(self)
        layout.addLayout(url_row)
        layout.addWidget(QLabel("Playlist seed"))
        layout.addWidget(self.seed_edit, stretch=3)
        layout.addLayout(action_row)
        layout.addWidget(self.output_view, stretch=2)
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.result_label, stretch=1)
        bottom_row.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        layout.addLayout(bottom_row)

    def refresh_models(self) -> None:
        """Load the available Gemini models into the dropdown when a Gemini key is set."""
        api_key = self.auth.get("GEMINI_API_KEY")
        if not api_key:
            return

        self.model_worker = Worker(lambda: list_models(api_key=api_key), capture_output=False)
        self.model_worker.succeeded.connect(self.set_models)
        self.model_worker.failed.connect(lambda message: self.append_output(f"Could not load the models: {message}\n"))
        self.model_worker.start()

    def set_models(self, models: list[str]) -> None:
        """Fill the dropdown with the models, keeping the current choice if it is still available."""
        if not models:
            return

        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        keep = current if current in models else DEFAULT_MODEL if DEFAULT_MODEL in models else models[0]
        self.model_combo.setCurrentText(keep)

    def open_link(self, link: str) -> None:
        """Open the rendered log for the log link; open a playlist link in the Spotify app if it opens, else the browser."""
        if link == LOG_LINK:
            LogDialog(log_file=self.log_file, parent=self).exec()
        else:
            open_playlist_link(link=link)

    def load_template(self) -> None:
        """Replace the seed text with the seed template."""
        self.seed_edit.setPlainText(TEMPLATE_PATH.read_text(encoding="utf-8"))

    def open_settings(self) -> None:
        """Show the settings dialog and keep the saved credentials."""
        dialog = SettingsDialog(auth=self.auth, parent=self)
        if dialog.exec():
            self.auth = dialog.auth
            self.refresh_models()

    def missing_keys(self, keys: tuple[str, ...]) -> bool:
        """Warn and return True when any of the keys is not set in the settings."""
        missing = [key for key in keys if not self.auth.get(key)]
        if missing:
            QMessageBox.warning(self, "Settings needed", f"Set {', '.join(missing)} under Settings first.")
        return bool(missing)

    def set_busy(self, *, busy: bool) -> None:
        """Disable the buttons while a task runs."""
        for button in (self.import_button, self.create_button, self.template_button, self.settings_button):
            button.setEnabled(not busy)
        self.model_combo.setEnabled(not busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def start(
        self, task: Callable[[], object], on_success: Callable[[object], None], cancel: Event | None = None
    ) -> None:
        """Run the task in a worker thread, showing its output and reporting errors in the output view.

        When `cancel` is given, clicking the spinning record sets it; the task must check it to stop.
        """
        self.result_label.clear()
        self.cancel = cancel
        self.spinner.setToolTip("Click to stop" if cancel else "")
        self.spinner.setCursor(Qt.CursorShape.PointingHandCursor if cancel else Qt.CursorShape.ArrowCursor)
        self.set_busy(busy=True)
        self.worker = Worker(task)
        self.worker.output.connect(self.append_output)
        self.worker.succeeded.connect(on_success)
        self.worker.failed.connect(lambda message: self.append_output(f"\nError: {message}\n"))
        self.worker.finished.connect(lambda: self.set_busy(busy=False))
        self.worker.start()

    def stop_task(self) -> None:
        """Ask the running task to stop; it ends at its next check."""
        if self.cancel and not self.cancel.is_set():
            self.cancel.set()
            self.append_output("\nStopping...\n")

    def append_output(self, text: str) -> None:
        """Append text to the output view; a carriage return rewrites the current line, as in a terminal."""
        cursor = self.output_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for index, part in enumerate(text.split("\r")):
            if index > 0:
                cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            cursor.insertText(part)
        self.output_view.setTextCursor(cursor)
        self.output_view.ensureCursorVisible()

    def import_playlist(self) -> None:
        """Fill the seed editor with the songs of the playlist in the URL box."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.information(self, "Import playlist", "Enter a Spotify playlist URL first.")
            return
        if self.missing_keys(SPOTIFY_KEYS):
            return

        auth = dict(self.auth)

        def task() -> tuple[str, str, int]:
            session = spotify_session(
                client_id=auth["CLIENT_ID"], client_secret=auth["CLIENT_SECRET"], cache_path=TOKEN_CACHE_PATH
            )
            return export_seed(session=session, playlist=url, start=1, end=None)

        def on_success(result: tuple[str, str, int]) -> None:
            name, seed_text, song_count = result
            self.seed_edit.setPlainText(seed_text)
            self.append_output(f"Imported {song_count} songs from '{name}'\n")

        self.start(task=task, on_success=on_success)

    def create_playlist(self) -> None:
        """Create the playlist from the seed text."""
        seed = parse_seed(text=self.seed_edit.toPlainText())
        if not seed.songs:
            QMessageBox.information(self, "Create playlist", "The seed has no songs.")
            return
        if self.missing_keys(ALL_KEYS):
            return

        auth = dict(self.auth)
        model = self.model_combo.currentText()
        # A 'name:' line in the seed wins; otherwise the time tells the playlists apart
        name = None if "name" in seed.settings else f"AI suggestions [{datetime.now().astimezone():%Y-%m-%d %H:%M}]"
        cancel = Event()

        def task() -> tuple[str | None, Path]:
            return create_playlist_from_seed(
                seed=seed,
                seed_label=SEED_LABEL,
                criteria=None,
                song_count=None,
                name=name,
                model=model,
                auth=auth,
                cancel=cancel,
            )

        def on_success(result: tuple[str | None, Path]) -> None:
            link, self.log_file = result
            playlist_part = f'<a href="{link}">Open the playlist in Spotify</a>' if link else "No playlist was created."
            self.result_label.setText(f'{playlist_part} | <a href="{LOG_LINK}">View the log</a>')

        self.start(task=task, on_success=on_success, cancel=cancel)


def main() -> None:
    """Open the window."""
    if sys.platform == "win32":
        # Without this, Windows shows the Python icon in the taskbar instead of the app icon
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    app = QApplication(sys.argv)
    app.setWindowIcon(load_icon())
    QFontDatabase.addApplicationFont(str(FONT_PATH))
    apply_dark_theme(app=app)
    window = MainWindow()
    window.resize(900, 800)
    window.layout().activate()
    seed_edit = window.seed_edit
    seed_needed = (
        seed_edit.fontMetrics().lineSpacing() * seed_edit.document().lineCount()
        + 2 * seed_edit.frameWidth()
        + 2 * seed_edit.document().documentMargin()
        + seed_edit.fontMetrics().lineSpacing()  # One spare line, so rounding never brings up a scrollbar
    )
    # The seed box gets 3 of the 5 stretch parts, so the window grows by 5/3 of the missing seed height
    extra_height = max(0, seed_needed - seed_edit.height()) * 5 / 3
    window_height = min(int(800 + extra_height), app.primaryScreen().availableGeometry().height())
    window.resize(900, window_height)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
