"""Device Manager service for tracking connected smartphones."""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional, Callable, List
from laptop_app.models.schemas import CameraDevice, CameraState
from laptop_app.config.settings import settings

logger = logging.getLogger("DeviceManager")


class DeviceManager:
    def __init__(self):
        # Primary index: camera_id (slot name) -> CameraDevice
        self._devices: Dict[str, CameraDevice] = {}
        # Secondary index: device_id (hardware UUID) -> camera_id
        # Lets us quickly detect when the same physical phone reconnects.
        self._device_id_index: Dict[str, str] = {}
        self._listeners: List[Callable[[], None]] = []
        # Lazy lock: created on first use inside a running event loop.
        # Creating asyncio.Lock() at module import time (before any loop is
        # running) raises a DeprecationWarning on Python 3.10 and breaks on
        # Python 3.12+.
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

    async def register_device(
        self,
        camera_id: str,
        device_name: str,
        ip_address: str,
        battery_level: Optional[int],
        sensor_info: dict,
        ws: any,
        # New identity fields — all optional for backward compatibility
        device_id: Optional[str] = None,
        manufacturer: Optional[str] = None,
        model: Optional[str] = None,
        os_version: Optional[str] = None,
        mac_address: Optional[str] = None,
    ) -> CameraDevice:
        """
        Register or update a device.

        Rules for device_id-aware deduplication:
        1. If device_id is known and already registered under a DIFFERENT camera_id:
           - Log a warning (phone changed its camera_id slot) and clean up the old slot.
        2. If camera_id is already occupied by a DIFFERENT device_id:
           - The old device is replaced (new physical phone claimed the same slot).
           - Close the old WebSocket.
        3. If camera_id is already occupied by the SAME device_id:
           - Normal reconnect; just update the websocket and status.
        """
        old_ws_to_close = None
        old_camera_id_to_remove = None

        async with self._get_lock():
            # --- Rule 1: same device_id, different camera_id ---
            if device_id and device_id in self._device_id_index:
                prev_cam_id = self._device_id_index[device_id]
                if prev_cam_id != camera_id:
                    logger.warning(
                        f"[DeviceManager] device_id='{device_id}' previously registered as "
                        f"'{prev_cam_id}', now re-registering as '{camera_id}'. "
                        f"Removing old slot."
                    )
                    old_prev_device = self._devices.get(prev_cam_id)
                    if old_prev_device and old_prev_device.client_ws is not None:
                        old_ws_to_close = old_prev_device.client_ws
                    old_camera_id_to_remove = prev_cam_id

            # --- Rule 2 & 3: camera_id slot already occupied ---
            existing = self._devices.get(camera_id)
            if existing is not None:
                if existing.client_ws is not None and existing.client_ws is not ws:
                    if device_id and existing.device_id and existing.device_id != device_id:
                        # Different physical phone is taking this slot — evict the old one
                        logger.warning(
                            f"[DeviceManager] camera_id='{camera_id}' was held by "
                            f"device_id='{existing.device_id}' ({existing.ip_address}). "
                            f"New device_id='{device_id}' ({ip_address}) is claiming it. "
                            f"Closing old connection."
                        )
                        # Remove the displaced device's device_id from the secondary index
                        self._device_id_index.pop(existing.device_id, None)
                    else:
                        # Same device reconnecting (or no device_id)
                        logger.info(
                            f"[DeviceManager] camera_id='{camera_id}' reconnecting "
                            f"from {ip_address}. Closing previous WebSocket."
                        )
                    # Only replace old_ws_to_close if not already set by Rule 1
                    if old_ws_to_close is None:
                        old_ws_to_close = existing.client_ws

            # Build the updated device record
            device = CameraDevice(
                camera_id=camera_id,
                device_id=device_id,
                device_name=device_name,
                manufacturer=manufacturer,
                model=model,
                os_version=os_version,
                mac_address=mac_address,
                ip_address=ip_address,
                battery_level=battery_level,
                state=CameraState.READY,
                sensor_info=sensor_info,
                registered_at=datetime.utcnow(),
                last_heartbeat=datetime.utcnow(),
                client_ws=ws
            )

            # Commit to both indexes
            if old_camera_id_to_remove and old_camera_id_to_remove in self._devices:
                del self._devices[old_camera_id_to_remove]
                # Remove old device_id mapping for that slot if it matches
                old_dev = self._devices.get(old_camera_id_to_remove)
                if old_dev and old_dev.device_id:
                    self._device_id_index.pop(old_dev.device_id, None)

            self._devices[camera_id] = device
            if device_id:
                self._device_id_index[device_id] = camera_id

        # I/O outside the lock
        if old_ws_to_close is not None:
            try:
                await old_ws_to_close.close()
            except Exception as e:
                logger.debug(f"[DeviceManager] Error closing old WebSocket: {e}")

        log_line = (
            f"[DeviceManager] Registered camera_id='{camera_id}' "
            f"device_id='{device_id}' manufacturer='{manufacturer}' "
            f"model='{model}' ip={ip_address}"
        )
        logger.info(log_line)
        self._notify()
        return device

    async def update_status(
        self,
        camera_id: str,
        state: Optional[CameraState] = None,
        battery_level: Optional[int] = None
    ) -> Optional[CameraDevice]:
        async with self._get_lock():
            device = self._devices.get(camera_id)
            if device:
                if state:
                    device.state = state
                if battery_level is not None:
                    device.battery_level = battery_level
                device.last_heartbeat = datetime.utcnow()
        self._notify()
        return device

    async def unregister_device(self, camera_id: str):
        async with self._get_lock():
            if camera_id in self._devices:
                dev = self._devices[camera_id]
                dev.state = CameraState.DISCONNECTED
                dev.client_ws = None
                # Remove from device_id index (device is no longer active)
                if dev.device_id:
                    self._device_id_index.pop(dev.device_id, None)
        self._notify()

    def get_device(self, camera_id: str) -> Optional[CameraDevice]:
        return self._devices.get(camera_id)

    def get_device_by_device_id(self, device_id: str) -> Optional[CameraDevice]:
        """Look up a device by its hardware UUID rather than its slot name."""
        cam_id = self._device_id_index.get(device_id)
        if cam_id:
            return self._devices.get(cam_id)
        return None

    def get_all_devices(self) -> List[CameraDevice]:
        return list(self._devices.values())

    def get_ready_devices(self) -> List[CameraDevice]:
        return [
            d for d in self._devices.values()
            if d.state in (CameraState.READY, CameraState.CONNECTED)
        ]


device_manager = DeviceManager()
