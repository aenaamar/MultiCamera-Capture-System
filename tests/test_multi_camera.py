"""
Test Suite: Multi-Camera Connection and Capture Dispatch
=========================================================
Verifies that:
  - 4 cameras with distinct IDs can register simultaneously and all remain connected.
  - Each camera has an independent WebSocket object.
  - Duplicate camera_id re-registration closes the old WebSocket and replaces the entry.
  - Disconnecting one camera does not affect the others.
  - capture_manager.trigger_capture_all() dispatches to all ready cameras.
  - Each camera's capture command uses the correct session_id.
  - CaptureManager refuses a second concurrent capture.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from laptop_app.services.device_manager import DeviceManager
from laptop_app.services.session_manager import SessionManager
from laptop_app.services.capture_manager import CaptureManager
from laptop_app.models.schemas import CameraState, CaptureStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ws(camera_id: str) -> AsyncMock:
    """Create a minimal mock WebSocket that records sent messages."""
    ws = AsyncMock()
    ws.sent_messages = []
    async def _send_json(msg):
        ws.sent_messages.append(msg)
    ws.send_json.side_effect = _send_json
    ws.close = AsyncMock()
    return ws


async def _register_n(manager: DeviceManager, n: int):
    """Register n cameras (camera_01 .. camera_0n) and return list of mock ws objects."""
    ws_list = []
    for i in range(1, n + 1):
        cam_id = f"camera_0{i}"
        ws = _make_ws(cam_id)
        ws_list.append(ws)
        await manager.register_device(
            camera_id=cam_id,
            device_name=f"Phone {i}",
            ip_address=f"192.168.1.{i}",
            battery_level=80,
            sensor_info={"model": "Samsung"},
            ws=ws
        )
    return ws_list

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

PASS = "[PASS]"
FAIL = "[FAIL]"

def report(label: str, passed: bool, detail: str = ""):
    status = PASS if passed else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {status} {label}{suffix}")
    return passed


async def test_four_cameras_all_connect():
    print("\n[TEST 1] Four cameras register simultaneously...")
    dm = DeviceManager()
    ws_list = await _register_n(dm, 4)
    devices = dm.get_all_devices()
    ok = len(devices) == 4
    report("All 4 cameras registered", ok, f"got {len(devices)}")

    ids = {d.camera_id for d in devices}
    expected = {"camera_01", "camera_02", "camera_03", "camera_04"}
    ok2 = ids == expected
    report("Camera IDs are correct", ok2, str(ids))

    # Each device should reference its own distinct WebSocket
    ws_set = {d.client_ws for d in devices}
    ok3 = len(ws_set) == 4
    report("Each camera has an independent WebSocket", ok3)

    return ok and ok2 and ok3


async def test_duplicate_id_closes_old_ws():
    print("\n[TEST 2] Duplicate camera_id re-registration closes old WebSocket...")
    dm = DeviceManager()
    ws1 = _make_ws("camera_01")
    ws2 = _make_ws("camera_01")  # same ID, different connection

    await dm.register_device("camera_01", "Phone A", "192.168.1.10", 80, {}, ws1)
    await dm.register_device("camera_01", "Phone A v2", "192.168.1.11", 90, {}, ws2)

    ok_closed = ws1.close.called
    report("Old WebSocket.close() was called", ok_closed)

    devices = dm.get_all_devices()
    ok_count = len(devices) == 1
    report("Only one entry remains in DeviceManager", ok_count, f"got {len(devices)}")

    dev = dm.get_device("camera_01")
    ok_ws = dev is not None and dev.client_ws is ws2
    report("New WebSocket is active after re-registration", ok_ws)

    return ok_closed and ok_count and ok_ws


async def test_disconnect_one_keeps_others():
    print("\n[TEST 3] Disconnecting camera_02 leaves the other three intact...")
    dm = DeviceManager()
    await _register_n(dm, 4)

    await dm.unregister_device("camera_02")

    devices = dm.get_all_devices()
    # unregister_device marks DISCONNECTED, keeps the entry
    ok_count = len(devices) == 4
    report("All 4 entries still present", ok_count)

    cam2 = dm.get_device("camera_02")
    ok_disc = cam2 is not None and cam2.state == CameraState.DISCONNECTED
    report("camera_02 is marked DISCONNECTED", ok_disc)

    others = [dm.get_device(f"camera_0{i}") for i in [1, 3, 4]]
    ok_others = all(d is not None and d.state == CameraState.READY for d in others)
    report("camera_01, 03, 04 remain READY", ok_others)

    return ok_count and ok_disc and ok_others


async def test_capture_all_dispatches_to_all_ready():
    print("\n[TEST 4] trigger_capture_all() dispatches to all 4 ready cameras...")
    dm = DeviceManager()
    ws_list = await _register_n(dm, 4)

    # Build a minimal SessionManager backed by a temp dir
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = SessionManager(dataset_dir=Path(tmpdir))
        cm = CaptureManager()

        with patch("laptop_app.services.capture_manager.device_manager", dm), \
             patch("laptop_app.services.capture_manager.session_manager", sm):
            result = await cm.trigger_capture_all()

    ok_status = result.get("status") == "success"
    report("trigger_capture_all returned success", ok_status, str(result.get("status")))

    triggered = result.get("cameras_triggered", [])
    ok_count = len(triggered) == 4
    report("All 4 cameras are in cameras_triggered", ok_count, str(triggered))

    # Each mock WebSocket should have received exactly one capture_request
    for i, ws in enumerate(ws_list, start=1):
        cam_id = f"camera_0{i}"
        msgs = ws.sent_messages
        ok_msg = len(msgs) == 1 and msgs[0].get("type") == "capture_request"
        report(f"{cam_id} received capture_request", ok_msg, str(msgs))

    return ok_status and ok_count


async def test_capture_all_uses_same_session_id():
    print("\n[TEST 5] All capture_request messages share the same session_id...")
    dm = DeviceManager()
    ws_list = await _register_n(dm, 4)

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = SessionManager(dataset_dir=Path(tmpdir))
        cm = CaptureManager()

        with patch("laptop_app.services.capture_manager.device_manager", dm), \
             patch("laptop_app.services.capture_manager.session_manager", sm):
            result = await cm.trigger_capture_all()

    session_ids = set()
    for ws in ws_list:
        for msg in ws.sent_messages:
            if msg.get("type") == "capture_request":
                session_ids.add(msg.get("session_id"))

    ok = len(session_ids) == 1
    report("All cameras received the same session_id", ok, str(session_ids))
    return ok


async def test_capture_all_reuses_idle_session():
    print("\n[TEST 6] trigger_capture_all() reuses an existing IDLE session...")
    dm = DeviceManager()
    ws_list = await _register_n(dm, 2)

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = SessionManager(dataset_dir=Path(tmpdir))
        # Pre-create a session via "New Session" button
        pre_session = await sm.create_session(["camera_01", "camera_02"])
        pre_id = pre_session.session_id

        cm = CaptureManager()
        with patch("laptop_app.services.capture_manager.device_manager", dm), \
             patch("laptop_app.services.capture_manager.session_manager", sm):
            result = await cm.trigger_capture_all()

    ok_status = result.get("status") == "success"
    ok_reuse = result.get("session_id") == pre_id
    report("Returns success", ok_status)
    report("Reused existing IDLE session (no new session created)", ok_reuse,
           f"expected={pre_id}, got={result.get('session_id')}")
    return ok_status and ok_reuse


async def test_concurrent_capture_blocked():
    print("\n[TEST 7] Second trigger_capture_all() while first is in progress is rejected...")
    dm = DeviceManager()
    ws_list = await _register_n(dm, 2)

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = SessionManager(dataset_dir=Path(tmpdir))
        cm = CaptureManager()
        cm._is_capturing = True  # simulate in-progress

        with patch("laptop_app.services.capture_manager.device_manager", dm), \
             patch("laptop_app.services.capture_manager.session_manager", sm):
            result = await cm.trigger_capture_all()

    ok = result.get("status") == "error"
    report("Concurrent capture correctly rejected", ok, str(result.get("message")))
    return ok


async def test_reconnect_does_not_overwrite_others():
    print("\n[TEST 8] camera_02 reconnects and other cameras remain intact...")
    dm = DeviceManager()
    ws_list = await _register_n(dm, 4)

    # camera_02 reconnects (new WebSocket)
    old_ws2 = ws_list[1]
    new_ws2 = _make_ws("camera_02")
    await dm.register_device("camera_02", "Phone 2 Reconnect", "192.168.1.2", 75, {}, new_ws2)

    ok_old_closed = old_ws2.close.called
    report("Old camera_02 WebSocket closed on reconnect", ok_old_closed)

    # Other cameras should be untouched
    for i in [1, 3, 4]:
        cam_id = f"camera_0{i}"
        dev = dm.get_device(cam_id)
        ok = dev is not None and dev.client_ws is ws_list[i - 1] and dev.state == CameraState.READY
        report(f"{cam_id} untouched after camera_02 reconnect", ok)

    total = len(dm.get_all_devices())
    ok_total = total == 4
    report("DeviceManager still has 4 entries", ok_total, f"got {total}")

    return ok_old_closed and ok_total


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def _run_all():
    results = []
    results.append(await test_four_cameras_all_connect())
    results.append(await test_duplicate_id_closes_old_ws())
    results.append(await test_disconnect_one_keeps_others())
    results.append(await test_capture_all_dispatches_to_all_ready())
    results.append(await test_capture_all_uses_same_session_id())
    results.append(await test_capture_all_reuses_idle_session())
    results.append(await test_concurrent_capture_blocked())
    results.append(await test_reconnect_does_not_overwrite_others())

    print("\n" + "=" * 65)
    passed = sum(results)
    total = len(results)
    if passed == total:
        print(f"  ALL {total} MULTI-CAMERA TESTS PASSED WITH 100% SUCCESS")
    else:
        print(f"  {passed}/{total} TESTS PASSED — {total - passed} FAILURES")
    print("=" * 65)
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(_run_all())
    sys.exit(0 if success else 1)
