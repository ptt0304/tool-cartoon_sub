import json
import logging
import sys
import traceback
from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from cartoon_sub.app.config import configure_logging
from cartoon_sub.ui.main_window import MainWindow

BUILD_MARKER = "transcript-timestamp-fix-v2"


def _runtime_transcribe(project_path, result_path):
    """Exercise the same controller action as the Transcript button from a frozen build."""
    from cartoon_sub.app.controller import Controller

    result_file = Path(result_path)
    controller = Controller()
    progress_messages = []

    def report(message):
        text = str(message)
        progress_messages.append(text)
        logging.getLogger(__name__).info("[RUNTIME TRANSCRIPT] %s", text)

    result = {"build": BUILD_MARKER, "project": str(Path(project_path).resolve())}
    try:
        controller.accept(controller.load(project_path))
        completed = controller.transcribe(progress=report)
        controller.accept(completed)
        result.update(status="completed", segments=len(controller.project.segments))
        exit_code = 0
    except Exception as exc:
        result.update(status="failed", error=str(exc), exception=type(exc).__name__,
                      traceback=traceback.format_exc())
        logging.getLogger(__name__).exception("[RUNTIME TRANSCRIPT] failed")
        exit_code = 2
    result["progress"] = progress_messages
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


def main():
    configure_logging()
    logging.getLogger(__name__).info("[BUILD] %s", BUILD_MARKER)
    if "--runtime-transcribe-project" in sys.argv:
        index = sys.argv.index("--runtime-transcribe-project")
        try:
            project_path = sys.argv[index + 1]
            result_index = sys.argv.index("--runtime-result")
            result_path = sys.argv[result_index + 1]
        except (ValueError, IndexError):
            raise SystemExit("Cần --runtime-transcribe-project <project.json> --runtime-result <result.json>")
        return _runtime_transcribe(project_path, result_path)
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
