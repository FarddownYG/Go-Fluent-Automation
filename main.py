"""PyQt6 desktop interface for the GoFluent activity worker."""
from __future__ import annotations

import sys
import json
import time
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from browser import BrowserPreviewWorker, BrowserWorker, MonthlyRefreshWorker
from monthly_tracker import (
    load_snapshot, MONTHLY_GOAL, record_manual_refresh, refresh_cooldown_remaining,
)


class GoFluentApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GoFluent · Automatisation")
        self.resize(1060, 780)
        self.setMinimumSize(520, 560)
        self.worker = None
        self.preview_worker = None
        self.monthly_refresh_worker = None
        self._monthly_cooldown_until = 0.0
        self.requested_count = 0
        self._field_widgets = []
        self._field_grid = None
        self.settings = QSettings("GoFluent", "Automation")

        self.setStyleSheet("""
            QMainWindow, QWidget#root {
                background: #0B1020;
                color: #E7EAF2;
                font-family: 'Segoe UI';
                font-size: 13px;
            }
            QWidget { color: #E7EAF2; }
            QLabel { background: transparent; border: none; }
            QFrame#card {
                background: #121A2B;
                border: 1px solid #263249;
                border-radius: 14px;
            }
            QFrame#brand_mark {
                background: #6D7CFF;
                border: none;
                border-radius: 12px;
            }
            QLabel#brand_letters { color: #FFFFFF; font-size: 16px; font-weight: 800; }
            QLabel#title { color: #F7F8FC; font-size: 25px; font-weight: 750; }
            QLabel#subtitle { color: #929DB2; font-size: 13px; }
            QLabel#eyebrow { color: #9AA7C0; font-size: 11px; font-weight: 700; }
            QLabel#section_title { color: #F3F5FA; font-size: 16px; font-weight: 700; }
            QLabel#section_hint { color: #8793A8; font-size: 12px; }
            QLabel#field_label { color: #ADB7C9; font-size: 12px; font-weight: 600; }
            QLabel#activity_title { color: #F4F6FB; font-size: 18px; font-weight: 650; }
            QLabel#url_label { color: #79869C; font-size: 11px; }
            QLabel#progress_text { color: #AEB8CA; font-size: 12px; font-weight: 600; }
            QLabel#monthly_count { color: #E7EAF2; font-size: 14px; font-weight: 700; }
            QLabel#status {
                color: #A8B2C6;
                background: #202B3F;
                border: 1px solid #2E3C55;
                border-radius: 10px;
                padding: 6px 11px;
                min-height: 20px;
                font-size: 11px;
                font-weight: 750;
            }
            QLabel#status[state="success"] { color: #70E1B1; background: #12352F; border-color: #205B4D; }
            QLabel#status[state="running"] { color: #A9B5FF; background: #222B55; border-color: #39467B; }
            QLabel#status[state="warning"] { color: #FFD28A; background: #3B2F1B; border-color: #67502A; }
            QLabel#status[state="error"] { color: #FFA0A8; background: #3E222B; border-color: #69313D; }
            QPushButton {
                background: #6878F5;
                color: #FFFFFF;
                border: 1px solid #7584FF;
                border-radius: 9px;
                padding: 11px 18px;
                min-height: 20px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:hover { background: #7887FF; }
            QPushButton:pressed { background: #5968DE; }
            QPushButton:disabled { background: #293247; border-color: #303B52; color: #65718A; }
            QPushButton#stop {
                background: #2A1D2A;
                border-color: #593341;
                color: #F09AA5;
            }
            QPushButton#stop:hover { background: #3A222D; }
            QPushButton#stop:disabled { background: #202737; border-color: #303B52; color: #68748A; }
            QPushButton#clear_log {
                background: transparent;
                color: #99A6BB;
                border: 1px solid #344159;
                padding: 7px 12px;
                min-height: 16px;
                font-size: 11px;
            }
            QPushButton#clear_log:hover { background: #1B2639; color: #E8ECF4; }
            QPushButton#monthly_refresh {
                background: transparent; color: #AAB6CC; border: 1px solid #344159;
                padding: 6px 10px; min-height: 14px; font-size: 11px;
            }
            QPushButton#monthly_refresh:hover { background: #1B2639; color: #FFFFFF; }
            QPushButton#monthly_refresh:disabled { background: #202737; border-color: #303B52; color: #68748A; }
            QComboBox, QSpinBox {
                background: #0D1423;
                color: #E7EAF2;
                border: 1px solid #344159;
                border-radius: 8px;
                padding: 9px 11px;
                min-height: 22px;
                selection-background-color: #6878F5;
            }
            QComboBox:hover, QSpinBox:hover { border-color: #6475A0; }
            QComboBox:focus, QSpinBox:focus { border: 1px solid #7887FF; }
            QComboBox::drop-down { border: none; width: 28px; }
            QComboBox QAbstractItemView {
                background: #121A2B;
                color: #E7EAF2;
                border: 1px solid #344159;
                selection-background-color: #344477;
                padding: 5px;
            }
            QProgressBar {
                background: #202A3B;
                border: none;
                border-radius: 5px;
                min-height: 10px;
                max-height: 10px;
                text-align: center;
            }
            QProgressBar::chunk { background: #7180FF; border-radius: 5px; }
            QProgressBar#monthly_bar { min-height: 8px; max-height: 8px; }
            QProgressBar#monthly_bar::chunk { background: #44C99A; border-radius: 4px; }
            QPlainTextEdit {
                background: #0A0F1A;
                color: #B7C2D6;
                border: 1px solid #263249;
                border-radius: 9px;
                padding: 12px;
                font-family: Consolas, 'Cascadia Mono';
                font-size: 11px;
                selection-background-color: #344477;
            }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 4px; }
            QScrollBar::handle:vertical { background: #35425A; border-radius: 4px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        self.build_ui()
        self._monthly_cooldown_timer = QTimer(self)
        self._monthly_cooldown_timer.setInterval(1000)
        self._monthly_cooldown_timer.timeout.connect(self.update_monthly_refresh_button)
        self._monthly_cooldown_until = time.monotonic() + refresh_cooldown_remaining()
        self.update_monthly_snapshot(load_snapshot())
        self.update_monthly_refresh_button()
        self.log("Application prête. Configure les activités puis démarre le navigateur.")

    @staticmethod
    def make_card() -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(15)
        return card, layout

    @staticmethod
    def make_field(label_text: str, widget: QWidget) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        label = QLabel(label_text)
        label.setObjectName("field_label")
        layout.addWidget(label)
        layout.addWidget(widget)
        return wrapper

    def build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: #0B1020; border: none; }")

        root = QWidget()
        root.setObjectName("root")
        root.setMinimumWidth(0)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        self.root_widget = root

        page = QVBoxLayout(root)
        page.setContentsMargins(30, 25, 30, 25)
        page.setSpacing(17)

        # Header
        header = QHBoxLayout()
        header.setSpacing(14)
        mark = QFrame()
        mark.setObjectName("brand_mark")
        mark.setFixedSize(46, 46)
        mark_layout = QVBoxLayout(mark)
        mark_layout.setContentsMargins(0, 0, 0, 0)
        letters = QLabel("GF")
        letters.setObjectName("brand_letters")
        letters.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark_layout.addWidget(letters)
        header.addWidget(mark)

        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("GoFluent Automation")
        title.setObjectName("title")
        subtitle = QLabel("Automatise les activités disponibles et suis chaque étape.")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch(1)
        self.connection_hint = QLabel("PLAYWRIGHT  ·  CHROMIUM")
        self.connection_hint.setObjectName("eyebrow")
        self.connection_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.connection_hint.setMinimumWidth(0)
        header.addWidget(self.connection_hint)
        page.addLayout(header)

        # Configuration
        config_card, config = self.make_card()
        section_heading = QVBoxLayout()
        section_heading.setSpacing(3)
        config_title = QLabel("Configuration")
        config_title.setObjectName("section_title")
        config_hint = QLabel("Choisis la plage de niveaux et le nombre d’activités à traiter.")
        config_hint.setObjectName("section_hint")
        section_heading.addWidget(config_title)
        section_heading.addWidget(config_hint)
        config.addLayout(section_heading)

        fields_widget = QWidget()
        fields_widget.setStyleSheet("background: transparent;")
        self._field_grid = QGridLayout(fields_widget)
        self._field_grid.setContentsMargins(0, 0, 0, 0)
        self._field_grid.setHorizontalSpacing(14)
        self._field_grid.setVerticalSpacing(12)
        self.min_level = QComboBox()
        self.min_level.addItems(["Tous", "Pre-A1", "A1", "A2", "B1", "B2", "C1", "C2"])
        self.min_level.setCurrentText("Tous")
        self.min_level.setMinimumWidth(0)
        self.min_level.setMinimumHeight(42)
        self.max_level = QComboBox()
        self.max_level.addItems(["Pre-A1", "A1", "A2", "B1", "B2", "C1", "C2"])
        self.max_level.setCurrentText("B2")
        self.max_level.setMinimumWidth(0)
        self.max_level.setMinimumHeight(42)
        self.activity_count = QSpinBox()
        self.activity_count.setRange(0, 100000)
        self.activity_count.setValue(12)
        self.activity_count.setSpecialValueText("Sans limite")
        self.activity_count.setMinimumWidth(0)
        self.activity_count.setMinimumHeight(42)
        self.activity_count.setToolTip("Saisis 0 pour parcourir toutes les activités traitables.")
        self.response_delay = QSpinBox()
        self.response_delay.setRange(0, 300)
        self.response_delay.setSuffix(" s")
        self.response_delay.setValue(int(self.settings.value("response_delay_seconds", 20)))
        self.response_delay.setMinimumWidth(0)
        self.response_delay.setMinimumHeight(42)
        self.response_delay.setToolTip("Temps d’écoute/réflexion après l’affichage de chaque question, avant la réponse. 0 désactive l’attente.")
        self.response_delay.valueChanged.connect(
            lambda value: self.settings.setValue("response_delay_seconds", value)
        )
        self._field_widgets = [
            self.make_field("NIVEAU MINIMUM", self.min_level),
            self.make_field("NIVEAU MAXIMUM", self.max_level),
            self.make_field("NOMBRE D’ACTIVITÉS", self.activity_count),
            self.make_field("TEMPS AVANT RÉPONSE", self.response_delay),
        ]
        for field in self._field_widgets:
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        config.addWidget(fields_widget)
        self._reflow_fields()

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.open_button = QPushButton("↗   Ouvrir GoFluent")
        self.open_button.setObjectName("clear_log")
        self.open_button.setMinimumWidth(135)
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.clicked.connect(self.toggle_browser_preview)
        self.start_button = QPushButton("▶   Démarrer")
        self.start_button.setMinimumWidth(130)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_button = QPushButton("■   Arrêter")
        self.stop_button.setObjectName("stop")
        self.stop_button.setMinimumWidth(105)
        self.stop_button.setEnabled(False)
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.clicked.connect(self.start_process)
        self.stop_button.clicked.connect(self.stop_process)
        actions.addWidget(self.open_button)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        config.addLayout(actions)
        page.addWidget(config_card)

        # Run status and actual completion progress
        progress_card, progress = self.make_card()
        progress_head = QHBoxLayout()
        progress_head.setSpacing(10)
        progress_title = QLabel("Suivi de la session")
        progress_title.setObjectName("section_title")
        progress_head.addWidget(progress_title)
        progress_head.addStretch(1)
        self.status_label = QLabel("●  En attente")
        self.status_label.setObjectName("status")
        self.status_label.setProperty("state", "warning")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setMinimumWidth(145)
        self.status_label.setMinimumHeight(34)
        progress_head.addWidget(self.status_label)
        progress.addLayout(progress_head)

        self.current_activity = QLabel("Aucune activité en cours")
        self.current_activity.setObjectName("activity_title")
        self.current_activity.setWordWrap(True)
        self.url_label = QLabel("Le navigateur s’ouvrira au démarrage.")
        self.url_label.setObjectName("url_label")
        self.url_label.setWordWrap(True)
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        progress.addWidget(self.current_activity)
        progress.addWidget(self.url_label)

        progress_line = QHBoxLayout()
        self.progress_text = QLabel("0 / 12 activités terminées")
        self.progress_text.setObjectName("progress_text")
        progress_line.addWidget(self.progress_text)
        progress_line.addStretch(1)
        self.percent_label = QLabel("0 %")
        self.percent_label.setObjectName("progress_text")
        progress_line.addWidget(self.percent_label)
        progress.addLayout(progress_line)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        progress.addWidget(self.progress_bar)

        monthly_head = QHBoxLayout()
        monthly_head.setSpacing(8)
        monthly_title = QLabel("Objectif GoFluent du mois")
        monthly_title.setObjectName("progress_text")
        monthly_head.addWidget(monthly_title)
        monthly_head.addStretch(1)
        self.monthly_count_label = QLabel(f"0 / {MONTHLY_GOAL} activité(s)")
        self.monthly_count_label.setObjectName("monthly_count")
        monthly_head.addWidget(self.monthly_count_label)
        self.monthly_refresh_button = QPushButton("↻  Rafraîchir")
        self.monthly_refresh_button.setObjectName("monthly_refresh")
        self.monthly_refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.monthly_refresh_button.setToolTip("Lire My Training · History. Limite : un rafraîchissement toutes les 2 minutes.")
        self.monthly_refresh_button.clicked.connect(self.refresh_monthly_count)
        monthly_head.addWidget(self.monthly_refresh_button)
        progress.addLayout(monthly_head)
        self.monthly_detail_label = QLabel("Historique My Training · History")
        self.monthly_detail_label.setObjectName("section_hint")
        self.monthly_detail_label.setWordWrap(True)
        progress.addWidget(self.monthly_detail_label)
        self.monthly_progress_bar = QProgressBar()
        self.monthly_progress_bar.setObjectName("monthly_bar")
        self.monthly_progress_bar.setRange(0, MONTHLY_GOAL)
        self.monthly_progress_bar.setValue(0)
        self.monthly_progress_bar.setTextVisible(False)
        progress.addWidget(self.monthly_progress_bar)
        page.addWidget(progress_card)

        # Live execution log
        log_card, log_layout = self.make_card()
        log_head = QHBoxLayout()
        log_title = QLabel("Journal d’exécution")
        log_title.setObjectName("section_title")
        log_hint = QLabel("Les actions et erreurs apparaissent ici.")
        log_hint.setObjectName("section_hint")
        log_head.addWidget(log_title)
        log_head.addSpacing(10)
        log_head.addWidget(log_hint)
        log_head.addStretch(1)
        clear_button = QPushButton("Effacer l’affichage")
        clear_button.setObjectName("clear_log")
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_button.clicked.connect(self.log_output_clear)
        log_head.addWidget(clear_button)
        log_layout.addLayout(log_head)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("En attente du lancement…")
        self.log_output.setMaximumBlockCount(3000)
        self.log_output.setMinimumHeight(170)
        self.log_output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        log_layout.addWidget(self.log_output, 1)
        page.addWidget(log_card, 1)

    def _reflow_fields(self):
        if self._field_grid is None or not self._field_widgets:
            return
        while self._field_grid.count():
            self._field_grid.takeAt(0)
        width = self.root_widget.width() if hasattr(self, "root_widget") else self.width()
        content_width = max(300, width - 100)
        columns = 3 if content_width >= 930 else 2 if content_width >= 600 else 1
        for index, field in enumerate(self._field_widgets):
            self._field_grid.addWidget(field, index // columns, index % columns)
        for column in range(columns):
            self._field_grid.setColumnStretch(column, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "root_widget"):
            self._reflow_fields()
        if hasattr(self, "connection_hint"):
            self.connection_hint.setVisible(self.width() >= 780)

    def log_output_clear(self):
        self.log_output.clear()
        self.log("Affichage du journal effacé. Les fichiers de log restent conservés.")

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"{timestamp}  {message}")

    def set_status(self, text: str, state: str):
        self.status_label.setText(f"●  {text}")
        self.status_label.setProperty("state", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def start_process(self):
        if self.monthly_refresh_worker is not None and self.monthly_refresh_worker.isRunning():
            self.log("Attends la fin du rafraîchissement de l’historique mensuel.")
            return
        if self.preview_worker is not None and self.preview_worker.isRunning():
            self.log("Ferme d’abord Chromium avec le bouton « Fermer Chromium ».")
            return
        if self.worker is not None and self.worker.isRunning():
            self.log("Le navigateur est déjà actif.")
            return

        self.requested_count = self.activity_count.value()
        min_level = self.min_level.currentText()
        max_level = self.max_level.currentText()
        level_order = {"Tous": 0, "Pre-A1": 0, "A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}
        if level_order[min_level] > level_order[max_level]:
            self.log("Configuration invalide : le niveau minimum dépasse le niveau maximum.")
            self.set_status("Configuration invalide", "error")
            return

        self.start_button.setEnabled(False)
        self.monthly_refresh_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.activity_count.setEnabled(False)
        self.response_delay.setEnabled(False)
        self.min_level.setEnabled(False)
        self.max_level.setEnabled(False)
        self.set_status("Démarrage", "running")
        self.current_activity.setText("Ouverture du navigateur…")
        self.url_label.setText("Connexion à GoFluent en cours.")
        self.progress_bar.setValue(0)
        self.percent_label.setText("0 %")
        self.monthly_detail_label.setText("Synchronisation depuis My Training · History en cours du traitement.")
        if self.requested_count == 0:
            self.progress_bar.setRange(0, 0)
            self.progress_text.setText("0 activité terminée · sans limite")
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_text.setText(f"0 / {self.requested_count} activités terminées")

        self.log("Démarrage de Playwright.")
        self.worker = BrowserWorker(self.requested_count, min_level, max_level, self.response_delay.value())
        self.worker.status_changed.connect(self.update_status)
        self.worker.log_message.connect(self.log)
        self.worker.page_changed.connect(self.update_url)
        self.worker.activity_found.connect(self.update_activity)
        self.worker.progress_changed.connect(self.update_progress)
        self.worker.monthly_snapshot_changed.connect(self.update_monthly_snapshot_json)
        self.worker.finished_signal.connect(self.process_finished)
        self.worker.start()

    def toggle_browser_preview(self):
        if self.monthly_refresh_worker is not None and self.monthly_refresh_worker.isRunning():
            self.log("Attends la fin du rafraîchissement mensuel avant d’ouvrir Chromium.")
            return
        if self.worker is not None and self.worker.isRunning():
            self.log("Arrête d’abord l’automatisation avant d’ouvrir Chromium.")
            return
        if self.preview_worker is not None and self.preview_worker.isRunning():
            self.open_button.setEnabled(False)
            self.preview_worker.stop()
            return

        self.open_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.monthly_refresh_button.setEnabled(False)
        self.activity_count.setEnabled(False)
        self.response_delay.setEnabled(False)
        self.min_level.setEnabled(False)
        self.max_level.setEnabled(False)
        self.set_status("Ouverture du navigateur", "running")
        self.current_activity.setText("Ouverture de GoFluent dans Chromium…")
        self.url_label.setText("Utilisation du profil Chromium existant.")
        self.log("Ouverture manuelle de GoFluent avec le profil de session existant.")

        self.preview_worker = BrowserPreviewWorker()
        self.preview_worker.status_changed.connect(self.update_status)
        self.preview_worker.log_message.connect(self.log)
        self.preview_worker.page_changed.connect(self.update_url)
        self.preview_worker.finished_signal.connect(self.preview_finished)
        self.open_button.setText("■   Fermer Chromium")
        self.open_button.setEnabled(True)
        self.preview_worker.start()

    def preview_finished(self):
        self.open_button.setText("↗   Ouvrir GoFluent")
        self.open_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.activity_count.setEnabled(True)
        self.response_delay.setEnabled(True)
        self.min_level.setEnabled(True)
        self.max_level.setEnabled(True)
        self.current_activity.setText("Navigateur GoFluent fermé.")
        self.log("Navigateur manuel fermé. Tu peux lancer l’automatisation.")
        if self.preview_worker is not None:
            self.preview_worker.deleteLater()
            self.preview_worker = None
        self.update_monthly_refresh_button()

    def stop_process(self):
        if self.worker is None or not self.worker.isRunning():
            self.log("Aucun navigateur actif.")
            return
        self.stop_button.setEnabled(False)
        self.set_status("Arrêt demandé", "warning")
        self.current_activity.setText("Arrêt en cours…")
        self.log("Demande d’arrêt envoyée.")
        self.worker.stop()

    def update_status(self, status):
        states = {
            "DÉMARRAGE": ("Démarrage", "running"),
            "CONNEXION REQUISE": ("Connexion requise", "warning"),
            "TERMINÉ": ("Terminé", "success"),
            "ARRÊT": ("Arrêté", "warning"),
            "ERREUR": ("Erreur", "error"),
        }
        text, state = states.get(status, (status.title(), "running"))
        self.set_status(text, state)
        if status == "CONNEXION REQUISE":
            self.current_activity.setText("Connecte-toi dans Chromium si nécessaire.")
        elif status == "ERREUR":
            self.current_activity.setText("Le traitement a rencontré une erreur.")
        elif status == "ARRÊT":
            self.current_activity.setText("Traitement arrêté à ta demande.")
        elif status == "TERMINÉ":
            self.current_activity.setText("Traitement terminé.")

    def update_activity(self, title, url):
        self.current_activity.setText(title)

    def update_url(self, url):
        self.url_label.setText(url)
        self.url_label.setToolTip(url)

    def update_progress(self, completed, requested):
        if requested == 0:
            self.progress_bar.setRange(0, 0)
            self.percent_label.setText("En cours")
            self.progress_text.setText(f"{completed} activité(s) terminée(s) · sans limite")
        else:
            self.progress_bar.setRange(0, 100)
            percent = round(100 * completed / requested)
            self.progress_bar.setValue(percent)
            self.percent_label.setText(f"{percent} %")
            self.progress_text.setText(f"{completed} / {requested} activités terminées")

    def update_monthly_snapshot_json(self, encoded):
        try:
            self.update_monthly_snapshot(json.loads(encoded))
        except (TypeError, json.JSONDecodeError):
            self.monthly_detail_label.setText("Historique mensuel reçu dans un format illisible.")

    def update_monthly_snapshot(self, snapshot):
        current_month = datetime.now().astimezone().strftime("%Y-%m")
        if not isinstance(snapshot, dict) or snapshot.get("month") != current_month:
            count = 0
            synced_at = None
        else:
            try:
                count = max(0, int(snapshot.get("count", 0)))
            except (TypeError, ValueError):
                count = 0
            synced_at = snapshot.get("synced_at")
        self.monthly_count_label.setText(f"{count} / {MONTHLY_GOAL} activité(s)")
        self.monthly_progress_bar.setValue(min(count, MONTHLY_GOAL))
        month_names = ["janvier", "février", "mars", "avril", "mai", "juin",
                       "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
        now = datetime.now().astimezone()
        month_label = f"{month_names[now.month - 1].capitalize()} {now.year}"
        if synced_at:
            try:
                sync_time = datetime.fromisoformat(synced_at).astimezone().strftime("%d/%m à %H:%M")
                self.monthly_detail_label.setText(
                    f"{month_label} · Synchronisé depuis GoFluent le {sync_time} · réussites distinctes"
                )
            except (ValueError, TypeError):
                self.monthly_detail_label.setText(f"{month_label} · Historique GoFluent synchronisé")
        else:
            self.monthly_detail_label.setText(f"{month_label} · Pas encore synchronisé avec GoFluent")
        self.monthly_progress_bar.setToolTip(
            f"{count} activité(s) réussie(s) distincte(s) sur {MONTHLY_GOAL} pour ce mois."
        )

    def refresh_monthly_count(self):
        if ((self.worker is not None and self.worker.isRunning())
                or (self.preview_worker is not None and self.preview_worker.isRunning())
                or (self.monthly_refresh_worker is not None and self.monthly_refresh_worker.isRunning())):
            self.log("Rafraîchissement refusé : un autre navigateur utilise le profil GoFluent.")
            return
        remaining = self._monthly_cooldown_until - time.monotonic()
        if remaining > 0:
            self.log(f"Rafraîchissement limité : réessaie dans {int(remaining + 0.99)} s.")
            return
        self._monthly_cooldown_until = time.monotonic() + 120
        try:
            record_manual_refresh()
        except OSError as exc:
            self.log(f"Limite de rafraîchissement non persistée : {exc}")
        self.monthly_refresh_button.setText("↻  Synchronisation…")
        self.monthly_refresh_button.setEnabled(False)
        self.monthly_detail_label.setText("Actualisation en cours depuis My Training · History.")
        self._monthly_cooldown_timer.start()
        self.log("Rafraîchissement manuel de l’objectif mensuel demandé.")
        self.monthly_refresh_worker = MonthlyRefreshWorker()
        self.monthly_refresh_worker.log_message.connect(self.log)
        self.monthly_refresh_worker.snapshot_changed.connect(self.update_monthly_snapshot_json)
        self.monthly_refresh_worker.result_changed.connect(self.monthly_refresh_finished)
        self.monthly_refresh_worker.finished.connect(self.monthly_refresh_thread_finished)
        self.monthly_refresh_worker.start()

    def monthly_refresh_finished(self, success, message):
        self.log(message)
        if not success:
            self.monthly_detail_label.setText("Échec de synchronisation · dernière valeur conservée.")

    def monthly_refresh_thread_finished(self):
        if self.monthly_refresh_worker is not None:
            self.monthly_refresh_worker.deleteLater()
            self.monthly_refresh_worker = None
        self.update_monthly_refresh_button()

    def update_monthly_refresh_button(self):
        remaining = max(0, int(self._monthly_cooldown_until - time.monotonic() + 0.99))
        busy = ((self.worker is not None and self.worker.isRunning())
                or (self.preview_worker is not None and self.preview_worker.isRunning())
                or (self.monthly_refresh_worker is not None and self.monthly_refresh_worker.isRunning()))
        if busy:
            if self.monthly_refresh_worker is not None and self.monthly_refresh_worker.isRunning():
                self.monthly_refresh_button.setText("↻  Synchronisation…")
            self.monthly_refresh_button.setEnabled(False)
            return
        if remaining:
            self.monthly_refresh_button.setText(f"↻  Rafraîchir ({remaining // 60}:{remaining % 60:02d})")
            self.monthly_refresh_button.setEnabled(False)
        else:
            self.monthly_refresh_button.setText("↻  Rafraîchir")
            self.monthly_refresh_button.setEnabled(True)
            self._monthly_cooldown_timer.stop()

    def process_finished(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.activity_count.setEnabled(True)
        self.response_delay.setEnabled(True)
        self.min_level.setEnabled(True)
        self.max_level.setEnabled(True)
        if self.requested_count == 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.percent_label.setText("—")
        self.log("Processus navigateur terminé.")
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        self.update_monthly_refresh_button()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.log("Arrêt du navigateur avant fermeture.")
            self.worker.stop()
            self.worker.wait()
        if self.preview_worker is not None and self.preview_worker.isRunning():
            self.preview_worker.stop()
            self.preview_worker.wait()
        if self.monthly_refresh_worker is not None and self.monthly_refresh_worker.isRunning():
            self.monthly_refresh_worker.wait()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = GoFluentApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
