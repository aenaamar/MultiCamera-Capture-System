"""Comprehensive tests for Stage 2 Laptop Application & Server."""
import io
import json
import hashlib
import pytest
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient

from laptop_app.config.settings import settings
from laptop_app.server.api_server import app
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager
from laptop_app.models.schemas import CameraState
from laptop_app.utils.file_utils import compute_sha256, validate_image_integrity

client = TestClient(app)

def create_dummy_jpeg(width=640, height=480, color=(255, 0, 0)) -> bytes:
    """Generate a valid in-memory JPEG byte array."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

def test_system_status_endpoint():
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ONLINE"
    assert "total_connected" in data

@pytest.mark.asyncio
async def test_websocket_device_registration_and_status():
    with client.websocket_connect("/ws") as ws:
        # 1. Register device
        reg_payload = {
            "type": "device_register",
            "camera_id": "camera_01",
            "device_name": "Test Pixel Phone",
            "battery_level": 92,
            "sensor_info": {"lens_facing": "BACK", "focal_length": 6.8}
        }
        ws.send_json(reg_payload)
        resp = ws.receive_json()
        assert resp["type"] == "registration_ack"
        assert resp["camera_id"] == "camera_01"
        assert resp["status"] == "ACCEPTED"

        # Verify device registered in manager
        dev = device_manager.get_device("camera_01")
        assert dev is not None
        assert dev.device_name == "Test Pixel Phone"
        assert dev.state == CameraState.READY

        # 2. Heartbeat update
        ws.send_json({
            "type": "device_status",
            "camera_id": "camera_01",
            "state": "READY",
            "battery_level": 91
        })
        ack = ws.receive_json()
        assert ack["type"] == "status_ack"
        assert dev.battery_level == 91

@pytest.mark.asyncio
async def test_session_creation_and_upload_verification(tmp_path):
    # Point session manager to temp dir for test isolation
    session_manager.dataset_dir = tmp_path
    settings.dataset_dir = tmp_path

    # Register camera
    dev = await device_manager.register_device(
        camera_id="camera_02",
        device_name="Test Phone 2",
        ip_address="192.168.1.100",
        battery_level=80,
        sensor_info={},
        ws=None
    )

    # Create session
    session = await session_manager.create_session(["camera_02"])
    assert session.session_id.startswith("Session_")
    session_dir = tmp_path / session.session_id
    assert (session_dir / "manifest.json").exists()
    assert (session_dir / "metadata" / "capture_info.json").exists()

    # Create dummy JPEG image
    jpeg_bytes = create_dummy_jpeg(800, 600)
    expected_sha = hashlib.sha256(jpeg_bytes).hexdigest()

    # 1. Test invalid checksum rejection
    files = {"file": ("camera_02.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")}
    data = {
        "session_id": session.session_id,
        "camera_id": "camera_02",
        "capture_request_id": "req_123",
        "sha256_checksum": "bad_checksum_hash",
        "metadata_json": json.dumps({"focal_length": 5.0})
    }
    bad_resp = client.post("/api/upload", data=data, files=files)
    assert bad_resp.status_code == 422
    assert "Checksum mismatch" in bad_resp.json()["detail"]

    # Incomplete upload must NOT mark session as completed
    assert not session.is_fully_completed
    assert (session_dir / "images" / "camera_02.jpg").exists() is False

    # 2. Test valid upload with true checksum
    files = {"file": ("camera_02.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")}
    data["sha256_checksum"] = expected_sha
    good_resp = client.post("/api/upload", data=data, files=files)
    assert good_resp.status_code == 200
    res_data = good_resp.json()
    assert res_data["status"] == "success"
    assert res_data["sha256"] == expected_sha
    assert res_data["width"] == 800
    assert res_data["height"] == 600

    # Verify atomic move and session completion
    final_img_path = session_dir / "images" / "camera_02.jpg"
    assert final_img_path.exists()
    assert session.is_fully_completed

    # Verify manifest.json integrity
    with open(session_dir / "manifest.json", "r") as f:
        manifest = json.load(f)
    assert manifest["session_id"] == session.session_id
    assert "camera_02.jpg" in manifest["image_filenames"]
    assert manifest["capture_status"] == "COMPLETED"

    # Verify capture_info.json format & no invented poses
    with open(session_dir / "metadata" / "capture_info.json", "r") as f:
        cap_info = json.load(f)
    assert len(cap_info["cameras"]) == 1
    cam_meta = cap_info["cameras"][0]
    assert cam_meta["camera_id"] == "camera_02"
    assert cam_meta["extrinsics"] is None
    assert cam_meta["intrinsics"] is None
    assert cam_meta["calibration_status"] == "uncalibrated_raw_acquisition"
