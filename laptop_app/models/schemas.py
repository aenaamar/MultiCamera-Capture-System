"""Data models for Camera devices and Capture Sessions."""
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class CameraState(str, Enum):
    CONNECTED = "CONNECTED"
    READY = "READY"
    CAPTURING = "CAPTURING"
    UPLOADING = "UPLOADING"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"

class CameraDevice(BaseModel):
    # Slot name used for file naming and capture dispatch (user-configured, e.g. "camera_01")
    camera_id: str
    # Hardware-level unique identity — UUID4 generated on the phone, persisted in SharedPreferences.
    # Survives reconnects; two phones of the same model always have different device_ids.
    device_id: Optional[str] = None
    device_name: str
    # Hardware details reported by the phone
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    os_version: Optional[str] = None
    mac_address: Optional[str] = None      # May be randomised; informational only
    ip_address: str = "0.0.0.0"
    battery_level: Optional[int] = None
    state: CameraState = CameraState.CONNECTED
    sensor_info: Dict[str, Any] = Field(default_factory=dict)
    registered_at: datetime = Field(default_factory=datetime.utcnow)
    last_heartbeat: datetime = Field(default_factory=datetime.utcnow)
    client_ws: Any = Field(default=None, exclude=True)

    class Config:
        arbitrary_types_allowed = True

class CaptureStatus(str, Enum):
    IDLE = "IDLE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class CameraCaptureResult(BaseModel):
    camera_id: str
    capture_success: bool = False
    capture_timestamp_ms: Optional[int] = None
    upload_success: bool = False
    filename: Optional[str] = None
    file_size_bytes: int = 0
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    sha256_checksum: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class CaptureSession(BaseModel):
    session_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expected_cameras: List[str] = Field(default_factory=list)
    status: CaptureStatus = CaptureStatus.IDLE
    results: Dict[str, CameraCaptureResult] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)

    @property
    def is_fully_completed(self) -> bool:
        if not self.expected_cameras:
            return False
        for cam_id in self.expected_cameras:
            res = self.results.get(cam_id)
            if not res or not res.upload_success:
                return False
        return True
