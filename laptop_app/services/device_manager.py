"""Device Manager service for tracking connected smartphones."""
import asyncio
from datetime import datetime
from typing import Dict, Optional, Callable, List
from laptop_app.models.schemas import CameraDevice, CameraState
from laptop_app.config.settings import settings

class DeviceManager:
    def __init__(self):
        self._devices: Dict[str, CameraDevice] = {}
        self._listeners: List[Callable[[], None]] = []
        self._lock = asyncio.Lock()

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
        ws: any
    ) -> CameraDevice:
        async with self._lock:
            device = CameraDevice(
                camera_id=camera_id,
                device_name=device_name,
                ip_address=ip_address,
                battery_level=battery_level,
                state=CameraState.READY,
                sensor_info=sensor_info,
                last_heartbeat=datetime.utcnow(),
                client_ws=ws
            )
            self._devices[camera_id] = device
        self._notify()
        return device

    async def update_status(
        self,
        camera_id: str,
        state: Optional[CameraState] = None,
        battery_level: Optional[int] = None
    ) -> Optional[CameraDevice]:
        async with self._lock:
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
        async with self._lock:
            if camera_id in self._devices:
                self._devices[camera_id].state = CameraState.DISCONNECTED
                self._devices[camera_id].client_ws = None
        self._notify()

    def get_device(self, camera_id: str) -> Optional[CameraDevice]:
        return self._devices.get(camera_id)

    def get_all_devices(self) -> List[CameraDevice]:
        return list(self._devices.values())

    def get_ready_devices(self) -> List[CameraDevice]:
        return [
            d for d in self._devices.values()
            if d.state in (CameraState.READY, CameraState.CONNECTED)
        ]

device_manager = DeviceManager()
