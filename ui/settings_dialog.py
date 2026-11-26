"""设置对话框：API Key、模型参数、测试连接。"""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from core.config import DATA_DIR, Config
from core.llm import DeepSeekClient
from core.local_llm import OllamaClient


class _TestSignals(QObject):
    finished = Signal(bool, str)


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(560)
        self._test_signals = _TestSignals(self)
        self._test_signals.finished.connect(self._on_test_done)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(12)

        title = QLabel("连接到 DeepSeek")
        title.setObjectName("AppTitle")
        layout.addWidget(title)
        hint = QLabel(
            "API Key 只保存在本机 config.json；也可以设置环境变量 DEEPSEEK_API_KEY。\n"
            "获取地址：platform.deepseek.com → API Keys"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        self.key_edit = QLineEdit(str(config.data.get("api_key", "")))
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-…")
        self.show_key_btn = QPushButton("显示")
        self.show_key_btn.setObjectName("Ghost")
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self._toggle_key_visibility)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.show_key_btn)
        form.addRow("API Key", key_row)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(["deepseek-flash", "deepseek-pro"])
        self.model_combo.setCurrentText(config.model)
        form.addRow("云端模型", self.model_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("自动：简单问答本地，其余云端", "auto")
        self.mode_combo.addItem("本地优先：简单问答本地，工具/截图云端", "local")
        self.mode_combo.addItem("仅云端：全部使用 DeepSeek", "cloud")
        mode_index = self.mode_combo.findData(str(config.data.get("model_mode") or "auto"))
        self.mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        form.addRow("模型模式", self.mode_combo)

        local_row = QHBoxLayout()
        self.local_model = QComboBox()
        self.local_model.setEditable(True)
        self.local_model.addItems(
            ["qwen2.5-3b:latest", "qwen2.5-3b-cpu:latest", "qwen3.5:4b"]
        )
        self.local_model.setCurrentText(
            str(config.data.get("local_model") or "qwen2.5-3b:latest")
        )
        refresh_local = QPushButton("刷新")
        refresh_local.setObjectName("Ghost")
        refresh_local.clicked.connect(self._refresh_local_models)
        local_row.addWidget(self.local_model, 1)
        local_row.addWidget(refresh_local)
        form.addRow("本地模型", local_row)

        self.local_url = QLineEdit(
            str(config.data.get("local_base_url") or "http://127.0.0.1:11434")
        )
        form.addRow("Ollama 地址", self.local_url)

        self.tokens = QSpinBox()
        self.tokens.setRange(256, 8192)
        self.tokens.setSingleStep(100)
        self.tokens.setValue(int(config.data.get("max_tokens", 4096)))
        form.addRow("最大回复 tokens", self.tokens)

        self.temp = QDoubleSpinBox()
        self.temp.setRange(0.0, 2.0)
        self.temp.setSingleStep(0.1)
        self.temp.setValue(float(config.data.get("temperature", 0.6)))
        form.addRow("温度", self.temp)

        self.save_caps = QCheckBox("保存截图原图到本地（推荐）")
        self.save_caps.setChecked(bool(config.data.get("save_captures", True)))
        form.addRow("", self.save_caps)

        self.pet_enabled = QCheckBox("显示桌面宠物（双击它可截图提问）")
        self.pet_enabled.setChecked(bool(config.data.get("pet_enabled", True)))
        form.addRow("", self.pet_enabled)

        self.thinking = QCheckBox("深度思考（复杂题更准，但明显更慢；预算不够会自动回退）")
        self.thinking.setChecked(bool(config.data.get("thinking", False)))
        self.thinking.setToolTip(
            "开启后模型会先输出思维链。截图快问快答建议关闭，否则可能长时间无正文。"
        )
        form.addRow("", self.thinking)

        self.auto_memory = QCheckBox(
            "自动整理长期记忆（每段对话达到 6 条消息后，后台提取一次）"
        )
        self.auto_memory.setChecked(bool(config.data.get("auto_memory", True)))
        self.auto_memory.setToolTip("会额外调用一次模型，可能产生少量 token 费用；可随时关闭。")
        form.addRow("", self.auto_memory)

        self.query_rewrite = QCheckBox("模糊追问时自动改写检索问题（推荐）")
        self.query_rewrite.setChecked(bool(config.data.get("query_rewrite", True)))
        self.query_rewrite.setToolTip("例如把“这个为什么”结合上一轮改写成完整问题，只影响长期记忆检索。")
        form.addRow("", self.query_rewrite)

        self.summary_enabled = QCheckBox("长对话自动摘要（历史过长时压缩较早消息）")
        self.summary_enabled.setChecked(bool(config.data.get("summary_enabled", True)))
        self.summary_enabled.setToolTip("超过字符阈值后，把较早消息压缩成摘要；只保留最近若干条原文。")
        form.addRow("", self.summary_enabled)

        self.history_limit = QSpinBox()
        self.history_limit.setRange(8000, 60000)
        self.history_limit.setSingleStep(2000)
        self.history_limit.setValue(int(config.data.get("history_char_limit", 18000)))
        form.addRow("摘要阈值（字符）", self.history_limit)

        self.embedding_enabled = QCheckBox("启用本地语义记忆（BGE-small-zh，需 models/ 模型）")
        self.embedding_enabled.setChecked(bool(config.data.get("embedding_enabled", True)))
        self.embedding_enabled.setToolTip(
            "模型缺失或加载失败时自动回退关键词检索，不会导致 App 打不开。"
        )
        form.addRow("", self.embedding_enabled)

        self.note_template = QTextEdit()
        self.note_template.setPlainText(config.note_template)
        self.note_template.setFixedHeight(130)
        self.note_template.setToolTip(
            "点「笔记 → 新建」会用这个模板；Agent 记笔记时也会按这个结构组织。"
        )
        form.addRow("笔记模板", self.note_template)

        data_row = QHBoxLayout()
        data_label = QLabel(str(DATA_DIR))
        data_label.setObjectName("Muted")
        open_btn = QPushButton("打开文件夹")
        open_btn.setObjectName("Ghost")
        open_btn.clicked.connect(self._open_data_dir)
        data_row.addWidget(data_label, 1)
        data_row.addWidget(open_btn)
        form.addRow("数据目录", data_row)

        layout.addLayout(form)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setObjectName("Ghost")
        self.test_btn.clicked.connect(self._test_connection)
        self.test_status = QLabel("")
        self.test_status.setObjectName("Muted")
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_status, 1)
        layout.addLayout(test_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("Primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def _refresh_local_models(self) -> None:
        models = OllamaClient.list_models(self.local_url.text().strip())
        if not models:
            self.test_status.setText("没有发现 Ollama 模型，请确认 Ollama 正在运行")
            return
        current = self.local_model.currentText().strip()
        self.local_model.clear()
        self.local_model.addItems(models)
        if current and self.local_model.findText(current) < 0:
            self.local_model.addItem(current)
        if current:
            self.local_model.setCurrentText(current)
        self.test_status.setText(f"发现 {len(models)} 个本地模型")

    def _toggle_key_visibility(self, visible: bool) -> None:
        self.key_edit.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)

    def _open_data_dir(self) -> None:
        try:
            os.startfile(str(DATA_DIR))
        except Exception:
            pass

    def _test_connection(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.test_status.setText("请先填写 API Key")
            return
        self.test_btn.setEnabled(False)
        self.test_status.setText("测试中…")
        model = self.model_combo.currentText().strip() or "deepseek-flash"
        base_url = self.config.base_url

        def run() -> None:
            try:
                client = DeepSeekClient(
                    api_key=key, base_url=base_url, model=model, max_tokens=32, temperature=0.0
                )
                answer = client.chat(
                    [{"role": "user", "content": "请只回复：连接成功"}]
                )
                self._test_signals.finished.emit(True, answer.strip()[:80])
            except Exception as exc:  # noqa: BLE001
                self._test_signals.finished.emit(False, str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _on_test_done(self, ok: bool, message: str) -> None:
        try:
            self.test_btn.setEnabled(True)
            self.test_status.setText(("✓ " if ok else "✗ ") + message)
        except RuntimeError:
            pass

    def _save(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "提示", "API Key 不能为空")
            return
        self.config.data["api_key"] = key
        self.config.data["model"] = self.model_combo.currentText().strip() or "deepseek-flash"
        self.config.data["max_tokens"] = int(self.tokens.value())
        self.config.data["temperature"] = float(self.temp.value())
        self.config.data["model_mode"] = str(self.mode_combo.currentData() or "auto")
        self.config.data["local_model"] = self.local_model.currentText().strip() or "qwen2.5-3b:latest"
        self.config.data["local_base_url"] = self.local_url.text().strip() or "http://127.0.0.1:11434"
        self.config.data["save_captures"] = bool(self.save_caps.isChecked())
        self.config.data["pet_enabled"] = bool(self.pet_enabled.isChecked())
        self.config.data["thinking"] = bool(self.thinking.isChecked())
        self.config.data["auto_memory"] = bool(self.auto_memory.isChecked())
        self.config.data["query_rewrite"] = bool(self.query_rewrite.isChecked())
        self.config.data["summary_enabled"] = bool(self.summary_enabled.isChecked())
        self.config.data["history_char_limit"] = int(self.history_limit.value())
        self.config.data["embedding_enabled"] = bool(self.embedding_enabled.isChecked())
        self.config.data["note_template"] = self.note_template.toPlainText().strip()
        self.config.save()
        self.accept()
