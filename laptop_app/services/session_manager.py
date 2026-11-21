"""Session Manager service for tracking active and past capture sessions."""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Callable
from laptop_app.models.schemas import CaptureSession, CaptureStatus, CameraCaptureResult
from laptop_app.config.settings import settings
from laptop_app.utils.metadata_utils import write_session_metadata

class SessionManager:
    def __init__(self, dataset_dir: Optional[Path] = None):
        self.dataset_dir = dataset_dir or settings.dataset_dir
        self.current_session: Optional[CaptureSession] = None
        self._sessions: Dict[str, CaptureSession] = {}
        self._listeners: List[Callable[[], None]] = []
        # Lazy lock: created on first use inside a running event loop.
        # Creating asyncio.Lock() at module import time (before any loop is
        # running) raises DeprecationWarning on Python 3.10 and breaks on 3.12+.
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def add_listener(self, callback: Callable[[], None]):
        self._listeners.append(callback)

    def _notify(self):
        for cb in self._listeners:
            try:
                cb()
            except Exception:
                pass

    def get_next_session_id(self) -> str:
        """Scan dataset folder to determine the next session ID (e.g., Session_001)."""
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        existing = [
            p.name for p in self.dataset_dir.iterdir()
            if p.is_dir() and p.name.startswith("Session_")
        ]
        numbers = []
        for name in existing:
            parts = name.split("_")
            if len(parts) >= 2 and parts[1].isdigit():
                numbers.append(int(parts[1]))
        next_num = max(numbers, default=0) + 1
        return f"Session_{next_num:03d}"

    async def create_session(self, expected_cameras: List[str]) -> CaptureSession:
        async with self._get_lock():
            session_id = self.get_next_session_id()
            session = CaptureSession(
                session_id=session_id,
                created_at=datetime.utcnow(),
                expected_cameras=expected_cameras,
                status=CaptureStatus.IDLE
            )
            for cam in expected_cameras:
                session.results[cam] = CameraCaptureResult(camera_id=cam)
            
            self.current_session = session
            self._sessions[session_id] = session
            
            # Create session folder and staging area
            session_dir = self.dataset_dir / session_id
            (session_dir / "images").mkdir(parents=True, exist_ok=True)
            (session_dir / "metadata").mkdir(parents=True, exist_ok=True)
            (session_dir / ".staging").mkdir(parents=True, exist_ok=True)
            
            # Initial metadata
            write_session_metadata(session_dir, session)
            
        self._notify()
        return session

    async def record_capture_event(
        self,
        session_id: str,
        camera_id: str,
        success: bool,
        timestamp_ms: Optional[int] = None,
        error_msg: Optional[str] = None
    ):
        async with self._get_lock():
            session = self._sessions.get(session_id)
            if not session or camera_id not in session.results:
                return
            res = session.results[camera_id]
            res.capture_success = success
            res.capture_timestamp_ms = timestamp_ms
            if error_msg:
                res.error_message = error_msg
                session.errors.append(f"[{camera_id}] Capture failed: {error_msg}")
        self._notify()

    async def record_upload_event(
        self,
        session_id: str,
        camera_id: str,
        success: bool,
        filename: Optional[str] = None,
        file_size: int = 0,
        width: Optional[int] = None,
        height: Optional[int] = None,
        sha256: Optional[str] = None,
        metadata: Optional[dict] = None,
        error_msg: Optional[str] = None
    ):
        async with self._get_lock():
            session = self._sessions.get(session_id)
            if not session or camera_id not in session.results:
                return
            res = session.results[camera_id]
            res.upload_success = success
            if success:
                res.filename = filename
                res.file_size_bytes = file_size
                res.image_width = width
                res.image_height = height
                res.sha256_checksum = sha256
                if metadata:
                    res.metadata = metadata
            else:
                if error_msg:
                    res.error_message = error_msg
                    session.errors.append(f"[{camera_id}] Upload failed: {error_msg}")
            
            # Check session completion
            if session.is_fully_completed:
                session.status = CaptureStatus.COMPLETED
            elif all(
                session.results[c].upload_success or (session.results[c].error_message is not None)
                for c in session.expected_cameras
            ):
                session.status = CaptureStatus.FAILED

            # Write updated manifest and capture_info.json
            session_dir = self.dataset_dir / session_id
            write_session_metadata(session_dir, session)

        self._notify()

session_manager = SessionManager()
