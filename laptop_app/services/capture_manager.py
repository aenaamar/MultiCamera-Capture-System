"""Capture Manager for dispatching multi-camera commands and coordinating execution."""
import asyncio
import time
from typing import Optional, Dict, Any
from laptop_app.models.schemas import CaptureStatus, CameraState
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager

class CaptureManager:
    def __init__(self):
        self._is_capturing = False

    async def trigger_capture_all(self) -> Dict[str, Any]:
        """
        Coordinates multi-camera capture:
        1. Identifies ready cameras.
        2. Reuses existing IDLE current_session (e.g. from 'New Session') or creates one.
        3. Sends capture_request to each phone over WebSockets with the correct session_id.
        """
        if self._is_capturing:
            return {"status": "error", "message": "A capture session is currently in progress."}

        ready_devices = device_manager.get_ready_devices()
        if not ready_devices:
            return {"status": "error", "message": "No cameras connected and ready."}

        self._is_capturing = True
        expected_camera_ids = [d.camera_id for d in ready_devices]
        
        # Check if an IDLE session already exists (e.g., user clicked "New Session" first)
        curr = session_manager.current_session
        if curr and curr.status == CaptureStatus.IDLE:
            session = curr
            # Ensure all currently ready cameras are included in expected_cameras
            for cid in expected_camera_ids:
                if cid not in session.expected_cameras:
                    session.expected_cameras.append(cid)
                if cid not in session.results:
                    from laptop_app.models.schemas import CameraCaptureResult
                    session.results[cid] = CameraCaptureResult(camera_id=cid)
        else:
            session = await session_manager.create_session(expected_camera_ids)

        session.status = CaptureStatus.IN_PROGRESS
        
        # Broadcast capture command to each ready device
        timestamp_ms = int(time.time() * 1000)
        capture_tasks = []

        for dev in ready_devices:
            req_id = f"req_{session.session_id}_{dev.camera_id}_{timestamp_ms}"
            msg = {
                "type": "capture_request",
                "session_id": session.session_id,
                "capture_request_id": req_id,
                "camera_id": dev.camera_id,
                "timestamp_ms": timestamp_ms
            }
            dev.state = CameraState.CAPTURING
            capture_tasks.append(self._send_command(dev.client_ws, msg, dev.camera_id, session.session_id))

        await asyncio.gather(*capture_tasks, return_exceptions=True)
        self._is_capturing = False

        return {
            "status": "success",
            "session_id": session.session_id,
            "cameras_triggered": expected_camera_ids
        }

    async def _send_command(self, ws, msg: dict, camera_id: str, session_id: str):
        try:
            if ws:
                await ws.send_json(msg)
        except Exception as e:
            await session_manager.record_capture_event(
                session_id=session_id,
                camera_id=camera_id,
                success=False,
                error_msg=f"Failed to dispatch capture command via WebSocket: {str(e)}"
            )

capture_manager = CaptureManager()
