# 3D Gaussian Splatting Integration Guide & Contract

This document serves as the integration contract between the **Multi-Camera Capture Application** and the downstream **3D Gaussian Splatting (3DGS) Reconstruction Pipeline**.

---

## 1. Responsibilities & Boundaries

- **Capture Application (Our Component):**
  - Connects to multiple Android smartphones over local Wi-Fi.
  - Triggers remote photo capture via Android Camera2.
  - Automatically uploads and verifies full-resolution, uncompressed JPEGs with SHA-256 integrity.
  - Formats output sessions into standard, reproducible folder structures.
  - Records real hardware timestamps (`System.currentTimeMillis()` and Camera2 `SENSOR_TIMESTAMP` nanoseconds).
  - Documents cross-device synchronization limitations (network time jitter vs. local monotonic sensor clocks).
  - **Does NOT invent or fabricate camera poses, extrinsics, or intrinsics.**

- **3DGS Pipeline (Teammate's Component):**
  - Reads captured image files and sensor metadata from `Datasets/Session_XXX/`.
  - Performs Structure-from-Motion (SfM) via COLMAP to estimate camera extrinsics (poses) and camera intrinsics.
  - Formats reconstructed sparse models for the specific 3DGS implementation used (e.g., Inria 3DGS, Nerfstudio `splatfacto`, or gsplat).
  - Trains 3D Gaussian Splatting models from the aligned cameras.
  - Integrates MediaPipe hand gestures for virtual camera navigation.

---

## 2. Dataset Folder Structure

```
Datasets/
    Session_001/
        images/
            camera_01.jpg
            camera_02.jpg
            camera_03.jpg
            camera_04.jpg
        metadata/
            capture_info.json
        manifest.json
    Session_002/
        ...
```

---

## 3. JSON Schemas

### `manifest.json`
Located at: `Datasets/<session_id>/manifest.json`

```json
{
    "schema_version": "1.0.0",
    "session_id": "Session_001",
    "capture_datetime": "2026-11-20T10:30:00Z",
    "expected_camera_count": 4,
    "expected_cameras": ["camera_01", "camera_02", "camera_03", "camera_04"],
    "successfully_captured_cameras": ["camera_01", "camera_02", "camera_03", "camera_04"],
    "successfully_uploaded_cameras": ["camera_01", "camera_02", "camera_03", "camera_04"],
    "image_filenames": [
        "camera_01.jpg",
        "camera_02.jpg",
        "camera_03.jpg",
        "camera_04.jpg"
    ],
    "image_dimensions": {
        "camera_01.jpg": {"width": 1920, "height": 1080},
        "camera_02.jpg": {"width": 1920, "height": 1080}
    },
    "image_file_sizes": {
        "camera_01.jpg": 2450820,
        "camera_02.jpg": 2390110
    },
    "capture_status": "COMPLETED",
    "approximate_synchronization_delta_ms": 32,
    "errors": []
}
```

### `metadata/capture_info.json`
Located at: `Datasets/<session_id>/metadata/capture_info.json`

```json
{
    "schema_version": "1.0.0",
    "session_id": "Session_001",
    "created_at": "2026-11-20T10:30:00Z",
    "synchronization_metrics": {
        "sync_delta_ms": 32,
        "min_timestamp_ms": 1700000000100,
        "max_timestamp_ms": 1700000000132,
        "accuracy_level": "approximate_network_time",
        "note": "Wall-clock milliseconds (System.currentTimeMillis()) are subject to NTP jitter across phones. Camera2 SENSOR_TIMESTAMP nanoseconds provide precise per-device exposure times but use independent monotonic clocks that cannot be directly diffed across physical devices without PTP/hardware sync."
    },
    "cameras": [
        {
            "camera_id": "camera_01",
            "filename": "camera_01.jpg",
            "capture_request_id": "req_Session_001_camera_01_1700000000100",
            "capture_timestamp_ms": 1700000000100,
            "camera2_sensor_timestamp_ns": 12845910283000,
            "elapsed_realtime_ns": 49204910294000,
            "timestamp_source": "System.currentTimeMillis() (wall-clock) + Camera2 SENSOR_TIMESTAMP (monotonic)",
            "image_width": 1920,
            "image_height": 1080,
            "file_size_bytes": 2450820,
            "sha256": "4a12b...6f9",
            "sensor_info": {
                "lens_facing": "BACK",
                "focal_length": 6.81,
                "sensor_physical_size": {"width_mm": 9.8, "height_mm": 7.3},
                "pixel_array_size": {"width": 1920, "height": 1080}
            },
            "exposure_settings": {
                "iso": 100,
                "exposure_time": "1/125s"
            },
            "extrinsics": null,
            "intrinsics": null,
            "calibration_status": "uncalibrated_raw_acquisition",
            "notes": "Physical camera poses and calibration must be estimated via SfM/COLMAP."
        }
    ]
}
```

---

## 4. How to Use the Python Integration Utilities

You do not need to install the capture application's dependencies to consume the dataset. Simply use `integration/dataset_reader.py`:

```python
from integration.dataset_reader import DatasetReader

# Point to the Datasets folder
reader = DatasetReader("Datasets")

# 1. List all successfully completed capture sessions
completed_sessions = reader.list_sessions(completed_only=True)
print("Available sessions:", completed_sessions)

if completed_sessions:
    sid = completed_sessions[-1]
    
    # 2. Get image file paths for COLMAP
    image_paths = reader.get_image_paths(sid)
    for cam_id, path in image_paths.items():
        print(f"Camera {cam_id}: {path}")

    # 3. Read camera sensor info & synchronization metrics
    sync_stats = reader.get_synchronization_stats(sid)
    print(f"Approximate network capture spread: {sync_stats.get('sync_delta_ms')} ms")
    
    manifest = reader.get_manifest(sid)
    print(f"Captured {len(manifest['image_filenames'])} photos.")
```

### Dataset Validation Utility
Before initiating COLMAP reconstruction, verify dataset integrity:

```bash
python integration/dataset_validator.py Datasets
```
This script confirms that all expected camera images exist, are non-empty valid JPEGs (with full Pillow raster decoding verification), and match their SHA-256 signatures.

---

## 5. COLMAP Reconstruction Handoff Workflow

1. Select a completed session (e.g., `Datasets/Session_001/`).
2. Run COLMAP feature extraction and matcher on `Datasets/Session_001/images`:
   ```bash
   colmap feature_extractor --database_path Session_001/database.db --image_path Session_001/images
   colmap exhaustive_matcher --database_path Session_001/database.db
   mkdir Session_001/sparse
   colmap mapper --database_path Session_001/database.db --image_path Session_001/images --output_path Session_001/sparse
   ```
3. Pass the resulting reconstruction into your 3D Gaussian Splatting training pipeline:
   - For Inria 3DGS: `python train.py -s Datasets/Session_001`
   - For Nerfstudio: `ns-train splatfacto --data Datasets/Session_001`
