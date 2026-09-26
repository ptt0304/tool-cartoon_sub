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


def _runtime_audio_mapping(project_path, result_path):
    """Verify frozen speaker mappings through one real Local_TTS generation request."""
    from threading import Event

    from cartoon_sub.app.controller import Controller
    from cartoon_sub.media.process import CancelledError
    from cartoon_sub.tts.generation_service import LocalTTSGenerationService
    from cartoon_sub.tts.local_tts_client import LocalTTSClient

    result_file = Path(result_path)
    controller = Controller()
    result = {"build": BUILD_MARKER, "project": str(Path(project_path).resolve())}
    client = None
    try:
        controller.accept(controller.load(project_path))
        settings = controller.settings_store.load_local_tts()
        client = LocalTTSClient(settings, request_timeout_seconds=180)
        library = client.voice_library()
        registry = {
            voice.get("voice_id"): voice for voice in library["voices"]
            if isinstance(voice.get("voice_id"), str)
        }
        speakers = LocalTTSGenerationService(client)._preflight(
            controller.project, controller.directory,
        )
        result["speakers"] = {
            speaker_id: {
                "voice_id": speaker.tts_voice_id,
                "display_name": registry[speaker.tts_voice_id].get("display_name"),
                "engine": registry[speaker.tts_voice_id].get("engine"),
                "status": registry[speaker.tts_voice_id].get("status"),
            }
            for speaker_id, speaker in sorted(speakers.items())
        }

        cancel = Event()
        generated_call = {}

        class OneGenerationClient:
            settings = client.settings

            def health(self):
                return client.health()

            def list_voices(self):
                return client.list_voices()

            def generate(self, segment_id, speaker_id, voice_id, text, speed):
                response = client.generate(segment_id, speaker_id, voice_id, text, speed)
                generated_call.update(
                    segment_id=segment_id, speaker_id=speaker_id, voice_id=voice_id,
                )
                cancel.set()
                return response

        try:
            LocalTTSGenerationService(
                OneGenerationClient(), controller.manager, settings.base_url,
            ).generate(controller.project, controller.directory, cancel=cancel)
        except CancelledError:
            pass
        result.update(status="completed", generated_call=generated_call)
        exit_code = 0
    except Exception as exc:
        result.update(status="failed", error=str(exc), exception=type(exc).__name__,
                      traceback=traceback.format_exc())
        logging.getLogger(__name__).exception("[RUNTIME AUDIO] failed")
        exit_code = 2
    finally:
        if client is not None:
            client.close()
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
    if "--runtime-audio-project" in sys.argv:
        index = sys.argv.index("--runtime-audio-project")
        try:
            project_path = sys.argv[index + 1]
            result_index = sys.argv.index("--runtime-result")
            result_path = sys.argv[result_index + 1]
        except (ValueError, IndexError):
            raise SystemExit("Cần --runtime-audio-project <project.json> --runtime-result <result.json>")
        return _runtime_audio_mapping(project_path, result_path)
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
