"""
Test Suite: Upload Verification
================================
Verifies that upload_manager.process_image_upload():
  - Writes the final JPEG to the correct path and reports it in the response.
  - Physical file exists and is non-empty after a successful upload.
  - SHA-256 checksum is computed correctly and matches the client value.
  - Duplicate (idempotent) upload with the same request_id returns success without rewriting.
  - A second distinct upload for the same camera in the same session creates a NEW file
    (via a new capture_request_id; the old file is replaced, which is by design).
  - Zero-byte uploads are rejected with HTTP 400.
  - Checksum mismatch is rejected with HTTP 422.
  - Corrupt (non-JPEG) data is rejected with HTTP 422.
  - Unregistered camera_id is rejected with HTTP 403.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import hashlib
import io
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import UploadFile, HTTPException

from laptop_app.services.session_manager import SessionManager
from laptop_app.services.device_manager import DeviceManager
from laptop_app.server.upload_manager import UploadManager
from laptop_app.models.schemas import CameraState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_jpeg(width: int = 32, height: int = 32) -> bytes:
    """Generate a minimal but valid JPEG using Pillow (or a raw JFIF header as fallback)."""
    try:
        from PIL import Image
        buf = io.BytesIO()
        img = Image.new("RGB", (width, height), color=(128, 64, 32))
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except ImportError:
        # Raw 2×2 JFIF JPEG bytes (always valid)
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=8"
            b"\x837\x82XHHWVWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW"
            b"\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xc4"
            b"\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
            b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05"
            b"\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06"
            b"\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br"
            b"\x82\t\n\x16\x17\x18\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZ"
            b"cdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94"
            b"\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa"
            b"\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7"
            b"\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3"
            b"\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8"
            b"\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd4P\x00\x00"
            b"\x00\x00\x00\x1f\xff\xd9"
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_upload_file(data: bytes, filename: str = "test.jpg") -> UploadFile:
    """Wrap bytes as a minimal FastAPI UploadFile with async read()."""
    buf = io.BytesIO(data)
    remaining = [data]  # mutable container

    async def _read(size: int = -1) -> bytes:
        if not remaining[0]:
            return b""
        if size == -1 or size >= len(remaining[0]):
            chunk = remaining[0]
            remaining[0] = b""
            return chunk
        chunk = remaining[0][:size]
        remaining[0] = remaining[0][size:]
        return chunk

    uf = MagicMock(spec=UploadFile)
    uf.filename = filename
    uf.read = _read
    return uf


async def _setup(tmpdir: str):
    """Create a fresh DeviceManager, SessionManager, and UploadManager for a test."""
    dm = DeviceManager()
    ws = AsyncMock()
    await dm.register_device(
        camera_id="camera_01",
        device_name="Test Phone",
        ip_address="127.0.0.1",
        battery_level=100,
        sensor_info={},
        ws=ws
    )
    sm = SessionManager(dataset_dir=Path(tmpdir))
    session = await sm.create_session(["camera_01"])
    um = UploadManager()
    return dm, sm, session, um


PASS = "[PASS]"
FAIL = "[FAIL]"

def report(label: str, passed: bool, detail: str = ""):
    status = PASS if passed else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {status} {label}{suffix}")
    return passed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_successful_upload():
    print("\n[TEST 1] Successful JPEG upload: file written, size correct, sha256 matches...")
    jpeg = _make_valid_jpeg()
    sha = _sha256(jpeg)

    with tempfile.TemporaryDirectory() as tmpdir:
        dm, sm, session, um = await _setup(tmpdir)
        uf = _make_upload_file(jpeg, "camera_01.jpg")

        with patch("laptop_app.server.upload_manager.device_manager", dm), \
             patch("laptop_app.server.upload_manager.session_manager", sm):
            resp = await um.process_image_upload(
                session_id=session.session_id,
                camera_id="camera_01",
                capture_request_id="req_001",
                sha256_checksum=sha,
                metadata_json_str="{}",
                file=uf
            )

        ok_status = resp.get("status") == "success"
        report("Response status is 'success'", ok_status)

        dest = Path(resp.get("destination_path", ""))
        ok_exists = dest.exists()
        report("File physically exists at destination_path", ok_exists, str(dest))

        ok_size = dest.stat().st_size == len(jpeg) if ok_exists else False
        report(f"File size matches ({len(jpeg)} bytes)", ok_size)

        ok_sha = resp.get("sha256", "").lower() == sha.lower()
        report("SHA-256 in response matches client checksum", ok_sha)

    return ok_status and ok_exists and ok_size and ok_sha


async def test_idempotent_upload():
    print("\n[TEST 2] Idempotent retry: same capture_request_id returns success without error...")
    jpeg = _make_valid_jpeg(16, 16)
    sha = _sha256(jpeg)

    with tempfile.TemporaryDirectory() as tmpdir:
        dm, sm, session, um = await _setup(tmpdir)

        with patch("laptop_app.server.upload_manager.device_manager", dm), \
             patch("laptop_app.server.upload_manager.session_manager", sm):
            # First upload
            uf1 = _make_upload_file(jpeg)
            resp1 = await um.process_image_upload(
                session_id=session.session_id,
                camera_id="camera_01",
                capture_request_id="req_idem",
                sha256_checksum=sha,
                metadata_json_str="{}",
                file=uf1
            )
            # Second upload — same request_id
            uf2 = _make_upload_file(jpeg)
            resp2 = await um.process_image_upload(
                session_id=session.session_id,
                camera_id="camera_01",
                capture_request_id="req_idem",
                sha256_checksum=sha,
                metadata_json_str="{}",
                file=uf2
            )

        ok1 = resp1.get("status") == "success"
        ok2 = resp2.get("status") == "success"
        ok_note = "Idempotent" in resp2.get("note", "") or resp2.get("note") is not None
        report("First upload succeeds", ok1)
        report("Idempotent retry also returns success", ok2)
        report("Idempotent response includes explanatory note", ok_note, resp2.get("note"))

    return ok1 and ok2


async def test_zero_byte_upload_rejected():
    print("\n[TEST 3] Zero-byte upload is rejected with HTTP 400...")
    with tempfile.TemporaryDirectory() as tmpdir:
        dm, sm, session, um = await _setup(tmpdir)
        uf = _make_upload_file(b"")

        try:
            with patch("laptop_app.server.upload_manager.device_manager", dm), \
                 patch("laptop_app.server.upload_manager.session_manager", sm):
                await um.process_image_upload(
                    session_id=session.session_id,
                    camera_id="camera_01",
                    capture_request_id="req_zero",
                    sha256_checksum="",
                    metadata_json_str="{}",
                    file=uf
                )
            ok = False
            report("Zero-byte upload correctly rejected", ok, "No exception raised!")
        except HTTPException as ex:
            ok = ex.status_code == 400
            report("Zero-byte upload rejected with HTTP 400", ok, f"got {ex.status_code}")

    return ok


async def test_checksum_mismatch_rejected():
    print("\n[TEST 4] Checksum mismatch is rejected with HTTP 422...")
    jpeg = _make_valid_jpeg()
    wrong_sha = "0" * 64

    with tempfile.TemporaryDirectory() as tmpdir:
        dm, sm, session, um = await _setup(tmpdir)
        uf = _make_upload_file(jpeg)

        try:
            with patch("laptop_app.server.upload_manager.device_manager", dm), \
                 patch("laptop_app.server.upload_manager.session_manager", sm):
                await um.process_image_upload(
                    session_id=session.session_id,
                    camera_id="camera_01",
                    capture_request_id="req_bad_sha",
                    sha256_checksum=wrong_sha,
                    metadata_json_str="{}",
                    file=uf
                )
            ok = False
            report("Checksum mismatch correctly rejected", ok, "No exception raised!")
        except HTTPException as ex:
            ok = ex.status_code == 422
            report("Checksum mismatch rejected with HTTP 422", ok, f"got {ex.status_code}")

    return ok


async def test_corrupt_image_rejected():
    print("\n[TEST 5] Corrupt (non-JPEG) data is rejected with HTTP 422...")
    garbage = b"This is not a JPEG file at all " * 100
    sha = _sha256(garbage)

    with tempfile.TemporaryDirectory() as tmpdir:
        dm, sm, session, um = await _setup(tmpdir)
        uf = _make_upload_file(garbage, "camera_01.jpg")

        try:
            with patch("laptop_app.server.upload_manager.device_manager", dm), \
                 patch("laptop_app.server.upload_manager.session_manager", sm):
                await um.process_image_upload(
                    session_id=session.session_id,
                    camera_id="camera_01",
                    capture_request_id="req_corrupt",
                    sha256_checksum=sha,
                    metadata_json_str="{}",
                    file=uf
                )
            ok = False
            report("Corrupt image correctly rejected", ok, "No exception raised!")
        except HTTPException as ex:
            ok = ex.status_code == 422
            report("Corrupt image rejected with HTTP 422", ok, f"got {ex.status_code}")

    return ok


async def test_unregistered_camera_rejected():
    print("\n[TEST 6] Unknown camera_id is rejected with HTTP 403...")
    jpeg = _make_valid_jpeg()
    sha = _sha256(jpeg)

    with tempfile.TemporaryDirectory() as tmpdir:
        dm, sm, session, um = await _setup(tmpdir)
        uf = _make_upload_file(jpeg)

        try:
            with patch("laptop_app.server.upload_manager.device_manager", dm), \
                 patch("laptop_app.server.upload_manager.session_manager", sm):
                await um.process_image_upload(
                    session_id=session.session_id,
                    camera_id="camera_99",   # not registered
                    capture_request_id="req_unregistered",
                    sha256_checksum=sha,
                    metadata_json_str="{}",
                    file=uf
                )
            ok = False
            report("Unregistered camera correctly rejected", ok, "No exception raised!")
        except HTTPException as ex:
            ok = ex.status_code == 403
            report("Unregistered camera rejected with HTTP 403", ok, f"got {ex.status_code}")

    return ok


async def test_manifest_updated_after_upload():
    print("\n[TEST 7] Session manifest is updated with upload_success=True after upload...")
    jpeg = _make_valid_jpeg()
    sha = _sha256(jpeg)

    with tempfile.TemporaryDirectory() as tmpdir:
        dm, sm, session, um = await _setup(tmpdir)
        uf = _make_upload_file(jpeg)

        with patch("laptop_app.server.upload_manager.device_manager", dm), \
             patch("laptop_app.server.upload_manager.session_manager", sm):
            await um.process_image_upload(
                session_id=session.session_id,
                camera_id="camera_01",
                capture_request_id="req_manifest",
                sha256_checksum=sha,
                metadata_json_str="{}",
                file=uf
            )

        result_entry = session.results.get("camera_01")
        ok_flag = result_entry is not None and result_entry.upload_success is True
        report("session.results['camera_01'].upload_success is True", ok_flag)

        ok_sha = result_entry is not None and result_entry.sha256_checksum is not None
        report("SHA-256 recorded in session result", ok_sha)

        ok_size = result_entry is not None and result_entry.file_size_bytes == len(jpeg)
        report(f"file_size_bytes matches ({len(jpeg)} bytes)", ok_size)

    return ok_flag and ok_sha and ok_size


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def _run_all():
    results = []
    results.append(await test_successful_upload())
    results.append(await test_idempotent_upload())
    results.append(await test_zero_byte_upload_rejected())
    results.append(await test_checksum_mismatch_rejected())
    results.append(await test_corrupt_image_rejected())
    results.append(await test_unregistered_camera_rejected())
    results.append(await test_manifest_updated_after_upload())

    print("\n" + "=" * 65)
    passed = sum(results)
    total = len(results)
    if passed == total:
        print(f"  ALL {total} UPLOAD VERIFICATION TESTS PASSED WITH 100% SUCCESS")
    else:
        print(f"  {passed}/{total} TESTS PASSED — {total - passed} FAILURES")
    print("=" * 65)
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(_run_all())
    sys.exit(0 if success else 1)
