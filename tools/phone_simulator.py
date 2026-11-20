"""
Android Phone Simulator Node for MultiCamera Capture System.
Simulates physical Android companion devices running Camera2 with:
- SHA-256 calculation
- WebSocket telemetry and capture requests
- Deduplication of repeated capture requests
- Exponential backoff retry on upload failures
- Configurable failure emulation for test suites
"""
import sys
import io
import time
import json
import hashlib
import asyncio
import argparse
import websockets
import requests
from PIL import Image, ImageDraw

class SimulatedAndroidPhone:
    def __init__(self, camera_id: str, device_name: str, server_host: str, server_port: int):
        self.camera_id = camera_id
        self.device_name = device_name
        self.server_host = server_host
        self.server_port = server_port
        self.battery_level = 90
        self.running = True
        self.handled_requests = set()
        self.captured_timestamps = {}
        # Fault injection flags for test automation
        self.inject_corrupt_sha = False
        self.inject_capture_fail = False

    def create_captured_frame(self, session_id: str) -> bytes:
        """Emulate Camera2 high-res capture."""
        width, height = 1920, 1080
        img = Image.new("RGB", (width, height), color=(40, 44, 52))
        draw = ImageDraw.Draw(img)
        text = f"SIMULATED PHONE: {self.camera_id}\nModel: {self.device_name}\nSession: {session_id}\nTime: {time.ctime()}"
        draw.text((60, 60), text, fill=(97, 175, 239))
        
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()

    async def run(self):
        ws_url = f"ws://{self.server_host}:{self.server_port}/ws"
        print(f"[{self.camera_id}] Connecting to {ws_url}...")

        while self.running:
            try:
                async with websockets.connect(ws_url) as ws:
                    print(f"[{self.camera_id}] Connected. Registering device...")
                    reg_payload = {
                        "type": "device_register",
                        "camera_id": self.camera_id,
                        "device_name": self.device_name,
                        "battery_level": self.battery_level,
                        "sensor_info": {
                            "lens_facing": "BACK",
                            "hardware_level": 3,
                            "focal_length": 6.81,
                            "sensor_physical_size": {"width_mm": 9.8, "height_mm": 7.3},
                            "pixel_array_size": {"width": 1920, "height": 1080},
                            "simulated": True
                        }
                    }
                    await ws.send(json.dumps(reg_payload))

                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                    listener_task = asyncio.create_task(self._listen_loop(ws))

                    done, pending = await asyncio.wait(
                        [heartbeat_task, listener_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()

            except Exception as e:
                if self.running:
                    print(f"[{self.camera_id}] Connection error: {e}. Retrying in 3s...")
                    await asyncio.sleep(3.0)

    async def _heartbeat_loop(self, ws):
        while self.running:
            await asyncio.sleep(3.0)
            status_payload = {
                "type": "device_status",
                "camera_id": self.camera_id,
                "state": "READY",
                "battery_level": self.battery_level
            }
            try:
                await ws.send(json.dumps(status_payload))
            except Exception:
                break

    async def _listen_loop(self, ws):
        async for message in ws:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "registration_ack":
                print(f"[{self.camera_id}] Registered successfully with laptop!")

            elif msg_type == "capture_request":
                session_id = data.get("session_id")
                req_id = data.get("capture_request_id")

                # Deduplication check
                if req_id in self.handled_requests:
                    print(f"[{self.camera_id}] Duplicate capture request IGNORED: {req_id}")
                    continue
                self.handled_requests.add(req_id)

                print(f"[{self.camera_id}] Capture request received for {session_id} (ID: {req_id})")

                if self.inject_capture_fail:
                    await ws.send(json.dumps({
                        "type": "capture_failed",
                        "camera_id": self.camera_id,
                        "session_id": session_id,
                        "error_message": "Injected sensor shutter error"
                    }))
                    continue

                await ws.send(json.dumps({
                    "type": "capture_started",
                    "camera_id": self.camera_id,
                    "session_id": session_id
                }))

                await asyncio.sleep(0.15)
                jpeg_bytes = self.create_captured_frame(session_id)
                sha256 = hashlib.sha256(jpeg_bytes).hexdigest()
                if self.inject_corrupt_sha:
                    sha256 = "invalid_injected_checksum"

                ts_ms = int(time.time() * 1000)
                self.captured_timestamps[req_id] = ts_ms

                await ws.send(json.dumps({
                    "type": "capture_completed",
                    "camera_id": self.camera_id,
                    "session_id": session_id,
                    "timestamp_ms": ts_ms
                }))

                # Perform reliable upload with exponential backoff
                self._upload_with_retry(session_id, req_id, jpeg_bytes, sha256)

    def _upload_with_retry(self, session_id: str, req_id: str, jpeg_bytes: bytes, sha256: str, max_retries=3):
        url = f"http://{self.server_host}:{self.server_port}/api/upload"

        for attempt in range(max_retries + 1):
            files = {"file": (f"{self.camera_id}.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")}
            data = {
                "session_id": session_id,
                "camera_id": self.camera_id,
                "capture_request_id": req_id,
                "sha256_checksum": sha256,
                "metadata_json": json.dumps({
                    "lens": "BACK",
                    "focal_length": 6.81,
                    "iso": 100,
                    "exposure_time": "1/125s",
                    "simulated": True
                })
            }
            try:
                resp = requests.post(url, data=data, files=files, timeout=10)
                if resp.status_code == 200:
                    print(f"[{self.camera_id}] Upload succeeded & verified by laptop!")
                    return True
                elif resp.status_code in (400, 422, 403):
                    print(f"[{self.camera_id}] Permanent upload rejection ({resp.status_code}): {resp.text}")
                    return False
                else:
                    print(f"[{self.camera_id}] Transient upload failure ({resp.status_code}). Retrying...")
            except Exception as e:
                print(f"[{self.camera_id}] Upload network exception: {e}. Retrying...")

            time.sleep(1.0 * (2 ** attempt))

        print(f"[{self.camera_id}] Failed upload after {max_retries} retries.")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulated Android Camera Node")
    parser.add_argument("--id", default="camera_01", help="Camera ID (e.g. camera_01)")
    parser.add_argument("--name", default="Pixel 7 Pro", help="Device model name")
    parser.add_argument("--host", default="127.0.0.1", help="Laptop IP")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    args = parser.parse_args()

    phone = SimulatedAndroidPhone(args.id, args.name, args.host, args.port)
    try:
        asyncio.run(phone.run())
    except KeyboardInterrupt:
        print(f"[{args.id}] Stopped.")
