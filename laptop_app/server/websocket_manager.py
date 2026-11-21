"""WebSocket connection and message protocol handler."""
import json
import logging
from fastapi import WebSocket, WebSocketDisconnect
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager
from laptop_app.models.schemas import CameraState

logger = logging.getLogger("WebSocketManager")


class WebSocketManager:
    async def handle_connection(self, websocket: WebSocket):
        await websocket.accept()
        registered_camera_id = None
        client_host = websocket.client.host if websocket.client else "unknown"

        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "Invalid JSON format"})
                    continue

                msg_type = msg.get("type")

                # 1. Device Registration
                if msg_type == "device_register":
                    registered_camera_id = msg.get("camera_id")
                    device_name = msg.get("device_name", "Android Camera")
                    battery_level = msg.get("battery_level")
                    sensor_info = msg.get("sensor_info", {})

                    # --- Unique device identity fields (new) ---
                    device_id    = msg.get("device_id")       # UUID4 generated on phone
                    manufacturer = msg.get("manufacturer")
                    model        = msg.get("model")
                    os_version   = msg.get("os_version")
                    mac_address  = msg.get("mac_address")     # May be randomised; informational

                    if not registered_camera_id:
                        await websocket.send_json({"type": "error", "message": "Missing camera_id"})
                        continue

                    if not device_id:
                        # Older client / client without device_id support — still accepted
                        logger.warning(
                            f"[WS] camera_id='{registered_camera_id}' from {client_host} "
                            f"did not send a device_id. Registration accepted without hardware identity."
                        )
                    else:
                        # Warn if a DIFFERENT physical phone is trying to claim the same camera_id slot
                        existing = device_manager.get_device(registered_camera_id)
                        if (existing is not None
                                and existing.device_id is not None
                                and existing.device_id != device_id
                                and existing.client_ws is not None
                                and existing.client_ws is not websocket):
                            logger.warning(
                                f"[WS] camera_id='{registered_camera_id}' is currently held by "
                                f"device_id='{existing.device_id}' ({existing.ip_address}). "
                                f"New device_id='{device_id}' ({client_host}) is claiming it."
                            )

                    await device_manager.register_device(
                        camera_id=registered_camera_id,
                        device_name=device_name,
                        ip_address=client_host,
                        battery_level=battery_level,
                        sensor_info=sensor_info,
                        ws=websocket,
                        device_id=device_id,
                        manufacturer=manufacturer,
                        model=model,
                        os_version=os_version,
                        mac_address=mac_address,
                    )
                    await websocket.send_json({
                        "type": "registration_ack",
                        "camera_id": registered_camera_id,
                        "device_id": device_id,
                        "status": "ACCEPTED"
                    })

                # 2. Heartbeat / Device Status
                elif msg_type == "device_status":
                    cam_id = msg.get("camera_id", registered_camera_id)
                    state_str = msg.get("state")
                    state = CameraState(state_str) if state_str in CameraState.__members__ else None
                    battery = msg.get("battery_level")
                    if cam_id:
                        await device_manager.update_status(cam_id, state=state, battery_level=battery)
                        await websocket.send_json({"type": "status_ack"})

                # 3. Capture started event
                elif msg_type == "capture_started":
                    cam_id = msg.get("camera_id", registered_camera_id)
                    if cam_id:
                        await device_manager.update_status(cam_id, state=CameraState.CAPTURING)

                # 4. Capture completed event
                elif msg_type == "capture_completed":
                    session_id = msg.get("session_id")
                    cam_id = msg.get("camera_id", registered_camera_id)
                    timestamp_ms = msg.get("timestamp_ms")
                    if session_id and cam_id:
                        await session_manager.record_capture_event(
                            session_id=session_id,
                            camera_id=cam_id,
                            success=True,
                            timestamp_ms=timestamp_ms
                        )
                        await device_manager.update_status(cam_id, state=CameraState.UPLOADING)

                # 5. Capture failed event
                elif msg_type == "capture_failed":
                    session_id = msg.get("session_id")
                    cam_id = msg.get("camera_id", registered_camera_id)
                    error_msg = msg.get("error_message", "Unknown capture error")
                    if session_id and cam_id:
                        await session_manager.record_capture_event(
                            session_id=session_id,
                            camera_id=cam_id,
                            success=False,
                            error_msg=error_msg
                        )
                        await device_manager.update_status(cam_id, state=CameraState.ERROR)

        except WebSocketDisconnect:
            if registered_camera_id:
                await device_manager.unregister_device(registered_camera_id)
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            if registered_camera_id:
                await device_manager.unregister_device(registered_camera_id)


ws_manager = WebSocketManager()
