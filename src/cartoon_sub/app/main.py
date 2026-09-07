import sys
from PySide6.QtWidgets import QApplication
from cartoon_sub.app.config import configure_logging
from cartoon_sub.ui.main_window import MainWindow

def main():
    configure_logging()
    application = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return application.exec()

if __name__ == "__main__":
    raise SystemExit(main())
