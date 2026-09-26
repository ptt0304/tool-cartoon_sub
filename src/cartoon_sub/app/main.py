import json
import logging
import sys
import traceback
from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from cartoon_sub.app.config import configure_logging
from cartoon_sub.ui.main_window import MainWindow

BUILD_MARKER = "normalized-timeline-search-v1"


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


def _runtime_timeline_ui(project_path, result_path):
    """Exercise frozen canonical exports and the three searchable timeline widgets."""
    import hashlib
    import tempfile

    import pysubs2

    from cartoon_sub.project.project_manager import ProjectManager
    from cartoon_sub.subtitle.canonical_timeline import canonical_timeline
    from cartoon_sub.subtitle.parser import export_canonical_srt
    from cartoon_sub.subtitle.renderer import save_ass
    from cartoon_sub.ui.tabs import transcript_tab, translate_tab, subtitle_tab
    from cartoon_sub.ui.timeline_table import populate as populate_timeline

    result_file = Path(result_path)
    result = {"build": BUILD_MARKER, "project": str(Path(project_path).resolve())}
    try:
        application = QApplication.instance() or QApplication([])
        project = ProjectManager().load(Path(project_path).parent)
        manifest = Path(project_path).parent / "audio" / "tts" / "tts_cache.json"
        before_manifest = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None

        transcript = transcript_tab.build()
        transcript_tab.populate(transcript, project)
        translate = translate_tab.build()
        populate_timeline(translate.table, project)
        subtitle = subtitle_tab.build()
        subtitle.load_project(project)

        def table_row(table, utterance_id):
            return next(row for row in range(table.rowCount())
                        if table.item(row, 0) and int(table.item(row, 0).text()) == utterance_id)

        transcript_113 = table_row(transcript.table, 113)
        translate_113 = table_row(translate.table, 113)
        subtitle_113 = next(subtitle.tree.topLevelItem(row) for row in range(subtitle.tree.topLevelItemCount())
                            if subtitle.tree.topLevelItem(row).data(1, 256) == 113)

        transcript.search_edit.setText("伏底阵")
        transcript_visible = [int(transcript.table.item(row, 0).text()) for row in range(transcript.table.rowCount())
                              if not transcript.table.isRowHidden(row)]
        translate.search_edit.setText("phù đáy")
        translate_visible = [int(translate.table.item(row, 0).text()) for row in range(translate.table.rowCount())
                             if not translate.table.isRowHidden(row)]
        subtitle.search_edit.setText("phù đáy")
        subtitle_visible = [subtitle.tree.topLevelItem(row).data(1, 256)
                            for row in range(subtitle.tree.topLevelItemCount())
                            if not subtitle.tree.topLevelItem(row).isHidden()]

        entries = canonical_timeline(project.utterances)
        overlap = next(entry for entry in entries if entry.source_utterance_ids == (113, 114))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            languages = ("zh", "vi_translation", "vi_subtitle", "vi_dubbing")
            identities = []
            for language in languages:
                output = base / f"{language}.srt"
                export_canonical_srt(project.utterances, output, language)
                rows = pysubs2.load(str(output), encoding="utf-8-sig")
                identities.append([(index, row.start, row.end) for index, row in enumerate(rows, 1)])
            ass_path = save_ass(project, base / "render.ass")
            rendered = pysubs2.load(str(ass_path))
            overlap_ass = next(row.text for row in rendered if "phù đáy" in row.text)

        after_manifest = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None
        result.update(
            status="completed",
            timestamps={
                "transcript": [transcript.table.item(transcript_113, 1).text(), transcript.table.item(transcript_113, 2).text()],
                "translate": [translate.table.item(translate_113, 1).text(), translate.table.item(translate_113, 2).text()],
                "subtitle": [subtitle_113.text(2), subtitle_113.text(3)],
            },
            search={"transcript": transcript_visible, "translate": translate_visible, "subtitle": subtitle_visible},
            overlap={
                "canonical_id": overlap.canonical_id,
                "source_ids": list(overlap.source_utterance_ids),
                "start": overlap.start,
                "end": overlap.end,
                "zh": overlap.chinese,
                "vi_subtitle": overlap.vi_subtitle,
                "vi_dubbing": overlap.vi_dubbing,
            },
            canonical_count=len(entries),
            alignment=all(identity == identities[0] for identity in identities[1:]),
            speaker_labels_absent="SPK_" not in overlap_ass,
            tts_manifest_unchanged=before_manifest == after_manifest,
        )
        application.processEvents()
        exit_code = 0
    except Exception as exc:
        result.update(status="failed", error=str(exc), exception=type(exc).__name__,
                      traceback=traceback.format_exc())
        logging.getLogger(__name__).exception("[RUNTIME TIMELINE UI] failed")
        exit_code = 2
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
    if "--runtime-timeline-project" in sys.argv:
        index = sys.argv.index("--runtime-timeline-project")
        try:
            project_path = sys.argv[index + 1]
            result_index = sys.argv.index("--runtime-result")
            result_path = sys.argv[result_index + 1]
        except (ValueError, IndexError):
            raise SystemExit("Cần --runtime-timeline-project <project.json> --runtime-result <result.json>")
        return _runtime_timeline_ui(project_path, result_path)
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
