"""PySide6 Modern Desktop Interface Main Window."""
import asyncio
import os
import subprocess
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QGridLayout, QMessageBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from laptop_app.config.settings import settings
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager
from laptop_app.services.capture_manager import capture_manager
from laptop_app.models.schemas import CameraState, CaptureStatus
from laptop_app.ui.camera_cards import CameraCard
from laptop_app.ui.capture_panel import CapturePanel

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Multi-Camera Capture System - 3D Gaussian Splatting Dataset Collector")
        self.resize(1000, 720)
        self.setStyleSheet("background-color: #1e2227; color: #abb2bf;")

        self.cards: dict[str, CameraCard] = {}
        self._init_ui()

        # Connect callbacks
        device_manager.add_listener(self._on_devices_updated)
        session_manager.add_listener(self._on_session_updated)

        # Polling fallback timer
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_ui_state)
        self.refresh_timer.start(1000)

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # 1. Top Dashboard Header
        top_bar = QHBoxLayout()
        title_box = QVBoxLayout()
        app_title = QLabel("Multi-Camera Capture Dashboard")
        app_title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        app_title.setStyleSheet("color: #61afef;")
        title_box.addWidget(app_title)

        sub_title = QLabel("High-Integrity Multi-Angle Dataset Acquisition for 3D Gaussian Splatting")
        sub_title.setStyleSheet("color: #5c6370; font-size: 11px;")
        title_box.addWidget(sub_title)
        top_bar.addLayout(title_box)

        top_bar.addStretch()

        # Server Status Info Card
        self.server_info_label = QLabel(
            f"Server: ONLINE\nHost: http://{settings.host}:{settings.port}\nWS: ws://{settings.host}:{settings.port}/ws"
        )
        self.server_info_label.setFont(QFont("Consolas", 10))
        self.server_info_label.setStyleSheet("""
            QLabel {
                background-color: #282c34;
                border: 1px solid #98c379;
                border-radius: 6px;
                padding: 8px 12px;
                color: #98c379;
            }
        """)
        top_bar.addWidget(self.server_info_label)
        main_layout.addLayout(top_bar)

        # 2. Camera Grid Area
        grid_header = QHBoxLayout()
        self.lbl_camera_count = QLabel("Connected Cameras (0 connected, 0 ready)")
        self.lbl_camera_count.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.lbl_camera_count.setStyleSheet("color: #d19a66;")
        grid_header.addWidget(self.lbl_camera_count)
        main_layout.addLayout(grid_header)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: 1px solid #3e4451; border-radius: 6px; background-color: #21252b; }")

        self.cards_container = QWidget()
        self.cards_grid = QGridLayout(self.cards_container)
        self.cards_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.cards_grid.setSpacing(10)
        scroll_area.setWidget(self.cards_container)
        main_layout.addWidget(scroll_area, stretch=1)

        # 3. Capture Controls & Progress Panel
        self.capture_panel = CapturePanel()
        self.capture_panel.capture_clicked.connect(self._on_capture_all_clicked)
        self.capture_panel.new_session_clicked.connect(self._on_new_session_clicked)
        self.capture_panel.open_folder_clicked.connect(self._on_open_folder_clicked)
        main_layout.addWidget(self.capture_panel)

    def _on_capture_all_clicked(self):
        ready = device_manager.get_ready_devices()
        if not ready:
            QMessageBox.warning(self, "No Cameras Ready", "Cannot capture: No Android cameras are currently connected and ready.")
            return
        self.capture_panel.log_message(f"Initiating capture across {len(ready)} cameras...")
        asyncio.create_task(capture_manager.trigger_capture_all())

    def _on_new_session_clicked(self):
        ready = device_manager.get_ready_devices()
        cams = [d.camera_id for d in ready]
        asyncio.create_task(session_manager.create_session(cams))
        self.capture_panel.log_message(f"Created new session with {len(cams)} cameras.")

    def _on_open_folder_clicked(self):
        folder = settings.dataset_dir
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def _on_devices_updated(self):
        QTimer.singleShot(0, self._refresh_ui_state)

    def _on_session_updated(self):
        QTimer.singleShot(0, self._refresh_ui_state)

    def _refresh_ui_state(self):
        devices = device_manager.get_all_devices()
        ready_devices = device_manager.get_ready_devices()
        self.lbl_camera_count.setText(f"Connected Cameras ({len(devices)} connected, {len(ready_devices)} ready)")

        # Synchronize Camera Cards
        current_cam_ids = {d.camera_id for d in devices}
        # Remove cards no longer present
        for cam_id in list(self.cards.keys()):
            if cam_id not in current_cam_ids:
                widget = self.cards.pop(cam_id)
                self.cards_grid.removeWidget(widget)
                widget.deleteLater()

        # Add or update
        for idx, dev in enumerate(sorted(devices, key=lambda d: d.camera_id)):
            if dev.camera_id not in self.cards:
                card = CameraCard(dev)
                self.cards[dev.camera_id] = card
                row = idx // 4
                col = idx % 4
                self.cards_grid.addWidget(card, row, col)
            else:
                self.cards[dev.camera_id].update_device(dev)

        # Update Session Panel
        sess = session_manager.current_session
        if sess:
            total = len(sess.expected_cameras)
            captured = sum(1 for r in sess.results.values() if r.capture_success)
            uploaded = sum(1 for r in sess.results.values() if r.upload_success)
            self.capture_panel.update_progress(sess.session_id, captured, uploaded, total)
