import sys
from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from cartoon_sub.app.config import configure_logging
from cartoon_sub.ui.main_window import MainWindow

def main():
    configure_logging()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PhamThanhTung.CartoonSub")
    application = QApplication(sys.argv)
    application.setApplicationName("Cartoon Sub")
    icon = QIcon(str(Path(__file__).resolve().parents[1] / "assets" / "cartoon_sub.png"))
    application.setWindowIcon(icon)
    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()
    return application.exec()

if __name__ == "__main__":
    raise SystemExit(main())
