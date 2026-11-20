"""
DatasetValidator: Independent verification utility for verifying datasets
before feeding into COLMAP or 3D Gaussian Splatting training.
Checks:
- Directory structure completeness
- manifest.json and capture_info.json validity
- File existence for all expected cameras
- Non-empty bytes and valid JPEG SOI/EOI
- Full JPEG pixel decoding validation using Pillow (catches truncated/corrupt scan data)
- SHA-256 match against recorded hashes
- Accurate session completion status
"""
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Tuple
from PIL import Image

class DatasetValidator:
    def __init__(self, datasets_root: str | Path):
        self.root = Path(datasets_root).resolve()

    def validate_session(self, session_id: str) -> Tuple[bool, List[str]]:
        """
        Validates a single session folder.
        Returns (is_valid, list_of_errors_or_warnings).
        """
        errors = []
        session_dir = self.root / session_id
        if not session_dir.exists():
            return False, [f"Session directory does not exist: {session_dir}"]

        images_dir = session_dir / "images"
        meta_dir = session_dir / "metadata"
        manifest_file = session_dir / "manifest.json"
        capture_info_file = meta_dir / "capture_info.json"

        # 1. Structural checks
        if not images_dir.exists():
            errors.append("Missing 'images/' directory")
        if not meta_dir.exists():
            errors.append("Missing 'metadata/' directory")
        if not manifest_file.exists():
            errors.append("Missing 'manifest.json'")
            return False, errors
        if not capture_info_file.exists():
            errors.append("Missing 'metadata/capture_info.json'")

        # 2. Manifest check
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            errors.append(f"Invalid JSON in manifest.json: {str(e)}")
            return False, errors

        expected_cameras = manifest.get("expected_cameras", [])
        uploaded_cameras = manifest.get("successfully_uploaded_cameras", [])
        status = manifest.get("capture_status")

        if status == "COMPLETED" and len(uploaded_cameras) != len(expected_cameras):
            errors.append(
                f"Status is COMPLETED but uploaded count ({len(uploaded_cameras)}) "
                f"!= expected count ({len(expected_cameras)})"
            )

        # 3. Check each expected image with full Pillow decode
        for cam_id in expected_cameras:
            img_path = images_dir / f"{cam_id}.jpg"
            if not img_path.exists():
                errors.append(f"Image file for expected camera '{cam_id}' not found at {img_path}")
                continue

            size = img_path.stat().st_size
            if size == 0:
                errors.append(f"Image file for '{cam_id}' is 0 bytes")
                continue

            # Verify binary markers (SOI 0xFFD8, EOI 0xFFD9)
            try:
                with open(img_path, "rb") as bf:
                    head = bf.read(2)
                    bf.seek(-2, 2)
                    foot = bf.read(2)
                    if head != b"\xff\xd8" or foot != b"\xff\xd9":
                        errors.append(f"Image for '{cam_id}' is corrupt or truncated JPEG (bad magic markers)")
                        continue
            except Exception as e:
                errors.append(f"Could not read bytes for '{cam_id}': {e}")
                continue

            # Full Pillow pixel decode validation: verify() + load() to decompress all scanlines
            try:
                with Image.open(img_path) as im:
                    im.verify()  # Verifies header, tables, markers
                # Reopen to fully decompress raster pixels
                with Image.open(img_path) as im:
                    im.load()    # Forces full decompression of JPEG scan data
            except Exception as exc:
                errors.append(f"Pillow full JPEG decoding failed for '{cam_id}': {exc}")

        # 4. Check capture_info.json SHA-256 against actual files
        if capture_info_file.exists():
            try:
                with open(capture_info_file, "r", encoding="utf-8") as f:
                    cinfo = json.load(f)
                for cam_meta in cinfo.get("cameras", []):
                    cid = cam_meta.get("camera_id")
                    rec_sha = cam_meta.get("sha256")
                    img_f = images_dir / f"{cid}.jpg"
                    if img_f.exists() and rec_sha:
                        actual_sha = self._compute_sha256(img_f)
                        if actual_sha.lower() != rec_sha.lower():
                            errors.append(
                                f"SHA-256 mismatch for '{cid}': recorded {rec_sha} != actual {actual_sha}"
                            )
            except Exception as e:
                errors.append(f"Error parsing metadata/capture_info.json: {e}")

        is_valid = (len(errors) == 0)
        return is_valid, errors

    def validate_all_sessions(self) -> Dict[str, Tuple[bool, List[str]]]:
        """Validate every session in the datasets directory."""
        results = {}
        for p in sorted(self.root.iterdir()):
            if p.is_dir() and p.name.startswith("Session_"):
                results[p.name] = self.validate_session(p.name)
        return results

    @staticmethod
    def _compute_sha256(filepath: Path) -> str:
        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        return sha.hexdigest()

if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "Datasets"
    val = DatasetValidator(root)
    print(f"Validating datasets in: {root}")
    results = val.validate_all_sessions()
    all_ok = True
    for sid, (ok, errs) in results.items():
        if ok:
            print(f"  [PASS] {sid}")
        else:
            all_ok = False
            print(f"  [FAIL] {sid}:")
            for err in errs:
                print(f"         - {err}")
    if not results:
        print("No sessions found to validate.")
    sys.exit(0 if all_ok else 1)
