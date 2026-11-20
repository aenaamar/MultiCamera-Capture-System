"""
Generates an example dataset with simulated multi-angle capture metadata
for testing downstream COLMAP / 3D Gaussian Splatting ingestion.
Clearly marked as SIMULATED data to comply with 3DGS requirements.
"""
import io
import json
import hashlib
from pathlib import Path
from PIL import Image, ImageDraw

def generate_example_dataset(output_root: str = "Datasets"):
    root = Path(output_root).resolve()
    session_dir = root / "Session_001_Example"
    images_dir = session_dir / "images"
    meta_dir = session_dir / "metadata"

    images_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    cameras = [
        ("camera_01", "Google Pixel 7 Pro", 1700000000100, (230, 80, 80)),
        ("camera_02", "Samsung Galaxy S23", 1700000000115, (80, 180, 80)),
        ("camera_03", "OnePlus 11 5G", 1700000000128, (80, 120, 230)),
        ("camera_04", "Xiaomi 13 Pro", 1700000000135, (230, 200, 80))
    ]

    image_filenames = []
    image_dimensions = {}
    image_sizes = {}
    camera_meta_list = []
    timestamps = [c[2] for c in cameras]

    for cam_id, model, ts, color in cameras:
        filename = f"{cam_id}.jpg"
        img_path = images_dir / filename

        # Render simulated scene frame
        img = Image.new("RGB", (1920, 1080), color=(30, 32, 40))
        draw = ImageDraw.Draw(img)
        # Draw simulated target object box
        draw.rectangle([760, 340, 1160, 740], outline=color, width=6)
        draw.text((780, 360), f"SIMULATED 3DGS TARGET\nAngle: {cam_id}\nDevice: {model}", fill=(240, 240, 240))
        
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        raw_bytes = buf.getvalue()

        with open(img_path, "wb") as f:
            f.write(raw_bytes)

        sha = hashlib.sha256(raw_bytes).hexdigest()
        image_filenames.append(filename)
        image_dimensions[filename] = {"width": 1920, "height": 1080}
        image_sizes[filename] = len(raw_bytes)

        camera_meta_list.append({
            "camera_id": cam_id,
            "filename": filename,
            "capture_request_id": f"req_example_{cam_id}",
            "capture_timestamp_ms": ts,
            "image_width": 1920,
            "image_height": 1080,
            "file_size_bytes": len(raw_bytes),
            "sha256": sha,
            "sensor_info": {
                "device_model": model,
                "lens_facing": "BACK",
                "focal_length": 6.81,
                "simulated_data": True
            },
            "exposure_settings": {
                "iso": 100,
                "exposure_time": "1/125s"
            },
            "extrinsics": None,
            "intrinsics": None,
            "calibration_status": "uncalibrated_raw_acquisition",
            "notes": "SIMULATED EXAMPLE DATASET. Physical camera poses must be estimated via SfM/COLMAP."
        })

    # 1. metadata/capture_info.json
    sync_delta = max(timestamps) - min(timestamps)
    capture_info_doc = {
        "schema_version": "1.0.0",
        "session_id": "Session_001_Example",
        "created_at": "2026-11-20T10:30:00Z",
        "synchronization_metrics": {
            "sync_delta_ms": sync_delta,
            "min_timestamp_ms": min(timestamps),
            "max_timestamp_ms": max(timestamps),
            "note": "SIMULATED: Timestamps reflect synthetic multi-angle acquisition spread."
        },
        "cameras": camera_meta_list
    }
    with open(meta_dir / "capture_info.json", "w", encoding="utf-8") as f:
        json.dump(capture_info_doc, f, indent=4)

    # 2. manifest.json
    manifest_doc = {
        "schema_version": "1.0.0",
        "session_id": "Session_001_Example",
        "capture_datetime": "2026-11-20T10:30:00Z",
        "expected_camera_count": 4,
        "expected_cameras": [c[0] for c in cameras],
        "successfully_captured_cameras": [c[0] for c in cameras],
        "successfully_uploaded_cameras": [c[0] for c in cameras],
        "image_filenames": image_filenames,
        "image_dimensions": image_dimensions,
        "image_file_sizes": image_sizes,
        "capture_status": "COMPLETED",
        "synchronization_delta_ms": sync_delta,
        "errors": []
    }
    with open(session_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest_doc, f, indent=4)

    print(f"Example simulated dataset created at: {session_dir}")

if __name__ == "__main__":
    generate_example_dataset()
