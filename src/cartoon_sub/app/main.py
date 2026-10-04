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


def _runtime_subtitle_autosegment(project_path, result_path):
    """Exercise the Auto Segment All controller path from a frozen build."""
    from cartoon_sub.app.controller import Controller
    from cartoon_sub.subtitle.segmentation_qc import review_display_segment
    from cartoon_sub.syllable.vietnamese import count_syllables

    result_file = Path(result_path)
    result = {"build": BUILD_MARKER, "project": str(Path(project_path).resolve())}
    try:
        controller = Controller()
        controller.accept(controller.load(project_path))
        controller.auto_segment()
        first = [(row.id, [(item.id, item.start, item.end, item.vi_text) for item in row.display_segments])
                 for row in controller.project.utterances]
        controller.auto_segment()
        second = [(row.id, [(item.id, item.start, item.end, item.vi_text) for item in row.display_segments])
                  for row in controller.project.utterances]
        controller.accept(controller.load(project_path))
        project = controller.project
        service = controller.segmentation_service
        _, settings = service.settings_for(project)
        rows = {}
        for utterance in project.utterances:
            if utterance.id not in (33, 53, 65, 101):
                continue
            flags = [review_display_segment(item, settings, service.source_text(project, utterance))
                     for item in utterance.display_segments]
            rows[str(utterance.id)] = {
                "children": len(utterance.display_segments),
                "max_syllables": max(count_syllables(item.vi_text) for item in utterance.display_segments),
                "max_chars": max(max((len(line.strip()) for line in item.vi_text.splitlines()), default=0)
                                 for item in utterance.display_segments),
                "qc": sorted({flag for group in flags for flag in group}),
            }
        result.update(status="completed", subtitle_source=project.subtitle_text_source,
                      settings={"preferred_syllables": settings.preferred_syllables_max,
                                "max_syllables": settings.max_syllables,
                                "max_lines": settings.max_lines,
                                "preferred_chars": settings.preferred_chars_per_line,
                                "hard_chars": settings.hard_max_chars_per_line,
                                "max_duration": settings.max_duration},
                      rows=rows, second_run_idempotent=first == second)
        exit_code = 0
    except Exception as exc:
        result.update(status="failed", error=str(exc), exception=type(exc).__name__,
                      traceback=traceback.format_exc())
        logging.getLogger(__name__).exception("[RUNTIME SUBTITLE AUTOSEGMENT] failed")
        exit_code = 2
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


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


def _runtime_translation_qa(project_path, result_path):
    """Inject one mixed-script row and exercise targeted QA from a frozen build."""
    from cartoon_sub.app.settings import SettingsStore
    from cartoon_sub.project.project_manager import ProjectManager
    from cartoon_sub.translation.qa_service import TranslationQAService
    from cartoon_sub.translation.qc import local_translation_qa

    source = "他还有一件中品法器, 流云笠。"
    bad_target = "Hắn còn có một kiện trung phẩm pháp khí là Lưu Vân L笠."
    fixed_target = "Hắn còn có một kiện pháp khí trung phẩm tên là Lưu Vân Lạp."
    result_file = Path(result_path)
    result = {"build": BUILD_MARKER, "project": str(Path(project_path).resolve())}
    try:
        directory = Path(project_path).parent
        project = ProjectManager().load(directory)
        segment = project.segments[0]
        identity = [segment.id, segment.start, segment.end, segment.speaker_id]
        segment.zh = source
        segment.vi = bad_target
        before = local_translation_qa(project, segment)

        class RuntimeStore:
            def __init__(self):
                self.real = SettingsStore()

            def load(self):
                return self.real.load()

            @staticmethod
            def get_key(_provider):
                return "runtime-test-key"

        class RuntimeClient:
            @staticmethod
            def generate_json(_system, prompt, _schema, _model, **_kwargs):
                payload = json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]
                return {"translations": [{
                    "id": payload["targets"][0]["id"], "vi": fixed_target,
                    "review_note": "", "meaning_preservation": "high", "compressed": False,
                }]}

            @staticmethod
            def close():
                pass

        completed, _ = TranslationQAService(RuntimeStore(), lambda _key: RuntimeClient()).run(
            project, directory, ids=[segment.id],
        )
        updated = completed.segments[0]
        after = local_translation_qa(completed, updated)
        live_srt = (directory / "exports" / "translate" / "vi_subtitle.srt").read_text(encoding="utf-8-sig")
        result.update(
            status="completed",
            detected=before["status"] == "FAIL",
            issues=[item["type"] for item in before["issues"]],
            retry_translation=updated.vi_subtitle,
            qa_after_retry=after["status"],
            qa_status=completed.translation_qa[str(updated.id)]["status"],
            identity_preserved=identity == [updated.id, updated.start, updated.end, updated.speaker_id],
            live_srt_synced=fixed_target in live_srt and "笠" not in live_srt,
        )
        exit_code = 0
    except Exception as exc:
        result.update(status="failed", error=str(exc), exception=type(exc).__name__,
                      traceback=traceback.format_exc())
        logging.getLogger(__name__).exception("[RUNTIME TRANSLATION QA] failed")
        exit_code = 2
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


def _runtime_translation_qa_scan(project_path, result_path):
    """Read-only local QA scan used to verify a real project from a frozen build."""
    from cartoon_sub.project.project_manager import ProjectManager
    from cartoon_sub.translation.qc import local_dubbing_qa, local_translation_qa

    result_file = Path(result_path)
    result = {"build": BUILD_MARKER, "project": str(Path(project_path).resolve())}
    try:
        project = ProjectManager().load(Path(project_path).parent)
        subtitle_han_ids = []
        dubbing_han_ids = []
        for segment in project.segments:
            subtitle = local_translation_qa(project, segment)
            dubbing = local_dubbing_qa(project, segment)
            if any(item["type"] == "UNTRANSLATED_HAN" for item in subtitle["issues"]):
                subtitle_han_ids.append(segment.id)
            if any(item["type"] == "UNTRANSLATED_HAN" for item in dubbing["issues"]):
                dubbing_han_ids.append(segment.id)
        target = next((segment for segment in project.segments if segment.id == 34), None)
        result.update(
            status="completed",
            total=len(project.segments),
            subtitle_source_selector=project.subtitle_text_source,
            subtitle_han_ids=subtitle_han_ids,
            dubbing_han_ids=dubbing_han_ids,
            id34_vi_subtitle=target.vi_subtitle if target else None,
            id34_vi_dubbing=target.vi_dubbing if target else None,
        )
        exit_code = 0
    except Exception as exc:
        result.update(status="failed", error=str(exc), exception=type(exc).__name__,
                      traceback=traceback.format_exc())
        logging.getLogger(__name__).exception("[RUNTIME TRANSLATION QA SCAN] failed")
        exit_code = 2
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


def _runtime_ui_zoom(result_path):
    """Exercise all editor tabs and global zoom levels from a frozen build."""
    from PySide6.QtWidgets import QApplication
    from cartoon_sub.ui.main_window import MainWindow

    result_file = Path(result_path)
    result = {"build": BUILD_MARKER}
    application = QApplication.instance() or QApplication([])
    window = None
    try:
        window = MainWindow()
        for index in range(window.tabs.count()):
            window.tabs.setTabEnabled(index, True)
        window.show()
        application.processEvents()
        levels = {}
        for percent in (0, 25, 50, 75, 100):
            window.ui_zoom_manager.set_percent(percent)
            visible_tabs = []
            for index in range(window.tabs.count()):
                window.tabs.setCurrentIndex(index)
                application.processEvents()
                visible_tabs.append(window.tabs.tabText(index))
            levels[str(percent)] = {
                "actual_scale": window.ui_zoom_manager.actual_scale,
                "font_points": application.font().pointSizeF(),
                "timeline_row_height": window.pages[2].table.verticalHeader().defaultSectionSize(),
                "tabs": visible_tabs,
            }
        result.update(
            status="completed",
            levels=levels,
            manual_qa_button=window.pages[2].manual_qa_button.text(),
            extended_selection=window.pages[2].table.selectionMode().name == "ExtendedSelection",
            shortcuts=len(window.zoom_shortcuts),
        )
        exit_code = 0
    except Exception as exc:
        result.update(status="failed", error=str(exc), exception=type(exc).__name__,
                      traceback=traceback.format_exc())
        logging.getLogger(__name__).exception("[RUNTIME UI ZOOM] failed")
        exit_code = 2
    finally:
        if window is not None:
            window.voice_sync_timer.stop()
            window.hide()
            window.deleteLater()
        application.processEvents()
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


def main():
    configure_logging()
    logging.getLogger(__name__).info("[BUILD] %s", BUILD_MARKER)
    if "--runtime-ui-zoom" in sys.argv:
        try:
            result_index = sys.argv.index("--runtime-result")
            result_path = sys.argv[result_index + 1]
        except (ValueError, IndexError):
            raise SystemExit("Cần --runtime-ui-zoom --runtime-result <result.json>")
        return _runtime_ui_zoom(result_path)
    if "--runtime-translation-qa-scan-project" in sys.argv:
        index = sys.argv.index("--runtime-translation-qa-scan-project")
        try:
            project_path = sys.argv[index + 1]
            result_index = sys.argv.index("--runtime-result")
            result_path = sys.argv[result_index + 1]
        except (ValueError, IndexError):
            raise SystemExit("Cần --runtime-translation-qa-scan-project <project.json> --runtime-result <result.json>")
        return _runtime_translation_qa_scan(project_path, result_path)
    if "--runtime-subtitle-autosegment-project" in sys.argv:
        index = sys.argv.index("--runtime-subtitle-autosegment-project")
        try:
            project_path = sys.argv[index + 1]
            result_index = sys.argv.index("--runtime-result")
            result_path = sys.argv[result_index + 1]
        except (ValueError, IndexError):
            raise SystemExit("Cần --runtime-subtitle-autosegment-project <project.json> --runtime-result <result.json>")
        return _runtime_subtitle_autosegment(project_path, result_path)
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
    if "--runtime-translation-qa-project" in sys.argv:
        index = sys.argv.index("--runtime-translation-qa-project")
        try:
            project_path = sys.argv[index + 1]
            result_index = sys.argv.index("--runtime-result")
            result_path = sys.argv[result_index + 1]
        except (ValueError, IndexError):
            raise SystemExit("Cần --runtime-translation-qa-project <project.json> --runtime-result <result.json>")
        return _runtime_translation_qa(project_path, result_path)
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
    window.start_openrouter_catalog_sync()
    return application.exec()

if __name__ == "__main__":
    raise SystemExit(main())
