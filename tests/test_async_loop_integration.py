"""Test for Qt/asyncio event loop integration and session creation thread safety."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import threading
import time
import shutil
import pytest
from laptop_app.utils.async_loop import set_server_loop, get_server_loop, run_async_coro
from laptop_app.services.session_manager import SessionManager
from laptop_app.services.device_manager import DeviceManager
from laptop_app.services.capture_manager import CaptureManager

def test_async_loop_registration_and_threadsafe_execution():
    test_dir = Path("Datasets_Test_AsyncLoop")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)

    # 1. Start a dedicated background asyncio loop simulating Uvicorn's event loop
    loop = asyncio.new_event_loop()
    loop_started = threading.Event()

    def run_background_loop():
        asyncio.set_event_loop(loop)
        set_server_loop(loop)
        loop_started.set()
        loop.run_forever()

    t = threading.Thread(target=run_background_loop, daemon=True)
    t.start()
    loop_started.wait(timeout=2.0)

    try:
        assert get_server_loop() is loop
        
        # 2. Test session creation from main thread via run_async_coro
        sm = SessionManager(dataset_dir=test_dir)
        cams = ["camera_01", "camera_02"]
        
        fut = run_async_coro(sm.create_session(cams))
        session = fut.result(timeout=5.0)

        assert session is not None
        assert session.session_id.startswith("Session_")
        assert session.expected_cameras == cams
        assert (test_dir / session.session_id / "images").exists()
        assert (test_dir / session.session_id / "manifest.json").exists()

        # 3. Test capture manager trigger_capture_all when no cameras ready
        cm = CaptureManager()
        fut_cap = run_async_coro(cm.trigger_capture_all())
        res = fut_cap.result(timeout=5.0)
        assert res.get("status") == "error"
        assert "No cameras" in res.get("message")

    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2.0)
        if test_dir.exists():
            shutil.rmtree(test_dir)

if __name__ == "__main__":
    test_async_loop_registration_and_threadsafe_execution()
    print("All async loop integration tests passed successfully!")
