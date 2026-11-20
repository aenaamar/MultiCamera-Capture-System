"""Dataset and JSON metadata generation adhering strictly to the 3DGS specification."""
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from laptop_app.models.schemas import CaptureSession

def write_session_metadata(
    session_dir: Path,
    session: CaptureSession
) -> None:
    """
    Generate manifest.json and metadata/capture_info.json in the session directory.
    Original images and calibration values are preserved without inventing poses.
    Calculates synchronization metrics (spread between earliest and latest capture timestamps).
    Explicitly clarifies cross-device clock offset and accuracy limitations.
    """
    images_dir = session_dir / "images"
    meta_dir = session_dir / "metadata"
    images_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    cameras_info = []
    image_filenames = []
    image_dimensions = {}
    image_sizes = {}
    timestamps = []

    for cam_id, res in session.results.items():
        if res.capture_timestamp_ms:
            timestamps.append(res.capture_timestamp_ms)

        if res.upload_success and res.filename:
            image_filenames.append(res.filename)
            image_dimensions[res.filename] = {
                "width": res.image_width,
                "height": res.image_height
            }
            image_sizes[res.filename] = res.file_size_bytes

            cameras_info.append({
                "camera_id": cam_id,
                "filename": res.filename,
                "capture_request_id": res.metadata.get("capture_request_id"),
                "capture_timestamp_ms": res.capture_timestamp_ms,
                "camera2_sensor_timestamp_ns": res.metadata.get("camera2_sensor_timestamp_ns"),
                "elapsed_realtime_ns": res.metadata.get("elapsed_realtime_ns"),
                "timestamp_source": res.metadata.get("timestamp_source", "System.currentTimeMillis()"),
                "image_width": res.image_width,
                "image_height": res.image_height,
                "file_size_bytes": res.file_size_bytes,
                "sha256": res.sha256_checksum,
                "sensor_info": res.metadata.get("sensor_info", {}),
                "exposure_settings": res.metadata.get("exposure_settings", {}),
                # Crucial 3DGS requirement: poses must NOT be fabricated
                "extrinsics": None,
                "intrinsics": None,
                "calibration_status": "uncalibrated_raw_acquisition",
                "notes": "Physical camera poses and calibration must be estimated via SfM/COLMAP."
            })

    # Cross-device synchronization spread (wall-clock estimation)
    sync_delta_ms = None
    if len(timestamps) > 1:
        sync_delta_ms = max(timestamps) - min(timestamps)

    # 1. Prepare metadata/capture_info.json
    capture_info_doc = {
        "schema_version": "1.0.0",
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat() + "Z",
        "synchronization_metrics": {
            "sync_delta_ms": sync_delta_ms,
            "min_timestamp_ms": min(timestamps) if timestamps else None,
            "max_timestamp_ms": max(timestamps) if timestamps else None,
            "accuracy_level": "approximate_network_time",
            "note": (
                "Wall-clock milliseconds (System.currentTimeMillis()) are subject to NTP jitter across phones. "
                "Camera2 SENSOR_TIMESTAMP nanoseconds provide precise per-device exposure times but use independent "
                "monotonic clocks that cannot be directly diffed across physical devices without PTP/hardware sync."
            )
        },
        "cameras": cameras_info
    }

    capture_info_path = meta_dir / "capture_info.json"
    with open(capture_info_path, "w", encoding="utf-8") as f:
        json.dump(capture_info_doc, f, indent=4)

    # 2. Prepare manifest.json
    successfully_captured = [c for c, r in session.results.items() if r.capture_success]
    successfully_uploaded = [c for c, r in session.results.items() if r.upload_success]

    manifest_doc = {
        "schema_version": "1.0.0",
        "session_id": session.session_id,
        "capture_datetime": session.created_at.isoformat() + "Z",
        "expected_camera_count": len(session.expected_cameras),
        "expected_cameras": session.expected_cameras,
        "successfully_captured_cameras": successfully_captured,
        "successfully_uploaded_cameras": successfully_uploaded,
        "image_filenames": image_filenames,
        "image_dimensions": image_dimensions,
        "image_file_sizes": image_sizes,
        "capture_status": session.status.value,
        "approximate_synchronization_delta_ms": sync_delta_ms,
        "errors": session.errors
    }

    manifest_path = session_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_doc, f, indent=4)
