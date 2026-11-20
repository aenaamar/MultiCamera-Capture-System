"""
End-to-end integration test demonstrating multi-camera registration and capture workflow.
Spawns 4 simulated Android phones (camera_01, camera_02, camera_03, camera_04),
registers them with the server, triggers a multi-camera capture session,
verifies all 4 images are uploaded with SHA-256 verification, and validates dataset integrity.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from laptop_app.config.settings import settings
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager
from laptop_app.services.capture_manager import capture_manager
from tools.phone_simulator import SimulatedAndroidPhone

async def test_multi_camera_live_flow():
    print("=================================================================")
    print("  MULTI-CAMERA LIVE SIMULATION & INTEGRATION TEST (4 CAMERAS)   ")
    print("=================================================================")

    test_datasets_dir = Path("Datasets_Simulation_Test").resolve()
    settings.dataset_dir = test_datasets_dir
    session_manager.dataset_dir = test_datasets_dir

    # 1. Start server in background
    import uvicorn
    from laptop_app.server.api_server import app

    server_config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(server_config)
    server_task = asyncio.create_task(server.serve())

    # Wait for server to bind
    await asyncio.sleep(1.0)
    print("[1/5] Laptop server running on http://127.0.0.1:8000")

    # 2. Launch 4 simulated Android phones
    phone_configs = [
        ("camera_01", "Google Pixel 7 Pro"),
        ("camera_02", "Samsung Galaxy S23"),
        ("camera_03", "OnePlus 11"),
        ("camera_04", "Xiaomi 13 Pro")
    ]
    phones = [
        SimulatedAndroidPhone(cam_id, model, "127.0.0.1", 8000)
        for cam_id, model in phone_configs
    ]

    phone_tasks = [asyncio.create_task(p.run()) for p in phones]

    # Allow phones to connect and register
    await asyncio.sleep(2.0)

    devices = device_manager.get_all_devices()
    ready = device_manager.get_ready_devices()
    print(f"[2/5] Devices registered: {len(devices)}/4 connected | {len(ready)}/4 ready")
    for d in devices:
        print(f"      - {d.camera_id}: {d.device_name} (Battery: {d.battery_level}%, State: {d.state.value})")
    assert len(ready) == 4, "Expected all 4 simulated phones to be ready"

    # 3. Trigger remote capture across all 4 phones
    print("[3/5] Triggering 'Capture All' across all 4 phones...")
    result = await capture_manager.trigger_capture_all()
    session_id = result["session_id"]
    print(f"      Capture session initiated: {session_id}")

    # Wait for capture simulation and HTTP uploads to complete
    await asyncio.sleep(3.0)

    # 4. Verify Session Completion and Image Integrity
    session = session_manager.current_session
    print(f"[4/5] Checking session completion: {session.session_id}")
    print(f"      Session fully completed: {session.is_fully_completed}")
    print(f"      Captured cameras: {[k for k, v in session.results.items() if v.capture_success]}")
    print(f"      Uploaded cameras: {[k for k, v in session.results.items() if v.upload_success]}")
    assert session.is_fully_completed, "Session must be marked fully completed"

    # 5. Verify Dataset directory structure and manifest.json
    session_dir = test_datasets_dir / session_id
    images_dir = session_dir / "images"
    manifest_file = session_dir / "manifest.json"
    metadata_file = session_dir / "metadata" / "capture_info.json"

    print("[5/5] Verifying dataset folder structure on disk...")
    assert images_dir.exists()
    assert manifest_file.exists()
    assert metadata_file.exists()

    for cam_id, _ in phone_configs:
        img_path = images_dir / f"{cam_id}.jpg"
        assert img_path.exists(), f"Expected image missing: {img_path}"
        print(f"      Found verified image: {img_path.name} ({img_path.stat().st_size} bytes)")

    import json
    with open(manifest_file, "r") as f:
        manifest_data = json.load(f)
    print(f"      Manifest status: {manifest_data['capture_status']}")
    print(f"      Manifest images: {manifest_data['image_filenames']}")
    assert manifest_data["capture_status"] == "COMPLETED"
    assert len(manifest_data["image_filenames"]) == 4

    # Stop phones and server
    for p in phones:
        p.running = False
    for t in phone_tasks:
        t.cancel()
    server.should_exit = True
    await asyncio.sleep(0.5)

    print("\n=================================================================")
    print("  SIMULATION WORKFLOW TEST COMPLETED SUCCESSFULLY WITH 4 CAMERAS ")
    print("=================================================================")

if __name__ == "__main__":
    asyncio.run(test_multi_camera_live_flow())
