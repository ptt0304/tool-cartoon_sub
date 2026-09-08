"""Run explicitly: real ffprobe/audio extraction and offscreen Qt worker smoke."""
import os
import sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication, QTabWidget
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QFontDatabase, QFont
from cartoon_sub.ui.main_window import MainWindow
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.media.ffmpeg import FFmpeg
from cartoon_sub.app.controller import Controller
from cartoon_sub.app.settings import SettingsStore
from cartoon_sub.ui.settings_dialog import SettingsDialog
from cartoon_sub.transcription.pipeline import TranscriptionPipeline
from cartoon_sub.transcription.gemini_transcriber import GeminiTranscriber
from cartoon_sub.translation.context_service import ContextService
from cartoon_sub.translation.pipeline import TranslationPipeline
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.ui.context_dialog import ContextDialog
from unittest.mock import Mock
from cartoon_sub.ui.speaker_dialog import SpeakerDialog
from cartoon_sub.tts.export_service import export_speakers

app = QApplication([])
for font in ('C:/Windows/Fonts/segoeui.ttf', 'C:/Windows/Fonts/msyh.ttc'):
    QFontDatabase.addApplicationFont(font)
app.setFont(QFont('Segoe UI', 10))
window = MainWindow()
window.show()
assert window.tabs.count() == 6
video = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else next(Path.cwd().glob("*.mp4"), None)
if video is None:
    raise SystemExit("Pass the path to a test MP4: python tests/smoke_local.py path/to/video.mp4")
with tempfile.TemporaryDirectory() as directory:
    vault = Mock()
    vault.get_password.return_value = "test-only-fake-key"
    controller = Controller(SettingsStore(Path(directory) / "settings", vault))
    window.controller = controller
    client = Mock()
    client.transcribe_json.return_value = {"segments": [
        {"id": 1, "start": 0, "end": 2.5, "zh": "这个女孩叫小美"},
        {"id": 2, "start": 2.5, "end": 5, "zh": "今天她收到了一封信"}]}
    def adapter(key_provider, model, cache, retries):
        return GeminiTranscriber(key_provider, model, cache, retries, lambda key: client)
    controller.pipeline = TranscriptionPipeline(controller.settings_store, transcriber_factory=adapter)
    text_client = Mock()
    profile = StoryContext(setting="Hiện đại", narration="Ngôi thứ ba").to_dict()
    text_client.generate_json.side_effect = [profile, {"segments": [
        {"id": 1, "vi": "Cô gái này tên là Tiểu Mỹ.", "review_note": ""},
        {"id": 2, "vi": "Hôm nay cô ấy nhận được một lá thư.", "review_note": ""}]}]
    controller.context_service = ContextService(controller.settings_store, lambda key: text_client)
    controller.translation_pipeline = TranslationPipeline(controller.settings_store, lambda key: text_client)
    result = controller.create(str(video), directory)
    controller.accept(result)
    assert controller.project.metadata["width"] > 0
    window.accept_project(result)
    loop = QEventLoop()
    window.transcribe()
    window.worker.finished.connect(loop.quit)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert window.controller.project.transcription_status == "completed"
    assert window.pages[1].table.rowCount() == 2
    speakers = SpeakerDialog(controller.project, directory, window)
    speakers.add()
    speakers.target.setCurrentIndex(speakers.target.findData('SPK_01'))
    speakers.table.selectAll()
    speakers.assign()
    speakers.approve()
    controller.project = speakers.project
    controller.save()
    window.refresh()
    window.analyze_context()
    window.worker.finished.connect(loop.quit)
    loop.exec()
    assert controller.project.context_status == "proposal_ready"
    assert not window.pages[2].translate_button.isEnabled()
    editor = ContextDialog(controller.project.context_proposal, {1, 2}, window, True)
    editor.show()
    app.processEvents()
    editor.apply()
    controller.apply_context(editor.result_context)
    window.refresh()
    assert window.pages[2].translate_button.isEnabled()
    window.translate()
    window.worker.finished.connect(loop.quit)
    loop.exec()
    assert "Tiểu Mỹ" in window.controller.project.segments[0].vi
    assert (Path(directory) / "subtitle" / "vi.srt").is_file()
    assert text_client.generate_json.call_count == 2
    exported = export_speakers(controller.project, directory)
    assert list(exported.rglob('speaker.srt'))
    assert list(exported.rglob('000001.txt'))
    window.resize(1280, 850)
    window.tabs.setCurrentIndex(2)
    app.processEvents()
    window.grab().save(str(Path.cwd() / 'docs' / 'master_timeline_ui.png'))
    window.pages[2].findChild(QTabWidget).setCurrentIndex(1)
    app.processEvents()
    window.grab().save(str(Path.cwd() / 'docs' / 'master_timeline_table.png'))
    assert window.save_project()
    loaded = controller.manager.load(directory)
    assert len(loaded.segments) == 2
    audio = Path(directory) / "audio" / "source.wav"
    assert audio.stat().st_size > 44
    assert (Path(directory) / "subtitle" / "zh.srt").is_file()
    window.transcribe()
    window.worker.finished.connect(loop.quit)
    loop.exec()
    assert client.transcribe_json.call_count == 1
    assert "Tiểu Mỹ" in controller.project.segments[0].vi
    dialog = SettingsDialog(controller, window)
    dialog.show()
    app.processEvents()
    assert dialog.key.text() == ""
    assert dialog.values().transcription_model == "gemini-3.5-flash"
    controller.test_connection = Mock(return_value="Kết nối thành công (fake test)")
    dialog.test_connection()
    dialog.worker.finished.connect(loop.quit)
    loop.exec()
    assert "thành công" in dialog.status.text()
    assert dialog.worker is None
    dialog.reject()
    print("PASS: six tabs, Settings, metadata, real audio extraction, fake Gemini pipeline, cache, zh.srt, save/load")
    print(controller.project.metadata)
    window.controller.project = None
window.close()
