import asyncio
import io
import time
import json
import hashlib
from pathlib import Path
from PIL import Image, ImageDraw
import httpx
from laptop_app.config.settings import settings
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager
from laptop_app.services.capture_manager import capture_manager
from laptop_app.server.api_server import app
from laptop_app.server.upload_manager import upload_manager
from fastapi import UploadFile

async def test_live_multi_camera_flow():
    print("=================================================================")
    print("  STAGE 3: 4-CAMERA LIVE PROTOCOL & LIFECYCLE VERIFICATION       ")
    print("=================================================================")

    test_datasets_dir = Path("Datasets_Stage3_Verification").resolve()
    settings.dataset_dir = test_datasets_dir
    session_manager.dataset_dir = test_datasets_dir

    # 1. Register 4 distinct Android cameras
    phone_configs = [
        ("camera_01", "Google Pixel 7 Pro", "192.168.1.101", 94),
        ("camera_02", "Samsung Galaxy S23", "192.168.1.102", 87),
        ("camera_03", "OnePlus 11 5G", "192.168.1.103", 91),
        ("camera_04", "Xiaomi 13 Pro", "192.168.1.104", 78)
    ]

    print("\n[Step 1] Registering 4 Android companion devices...")
    for cam_id, model, ip, batt in phone_configs:
        dev = await device_manager.register_device(
            camera_id=cam_id,
            device_name=model,
            ip_address=ip,
            battery_level=batt,
            sensor_info={"lens": "BACK", "focal_length": 6.81, "hardware_level": 3},
            ws=None
        )
        print(f"  + Registered {cam_id}: {model} ({ip}) - Battery: {batt}%")

    ready = device_manager.get_ready_devices()
    assert len(ready) == 4, "All 4 cameras must be recognized and READY"
    print(f"  -> Total ready cameras: {len(ready)}/4")

    # 2. Create Capture Session for 4 cameras
    print("\n[Step 2] Initiating new multi-camera capture session...")
    cam_ids = [c[0] for c in phone_configs]
    session = await session_manager.create_session(cam_ids)
    session_dir = test_datasets_dir / session.session_id
    print(f"  -> Session created: {session.session_id}")
    print(f"  -> Expected cameras: {session.expected_cameras}")

    # 3. Simulate high-res image capture and streaming upload for each camera
    print("\n[Step 3] Simulating Camera2 capture and HTTP upload with SHA-256...")
    for cam_id, model, _, _ in phone_configs:
        # Create uncompressed JPEG
        img = Image.new("RGB", (1920, 1080), color=(30, 34, 42))
        draw = ImageDraw.Draw(img)
        draw.text((80, 80), f"Camera: {cam_id}\nModel: {model}\nSession: {session.session_id}", fill=(97, 175, 239))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        raw_bytes = buf.getvalue()

        # Compute SHA-256
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        req_id = f"req_{session.session_id}_{cam_id}_1700"

        # Record capture event
        await session_manager.record_capture_event(
            session_id=session.session_id,
            camera_id=cam_id,
            success=True,
            timestamp_ms=int(time.time() * 1000)
        )

        # Upload and verify
        upload_file = UploadFile(filename=f"{cam_id}.jpg", file=io.BytesIO(raw_bytes))
        res = await upload_manager.process_image_upload(
            session_id=session.session_id,
            camera_id=cam_id,
            capture_request_id=req_id,
            sha256_checksum=sha256,
            metadata_json_str=json.dumps({"lens": "BACK", "focal_length": 6.81, "iso": 100}),
            file=upload_file
        )
        assert res["status"] == "success"
        print(f"  + [{cam_id}] Upload verified & committed ({res['file_size']} bytes, SHA256: {sha256[:12]}...)")

    # 4. Verify Session Completion
    print("\n[Step 4] Verifying session completion & status...")
    assert session.is_fully_completed, "All 4 cameras uploaded -> session must be complete"
    print(f"  -> Session {session.session_id} is_fully_completed: {session.is_fully_completed}")

    # 5. Check Output Dataset Structure
    print("\n[Step 5] Checking generated dataset files on disk...")
    images_dir = session_dir / "images"
    manifest_path = session_dir / "manifest.json"
    metadata_path = session_dir / "metadata" / "capture_info.json"

    assert images_dir.exists()
    assert manifest_path.exists()
    assert metadata_path.exists()

    for cam_id, _, _, _ in phone_configs:
        img_file = images_dir / f"{cam_id}.jpg"
        assert img_file.exists()
        print(f"  + Verified image on disk: {img_file.name}")

    with open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["capture_status"] == "COMPLETED"
    assert len(manifest["image_filenames"]) == 4
    print(f"  -> manifest.json verified: {manifest['capture_status']} with 4 images")

    with open(metadata_path) as f:
        meta = json.load(f)
    assert len(meta["cameras"]) == 4
    for c in meta["cameras"]:
        assert c["calibration_status"] == "uncalibrated_raw_acquisition"
        assert c["extrinsics"] is None
        assert c["intrinsics"] is None
    print(f"  -> metadata/capture_info.json verified: 4 cameras, poses uninvented")

    print("\n=================================================================")
    print("  STAGE 3: MULTI-CAMERA FULL LIFECYCLE VERIFICATION SUCCEEDED     ")
    print("=================================================================")

if __name__ == "__main__":
    asyncio.run(test_live_multi_camera_flow())
