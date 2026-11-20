import asyncio
import io
import json
import hashlib
from pathlib import Path
from PIL import Image
from laptop_app.config.settings import settings
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager
from laptop_app.models.schemas import CameraState
from laptop_app.server.upload_manager import upload_manager
from fastapi import UploadFile

async def run_stage2_verification():
    print("[1/7] Setting up isolated test dataset directory...")
    test_dir = Path("Datasets_Stage2_Test").resolve()
    settings.dataset_dir = test_dir
    session_manager.dataset_dir = test_dir

    print("[2/7] Testing device registration...")
    dev = await device_manager.register_device(
        camera_id="camera_01",
        device_name="Google Pixel 7 Pro",
        ip_address="192.168.1.50",
        battery_level=94,
        sensor_info={"lens": "BACK", "focal_length": 6.81},
        ws=None
    )
    assert dev.camera_id == "camera_01"
    assert dev.state == CameraState.READY
    print(f"      OK: Device {dev.camera_id} registered with state={dev.state.value}, battery={dev.battery_level}%")

    print("[3/7] Creating capture session...")
    session = await session_manager.create_session(["camera_01"])
    assert session.session_id.startswith("Session_")
    session_dir = test_dir / session.session_id
    assert (session_dir / "manifest.json").exists()
    assert (session_dir / "metadata" / "capture_info.json").exists()
    print(f"      OK: Created {session.session_id} with initial manifest & metadata")

    print("[4/7] Generating synthetic raw JPEG image with known SHA-256...")
    img = Image.new("RGB", (1920, 1080), color=(70, 130, 180))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    raw_bytes = buf.getvalue()
    real_sha = hashlib.sha256(raw_bytes).hexdigest()
    print(f"      OK: Image size: {len(raw_bytes)} bytes | SHA256: {real_sha[:16]}...")

    print("[5/7] Testing SHA-256 rejection for tampered/corrupt upload...")
    corrupt_file = UploadFile(filename="camera_01.jpg", file=io.BytesIO(raw_bytes))
    rejected = False
    try:
        await upload_manager.process_image_upload(
            session_id=session.session_id,
            camera_id="camera_01",
            capture_request_id="req_001",
            sha256_checksum="tampered_bad_sha256",
            metadata_json_str=json.dumps({"iso": 100}),
            file=corrupt_file
        )
    except Exception as exc:
        rejected = True
        print(f"      OK: Successfully rejected invalid SHA-256: {exc}")
    assert rejected, "Upload manager must reject mismatched checksums"
    assert not (session_dir / "images" / "camera_01.jpg").exists(), "Corrupt upload must not be moved to images"
    assert not session.is_fully_completed, "Incomplete upload must never mark session as completed"

    print("[6/7] Testing valid upload and atomic staging commit...")
    valid_file = UploadFile(filename="camera_01.jpg", file=io.BytesIO(raw_bytes))
    res = await upload_manager.process_image_upload(
        session_id=session.session_id,
        camera_id="camera_01",
        capture_request_id="req_001",
        sha256_checksum=real_sha,
        metadata_json_str=json.dumps({"focal_length": 6.81, "iso": 100, "exposure_time": "1/125"}),
        file=valid_file
    )
    assert res["status"] == "success"
    final_img_path = session_dir / "images" / "camera_01.jpg"
    assert final_img_path.exists()
    assert session.is_fully_completed
    print(f"      OK: Upload verified and committed to {final_img_path.name}")
    print(f"      OK: Session status is fully completed: {session.is_fully_completed}")

    print("[7/7] Validating manifest.json & capture_info.json schemas...")
    with open(session_dir / "manifest.json", "r") as f:
        manifest = json.load(f)
    assert manifest["session_id"] == session.session_id
    assert manifest["capture_status"] == "COMPLETED"
    assert "camera_01.jpg" in manifest["image_filenames"]
    print(f"      OK: manifest.json verified: status={manifest['capture_status']}, images={manifest['image_filenames']}")

    with open(session_dir / "metadata" / "capture_info.json", "r") as f:
        info = json.load(f)
    cam_entry = info["cameras"][0]
    assert cam_entry["camera_id"] == "camera_01"
    assert cam_entry["calibration_status"] == "uncalibrated_raw_acquisition"
    assert cam_entry["extrinsics"] is None
    assert cam_entry["intrinsics"] is None
    print(f"      OK: capture_info.json verified (calibration_status='{cam_entry['calibration_status']}', extrinsics=null)")

    print("\n>>> STAGE 2 COMPONENT VERIFICATION COMPLETED WITH 100% SUCCESS <<<")

if __name__ == "__main__":
    asyncio.run(run_stage2_verification())
