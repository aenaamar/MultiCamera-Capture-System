"""PySide6 Modern Card Widget for displaying camera status."""
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from laptop_app.models.schemas import CameraDevice, CameraState

STATE_COLORS = {
    CameraState.CONNECTED: "#2196F3",
    CameraState.READY: "#4CAF50",
    CameraState.CAPTURING: "#FF9800",
    CameraState.UPLOADING: "#9C27B0",
    CameraState.ERROR: "#F44336",
    CameraState.DISCONNECTED: "#9E9E9E",
}


def _short_device_id(device_id: str | None) -> str:
    """Return last 8 chars of device_id for compact display, or '--' if absent."""
    if not device_id:
        return "--"
    return device_id[-8:] if len(device_id) > 8 else device_id


class CameraCard(QFrame):
    def __init__(self, device: CameraDevice, parent=None):
        super().__init__(parent)
        self.camera_id = device.camera_id
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            CameraCard {
                background-color: #23272e;
                border-radius: 8px;
                border: 1px solid #3e4451;
                padding: 12px;
            }
        """)
        self.setFixedHeight(140)
        self._init_ui(device)

    def _init_ui(self, device: CameraDevice):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Header: Camera ID (slot) & State Badge
        header_layout = QHBoxLayout()
        self.title_label = QLabel(device.camera_id)
        font = QFont("Segoe UI", 11, QFont.Bold)
        self.title_label.setFont(font)
        self.title_label.setStyleSheet("color: #abb2bf;")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.badge_label = QLabel(device.state.value)
        self.badge_label.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self._update_badge(device.state)
        header_layout.addWidget(self.badge_label)
        layout.addLayout(header_layout)

        # Device model line  (e.g. "Samsung Galaxy S22")
        model_str = _build_model_str(device)
        self.device_name_label = QLabel(model_str)
        self.device_name_label.setStyleSheet("color: #98c379; font-size: 11px;")
        layout.addWidget(self.device_name_label)

        # Hardware UUID (truncated)
        uid_str = f"ID: …{_short_device_id(device.device_id)}"
        self.device_id_label = QLabel(uid_str)
        self.device_id_label.setStyleSheet("color: #56b6c2; font-size: 10px; font-family: Consolas;")
        layout.addWidget(self.device_id_label)

        # OS version (if available)
        os_str = f"OS: {device.os_version}" if device.os_version else ""
        self.os_label = QLabel(os_str)
        self.os_label.setStyleSheet("color: #5c6370; font-size: 10px;")
        layout.addWidget(self.os_label)

        # Battery & IP row
        sub_layout = QHBoxLayout()
        batt_str = f"🔋 {device.battery_level}%" if device.battery_level is not None else "🔋 --%"
        self.battery_label = QLabel(batt_str)
        self.battery_label.setStyleSheet("color: #e5c07b; font-size: 11px;")
        sub_layout.addWidget(self.battery_label)

        self.ip_label = QLabel(f"IP: {device.ip_address}")
        self.ip_label.setStyleSheet("color: #5c6370; font-size: 11px;")
        sub_layout.addWidget(self.ip_label)
        sub_layout.addStretch()
        layout.addLayout(sub_layout)

    def _update_badge(self, state: CameraState):
        color = STATE_COLORS.get(state, "#9E9E9E")
        self.badge_label.setText(f" {state.value} ")
        self.badge_label.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                color: #ffffff;
                border-radius: 4px;
                padding: 2px 6px;
            }}
        """)

    def update_device(self, device: CameraDevice):
        self._update_badge(device.state)
        batt_str = f"🔋 {device.battery_level}%" if device.battery_level is not None else "🔋 --%"
        self.battery_label.setText(batt_str)
        self.ip_label.setText(f"IP: {device.ip_address}")
        self.device_name_label.setText(_build_model_str(device))
        self.device_id_label.setText(f"ID: …{_short_device_id(device.device_id)}")
        if device.os_version:
            self.os_label.setText(f"OS: {device.os_version}")


def _build_model_str(device: CameraDevice) -> str:
    """Build a human-readable device description from available fields."""
    parts = []
    if device.manufacturer:
        parts.append(device.manufacturer)
    if device.model:
        parts.append(device.model)
    if parts:
        return " ".join(parts)
    return device.device_name or "Android Camera"
