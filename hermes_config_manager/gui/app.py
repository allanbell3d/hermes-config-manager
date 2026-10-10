"""Start the application (double-click ``HermesConfigManager.pyw`` or ``python -m hermes_config_manager``).

Hosts are chosen in the app (Hosts screen); there are no command-line options.
"""

from __future__ import annotations

import sys
import time
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .. import __version__


def build(session=None):
    """Application state + main window (tests and benchmarks pass their own session)."""
    from ..services.session import Session
    from .main_window import MainWindow
    from .state import AppState
    from .theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    session = session or Session()
    state = AppState(session)
    apply_theme(app, state.settings.value("ui/theme", "light") == "dark")
    window = MainWindow(state)
    return app, state, window


def install_crash_handler() -> None:
    """An unexpected error is written (credentials masked) to errors.log and shown, never lost silently."""
    from ..core import appdirs, secrets

    busy = {"on": False}

    def hook(exc_type, exc, tb) -> None:
        if busy["on"]:
            return
        busy["on"] = True
        try:
            text = secrets.mask_text("".join(traceback.format_exception(exc_type, exc, tb)))
            path = appdirs.log_dir() / "errors.log"
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} · Hermes Config Manager v{__version__}\n{text}")
            except OSError:
                path = None
            if sys.__stderr__ is not None:
                print(text, file=sys.__stderr__)
            if QApplication.instance() is not None and QApplication.platformName() != "offscreen":
                from PySide6.QtWidgets import QMessageBox

                QMessageBox.critical(None, "Hermes Config Manager",
                                     f"Something went wrong: {secrets.mask_text(str(exc))}\n\n"
                                     + (f"Details were saved to {path}.\n" if path else "")
                                     + "Nothing was written because of this; staged changes are still there.")
        finally:
            busy["on"] = False

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    install_crash_handler()
    if sys.platform == "win32":
        try:                       # own taskbar entry and icon instead of Python's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HermesConfig.App")
        except (AttributeError, OSError):
            pass
    app = QApplication.instance() or QApplication([sys.argv[0]] + argv)
    app.setApplicationName("Hermes Config Manager")
    app.setApplicationVersion(__version__)
    from pathlib import Path

    from PySide6.QtGui import QIcon

    app.setWindowIcon(QIcon(str(Path(__file__).with_name("hermes.png"))))
    _app, state, window = build()
    window.show()
    QTimer.singleShot(0, state.start)
    QTimer.singleShot(200, window.first_run)
    code = app.exec()
    state.session.shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
