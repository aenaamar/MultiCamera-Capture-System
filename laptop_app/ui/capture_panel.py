"""Capture control and real-time progress panel widget."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar, QTextEdit
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

class CapturePanel(QWidget):
    capture_clicked = Signal()
    new_session_clicked = Signal()
    open_folder_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Action Buttons Layout
        btn_layout = QHBoxLayout()

        self.btn_capture = QPushButton("📸 Capture All")
        self.btn_capture.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.btn_capture.setStyleSheet("""
            QPushButton {
                background-color: #61afef;
                color: #282c34;
                border-radius: 6px;
                padding: 10px 18px;
            }
            QPushButton:hover {
                background-color: #528bcc;
            }
            QPushButton:disabled {
                background-color: #4b5263;
                color: #abb2bf;
            }
        """)
        self.btn_capture.clicked.connect(self.capture_clicked.emit)
        btn_layout.addWidget(self.btn_capture)

        self.btn_new_session = QPushButton("📁 New Session")
        self.btn_new_session.setFont(QFont("Segoe UI", 10))
        self.btn_new_session.setStyleSheet("""
            QPushButton {
                background-color: #3e4451;
                color: #ffffff;
                border-radius: 6px;
                padding: 10px 14px;
            }
            QPushButton:hover {
                background-color: #4b5263;
            }
        """)
        self.btn_new_session.clicked.connect(self.new_session_clicked.emit)
        btn_layout.addWidget(self.btn_new_session)

        self.btn_open_folder = QPushButton("📂 Open Dataset Folder")
        self.btn_open_folder.setFont(QFont("Segoe UI", 10))
        self.btn_open_folder.setStyleSheet("""
            QPushButton {
                background-color: #3e4451;
                color: #ffffff;
                border-radius: 6px;
                padding: 10px 14px;
            }
            QPushButton:hover {
                background-color: #4b5263;
            }
        """)
        self.btn_open_folder.clicked.connect(self.open_folder_clicked.emit)
        btn_layout.addWidget(self.btn_open_folder)

        layout.addLayout(btn_layout)

        # Session Status & Metrics
        stats_layout = QHBoxLayout()
        self.lbl_session = QLabel("Session: None")
        self.lbl_session.setStyleSheet("color: #e5c07b; font-weight: bold; font-size: 13px;")
        stats_layout.addWidget(self.lbl_session)

        stats_layout.addStretch()

        self.lbl_progress_counts = QLabel("Captures: 0/0 | Uploads: 0/0")
        self.lbl_progress_counts.setStyleSheet("color: #abb2bf; font-size: 12px;")
        stats_layout.addWidget(self.lbl_progress_counts)

        layout.addLayout(stats_layout)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #3e4451;
                border-radius: 4px;
                text-align: center;
                background-color: #21252b;
                color: #abb2bf;
                height: 18px;
            }
            QProgressBar::chunk {
                background-color: #98c379;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress_bar)

        # Activity & Log Box
        lbl_log = QLabel("Capture & Verification Log")
        lbl_log.setStyleSheet("color: #5c6370; font-size: 11px;")
        layout.addWidget(lbl_log)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("""
            QTextEdit {
                background-color: #1e2227;
                color: #abb2bf;
                border: 1px solid #3e4451;
                border-radius: 4px;
                font-family: Consolas, monospace;
                font-size: 11px;
            }
        """)
        self.log_box.setMaximumHeight(140)
        layout.addWidget(self.log_box)

    def log_message(self, msg: str):
        self.log_box.append(msg)

    def update_progress(self, session_id: str, captured: int, uploaded: int, total: int):
        self.lbl_session.setText(f"Session: {session_id}")
        self.lbl_progress_counts.setText(f"Captures: {captured}/{total} | Verified Uploads: {uploaded}/{total}")
        if total > 0:
            pct = int((uploaded / total) * 100)
            self.progress_bar.setValue(pct)
        else:
            self.progress_bar.setValue(0)
