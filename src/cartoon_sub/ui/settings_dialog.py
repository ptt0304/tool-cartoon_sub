import html
from datetime import datetime
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
    QPlainTextEdit, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from cartoon_sub.ai.openrouter_client import (filter_models, mask_api_key, model_author,
    parse_openrouter_api_keys, supports_capability, validate_openrouter_key)
from cartoon_sub.app.settings import AISettings
from cartoon_sub.project.cache import check_cancel
from cartoon_sub.ui.worker import Worker


MODELS_URL = "https://openrouter.ai/models"
RANKINGS_URL = "https://openrouter.ai/rankings#top-models"
PROVIDERS_URL = "https://openrouter.ai/providers"


class OpenRouterKeysDialog(QDialog):
    """Explicit editor: environment keys are never populated here automatically."""
    def __init__(self, keys, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit OpenRouter API Keys"); self.resize(620, 300)
        layout = QVBoxLayout(self)
        note = QLabel("One API key per line. Order is preserved and duplicates are removed on Save.")
        note.setWordWrap(True); layout.addWidget(note)
        self.editor = QPlainTextEdit(); self.editor.setPlaceholderText("sk-or-v1-…\nsk-or-v1-…")
        self.editor.setPlainText("\n".join(keys)); layout.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def keys(self):
        return parse_openrouter_api_keys(self.editor.toPlainText())


def _format_context(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return "N/A"
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    if value >= 1_000:
        return f"{value / 1_000:g}K"
    return str(value)


def _token_price(value):
    try:
        return f"${Decimal(str(value)) * Decimal(1_000_000):g} / 1M tokens"
    except (InvalidOperation, ValueError, TypeError):
        return "N/A"


class ModelSelector(QWidget):
    """Reusable searchable OpenRouter selector with capability-aware metadata."""
    selectionChanged = Signal()

    def __init__(self, title, capability, placeholder, *, rankings=False,
                 empty_text="No compatible OpenRouter models", allow_empty=False,
                 empty_choice="Chọn model…", parent=None):
        super().__init__(parent)
        self.catalog = []
        self.capability = capability
        self.author = "all"
        self._selected_id = ""
        self.empty_text = empty_text
        self.allow_empty = allow_empty
        self.empty_choice = empty_choice

        group = QGroupBox(title)
        form = QFormLayout(group)
        self.search = QLineEdit(); self.search.setPlaceholderText(placeholder)
        self.model = QComboBox(); self.model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(28)
        self.warning = QLabel(); self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #b26a00")
        self.metadata = QLabel(); self.metadata.setWordWrap(True)
        self.metadata.setTextFormat(Qt.TextFormat.RichText); self.metadata.setOpenExternalLinks(True)
        form.addRow("Search", self.search); form.addRow("Model", self.model)
        form.addRow("", self.warning); form.addRow("", self.metadata)
        links = f'<a href="{MODELS_URL}">View on OpenRouter ↗</a>'
        if rankings:
            links += f' &nbsp; <a href="{RANKINGS_URL}">View Rankings ↗</a>'
        link = QLabel(links); link.setOpenExternalLinks(True); form.addRow("", link)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(group)

        self.search.textChanged.connect(self.rebuild)
        self.model.currentIndexChanged.connect(self._selection_changed)

    def set_catalog(self, catalog, preferred=None):
        self.catalog = list(catalog)
        if preferred is not None:
            self._selected_id = preferred
        self.rebuild()

    def set_author(self, author):
        self.author = author or "all"
        self.rebuild()

    def set_capability(self, capability):
        self.capability = capability
        self.rebuild()

    def selected_id(self):
        if self.allow_empty and self.model.currentIndex() >= 0 and self.model.currentData() == "":
            return ""
        return self.model.currentData() or self._selected_id

    def selected_model(self):
        selected = self.selected_id()
        return next((item for item in self.catalog if item["id"] == selected), None)

    def selection_is_compatible(self):
        model = self.selected_model()
        return bool(model and (self.capability == "transcription_catalog"
                               or supports_capability(model, self.capability)))

    def rebuild(self, *_args):
        preferred = self.model.currentData() or self._selected_id
        options = filter_models(self.catalog, capability=self.capability, author=self.author,
                                search=self.search.text())
        self.model.blockSignals(True); self.model.clear()
        if self.allow_empty:
            self.model.addItem(self.empty_choice, "")
        for item in options:
            self.model.addItem(f"{item['name']} — {item['id']}", item["id"])
        index = self.model.findData(preferred)
        if index < 0 and preferred:
            existing = next((item for item in self.catalog if item["id"] == preferred), None)
            if self.capability == "transcription_catalog" and existing is None:
                self._selected_id = ""
                preferred = ""
            elif existing and not (self.capability == "transcription_catalog"
                                 or supports_capability(existing, self.capability)):
                prefix = "[Incompatible]"
            elif existing:
                prefix = "[Hidden by filter]"
            else:
                prefix = "[Unavailable]"
            if preferred:
                self.model.insertItem(0, f"{prefix} {preferred}", preferred); index = 0
        if self.model.count():
            self.model.setCurrentIndex(index if index >= 0 else 0)
        else:
            empty_text = self.empty_text
            if self.capability == "transcription_catalog" and self.catalog:
                empty_text = "No matching transcription models"
            self.model.addItem(empty_text, None)
        if self.capability == "transcription_catalog":
            self.model.setEnabled(bool(self.catalog))
        self.model.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self, *_args):
        value = self.model.currentData()
        if value:
            self._selected_id = value
        elif self.allow_empty and value == "":
            self._selected_id = ""
        model = self.selected_model()
        compatible = self.selection_is_compatible()
        if self.capability == "transcription_catalog" and not self.catalog:
            self.warning.clear()
            self.metadata.setText("")
        elif self.allow_empty and not value:
            self.warning.clear(); self.metadata.setText("")
        elif not model:
            self.warning.setText("⚠ Saved model is unavailable in the current catalog.")
            self.metadata.setText("")
        elif not compatible:
            self.warning.setText("⚠ Selected model is incompatible with this task/input mode.")
            self.metadata.setText(self._metadata_html(model))
        else:
            self.warning.clear(); self.metadata.setText(self._metadata_html(model))
        self.selectionChanged.emit()

    @staticmethod
    def _metadata_html(model):
        architecture, pricing = model.get("architecture", {}), model.get("pricing", {})
        inputs = [str(item).title() for item in architecture.get("input_modalities", [])]
        outputs = [str(item).title() for item in architecture.get("output_modalities", [])]
        capabilities = []
        for label, modality in (("Text", "text"), ("Image", "image"), ("Video", "video")):
            capabilities.append(("✓" if modality in {str(x).casefold() for x in architecture.get("input_modalities", [])}
                                 else "✕") + f" {label}")
        prices = []
        if "prompt" in pricing:
            prices.append(f"Input: {_token_price(pricing['prompt'])}")
        if "completion" in pricing:
            prices.append(f"Output: {_token_price(pricing['completion'])}")
        for key, value in pricing.items():
            if key not in {"prompt", "completion"} and value not in (None, ""):
                prices.append(f"{html.escape(str(key))}: {html.escape(str(value))} (raw catalog value)")
        model_id = html.escape(model["id"])
        return (
            f"<b>{html.escape(model.get('name') or model['id'])}</b><br>"
            f"Author: {html.escape(model_author(model))}<br>Model ID: {model_id}<br>"
            f"Input: {html.escape(', '.join(inputs) or 'N/A')}<br>"
            f"Output: {html.escape(', '.join(outputs) or 'N/A')}<br>"
            f"Context: {_format_context(model.get('context_length'))}<br>"
            f"Pricing:<br>{'<br>'.join(prices) if prices else 'N/A'}<br>"
            f"Capabilities: {' &nbsp; '.join(capabilities)}<br>"
            f'<a href="https://openrouter.ai/{model_id}">View selected model on OpenRouter ↗</a>'
        )


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller, self.worker = controller, None
        self.saved_settings = controller.settings_store.load()
        self.key_source = controller.settings_store.openrouter_key_source()
        self.pending_keys = controller.settings_store.get_openrouter_keys()
        self.keys_edited = False
        self.key_test_results = []
        self.setWindowTitle("Settings > AI"); self.resize(820, 760)

        outer = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); content = QVBoxLayout(body); content.setSpacing(10)
        scroll.setWidget(body); outer.addWidget(scroll, 1)

        intro = QLabel(
            f'AI models are provided through OpenRouter. &nbsp; '
            f'<a href="{MODELS_URL}">OpenRouter Models</a> &nbsp; '
            f'<a href="{RANKINGS_URL}">OpenRouter Rankings</a> &nbsp; '
            f'<a href="{PROVIDERS_URL}">OpenRouter Providers</a>')
        intro.setOpenExternalLinks(True); intro.setWordWrap(True); content.addWidget(intro)

        openrouter = QGroupBox("OpenRouter"); openrouter_form = QFormLayout(openrouter)
        self.keys_summary = QLabel(); self.keys_summary.setWordWrap(True)
        key_row = QWidget(); key_layout = QHBoxLayout(key_row); key_layout.setContentsMargins(0, 0, 0, 0)
        self.edit_keys_button = QPushButton("Edit Keys…")
        self.test_connection_button = QPushButton("Test All Keys")
        key_layout.addWidget(self.edit_keys_button); key_layout.addWidget(self.test_connection_button)
        key_layout.addStretch(1)
        self.connection_status = QLabel(); self.connection_status.setWordWrap(True)
        openrouter_form.addRow("API Keys", self.keys_summary); openrouter_form.addRow("", key_row)
        openrouter_form.addRow("", self.connection_status); content.addWidget(openrouter)

        catalog_box = QGroupBox("Model Catalog"); catalog_form = QFormLayout(catalog_box)
        self.catalog_status = QLabel(); self.catalog_status.setWordWrap(True)
        catalog_form.addRow(self.catalog_status); content.addWidget(catalog_box)

        filters = QGroupBox("Model Author"); filters_form = QFormLayout(filters)
        self.author_search = QLineEdit(); self.author_search.setPlaceholderText("Search author…")
        self.author = QComboBox(); self.provider = self.author
        filters_form.addRow("Search author", self.author_search); filters_form.addRow("Author", self.author)
        content.addWidget(filters)

        self.transcription_selector = ModelSelector("Speech-to-Text / Transcription",
            "transcription_catalog", "Search transcription model…",
            empty_text="No transcription models available")
        self.default_selector = ModelSelector(
            "Model AI mặc định", "text", "Tìm model AI mặc định…", rankings=True,
            allow_empty=True, empty_choice="Chọn Model AI mặc định…")
        content.addWidget(self.transcription_selector); content.addWidget(self.default_selector)
        default_help = QLabel(
            "Được sử dụng cho mọi chức năng AI nếu tab tương ứng không chọn model riêng.")
        default_help.setWordWrap(True); content.addWidget(default_help)

        vision_box = QGroupBox("Visual Input"); vision_layout = QVBoxLayout(vision_box)
        visual_row = QHBoxLayout(); visual_row.addWidget(QLabel("Visual Input:"))
        self.frames_mode = QRadioButton("Extracted Frames"); self.video_mode = QRadioButton("Direct Video")
        visual_row.addWidget(self.frames_mode); visual_row.addWidget(self.video_mode); visual_row.addStretch(1)
        vision_layout.addLayout(visual_row)
        content.addWidget(vision_box)

        self.advanced_toggle = QToolButton(); self.advanced_toggle.setText("Advanced ▸")
        self.advanced_toggle.setCheckable(True); self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        content.addWidget(self.advanced_toggle)
        self.advanced = QFrame(); advanced_form = QFormLayout(self.advanced)
        self.chunk = QSpinBox(); self.chunk.setRange(30, 50); self.chunk.setValue(self.saved_settings.translation_chunk_size)
        self.retry = QSpinBox(); self.retry.setRange(0, 5); self.retry.setValue(self.saved_settings.retry_count)
        advanced_form.addRow("Translation chunk size", self.chunk); advanced_form.addRow("Retry count", self.retry)
        self.advanced.hide(); content.addWidget(self.advanced); content.addStretch(1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save); self.buttons.rejected.connect(self.reject); outer.addWidget(self.buttons)

        # Compatibility aliases used by existing UI tests and integrations.
        self.translation_selector = self.default_selector
        self.translation_model = self.default_selector.model
        self.transcription_model = self.transcription_selector.model
        self.search = self.translation_selector.search

        self.edit_keys_button.clicked.connect(self.edit_keys)
        self.test_connection_button.clicked.connect(self.test_connection)
        self.author_search.textChanged.connect(self._populate_authors)
        self.author.currentIndexChanged.connect(self._author_changed)
        self.frames_mode.toggled.connect(self._vision_mode_changed)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)

        self.frames_mode.setChecked(self.saved_settings.vision_input_mode != "video")
        self.video_mode.setChecked(self.saved_settings.vision_input_mode == "video")
        self._update_keys_summary()
        self._reload_catalogs(initial=True)
        self._vision_mode_changed()

        self.catalog_timer = QTimer(self); self.catalog_timer.setInterval(500)
        self.catalog_timer.timeout.connect(self._poll_catalog_sync)
        if self.controller.settings_store.catalog_sync_snapshot()["state"] == "syncing":
            self.catalog_timer.start()

    def _update_keys_summary(self):
        if self.keys_edited or self.pending_keys:
            source = "api_key.txt" if self.key_source == "file" else self.key_source
            self.keys_summary.setText(f"{len(self.pending_keys)} keys configured ({source})")
        elif self.key_source == "environment":
            self.keys_summary.setText("No keys stored in Settings. Using OPENROUTER_API_KEY.")
        else:
            self.keys_summary.setText("No OpenRouter API keys configured.")

    def edit_keys(self):
        dialog = OpenRouterKeysDialog(self.pending_keys, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.pending_keys = dialog.keys(); self.keys_edited = True
            self._update_keys_summary(); self.connection_status.clear()

    def _toggle_advanced(self, expanded):
        self.advanced.setVisible(expanded)
        self.advanced_toggle.setText("Advanced ▾" if expanded else "Advanced ▸")

    def _reload_catalogs(self, initial=False):
        snapshot = self.controller.settings_store.catalog_sync_snapshot()
        general = snapshot.get("general") or {"models": [], "updated_at": ""}
        transcription = snapshot.get("transcription") or {"models": [], "updated_at": ""}
        self.general_models = list(general["models"])
        self.transcription_models = list(transcription["models"])
        self._populate_authors(preferred=self.saved_settings.provider_filter if initial else self.author.currentData())
        transcription_ids = {model["id"] for model in self.transcription_models}
        transcription_preferred = self.saved_settings.transcription_model if initial else None
        if transcription_preferred not in transcription_ids:
            # Legacy Gemini/OpenAI compatibility IDs do not belong in the
            # dedicated OpenRouter transcription selector. Let the selector
            # choose the first live catalog entry instead of showing stale UI.
            transcription_preferred = None
            if initial:
                self.transcription_selector._selected_id = ""
        self.transcription_selector.set_catalog(self.transcription_models,
                                                transcription_preferred)
        self.default_selector.set_catalog(self.general_models,
            self.saved_settings.default_ai_model if initial else None)
        self._show_catalog_status(snapshot, general, transcription)

    def _show_catalog_status(self, snapshot, general, transcription):
        authors = {model_author(item) for item in general["models"]}
        timestamps = [value for value in (general.get("updated_at"), transcription.get("updated_at")) if value]
        latest = max(timestamps) if timestamps else "Never"
        if latest != "Never":
            try:
                latest = datetime.fromisoformat(latest.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        state = snapshot.get("state", "idle")
        status = "✓ Synced" if state == "synced" else "⏳ Synchronizing…" if state == "syncing" else (
            "⚠ Offline — using cached model catalog" if general["models"] or transcription["models"]
            else "⚠ Catalog unavailable")
        self.catalog_status.setText(
            f"Status: {status}\nModels: {len(general['models'])}\nAuthors: {len(authors)}\n"
            f"Transcription: {len(transcription['models'])}\nLast updated: {latest}")

    def _poll_catalog_sync(self):
        state = self.controller.settings_store.catalog_sync_snapshot()["state"]
        if state != "syncing":
            self.catalog_timer.stop(); self._reload_catalogs()

    def _populate_authors(self, *_args, preferred=None):
        current = preferred or self.author.currentData() or "all"
        needle = self.author_search.text().strip().casefold()
        authors = sorted({model_author(item) for item in self.general_models + self.transcription_models
                          if not needle or needle in model_author(item).casefold()})
        self.author.blockSignals(True); self.author.clear(); self.author.addItem("All", "all")
        for author in authors:
            self.author.addItem(author, author)
        index = self.author.findData(current); self.author.setCurrentIndex(index if index >= 0 else 0)
        self.author.blockSignals(False); self._author_changed()

    def _author_changed(self, *_args):
        author = self.author.currentData() or "all"
        for selector in (self.transcription_selector, self.default_selector):
            selector.set_author(author)

    def _vision_mode_changed(self, *_args):
        pass

    def _test_operation(self, *, cancel=None, progress=None):
        keys = list(self.pending_keys) if self.keys_edited else self.controller.settings_store.get_openrouter_keys()
        if not keys:
            raise ValueError("OpenRouter API key is required")
        results = []
        for index, key in enumerate(keys, 1):
            check_cancel(cancel)
            if progress:
                progress(f"Testing Key {index}/{len(keys)} — {mask_api_key(key)}")
            result = validate_openrouter_key(key)
            results.append({"index": index, "masked": mask_api_key(key), **result})
        valid = next((key for key, result in zip(keys, results) if result["status"] == "VALID"), None)
        if valid:
            self.controller.settings_store.sync_openrouter_catalogs_once(
                key_override=valid, retry=True, progress=progress)
        return results

    def test_connection(self):
        if self.worker is not None:
            return
        self.worker = Worker(self._test_operation, self, log_errors=False)
        self.worker.result.connect(self._show_key_results)
        self.worker.error.connect(lambda message: self.connection_status.setText(f"✕ {message}"))
        self.worker.finished.connect(self._worker_finished)
        self.test_connection_button.setText("Test All Keys…")
        self.test_connection_button.setEnabled(False); self.edit_keys_button.setEnabled(False)
        self.worker.start()

    def _show_key_results(self, results):
        self.key_test_results = list(results)
        icons = {"VALID": "✓", "RATE_LIMITED": "⚠"}
        lines = [f"{icons.get(item['status'], '✕')} Key {item['index']}  {item['masked']}  {item['message']}"
                 for item in results]
        valid = sum(item["status"] == "VALID" for item in results)
        lines.append(f"\nSummary: {valid} / {len(results)} keys valid")
        self.connection_status.setText("\n".join(lines))

    def _worker_finished(self):
        self.worker.deleteLater(); self.worker = None
        self.test_connection_button.setText("Test All Keys")
        self.test_connection_button.setEnabled(True); self.edit_keys_button.setEnabled(True)
        self._reload_catalogs()

    def values(self):
        transcription = self.transcription_selector.selected_id()
        if not transcription:
            raise ValueError("Speech-to-Text: select a model.")
        # Transcription selections are OpenRouter model IDs from the dedicated catalog.
        if "/" not in transcription:
            raise ValueError("Speech-to-Text: legacy selection requires an OpenRouter model selection.")
        default_model = self.default_selector.selected_id()
        if not default_model or not self.default_selector.selection_is_compatible():
            raise ValueError("Model AI mặc định là bắt buộc. Hãy chọn một model text → text hợp lệ.")
        return AISettings(
            default_ai_model=default_model,
            tab_model_overrides=dict(self.saved_settings.tab_model_overrides or {}),
            ai_model_routing_version=1,
            transcription_model=transcription,
            transcription_provider="openrouter",
            translation_model=self.saved_settings.translation_model,
            translation_provider="openrouter",
            vision_speaker_model=self.saved_settings.vision_speaker_model,
            vision_input_mode="frames" if self.frames_mode.isChecked() else "video",
            review_model_mode=self.saved_settings.review_model_mode,
            review_model=self.saved_settings.review_model,
            translation_chunk_size=self.chunk.value(), retry_count=self.retry.value(),
            provider_filter=self.author.currentData() or "all",
            gemini_api_keys_file=self.saved_settings.gemini_api_keys_file,
            api_key_file=self.saved_settings.api_key_file,
            legacy_gemini_video_model=self.saved_settings.legacy_gemini_video_model,
            legacy_gemini_audio_model=self.saved_settings.legacy_gemini_audio_model,
            ui_zoom_percent=self.saved_settings.ui_zoom_percent,
        ).validate()

    def save(self):
        try:
            invalid = sum(item.get("status") == "INVALID_OR_REVOKED"
                          for item in self.key_test_results)
            if invalid:
                QMessageBox.warning(self, "OpenRouter API Keys",
                                    f"{invalid} of {len(self.key_test_results)} OpenRouter API keys "
                                    "appears invalid or revoked. The keys will still be saved.")
            keys = self.pending_keys if self.keys_edited else None
            self.controller.settings_store.save(self.values(), openrouter_keys=keys)
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Settings > AI", str(exc))

    def reject(self):
        if self.worker is not None:
            self.worker.cancel(); self.connection_status.setText("Waiting for current request to stop…")
        else:
            super().reject()

    def closeEvent(self, event):
        self.catalog_timer.stop()
        if self.worker is not None:
            self.reject(); event.ignore()
        else:
            super().closeEvent(event)
