"""
DatasetReader: Standalone Python utility for the 3DGS reconstruction teammate.
Allows downstream pipelines to query and load captured sessions, images, and metadata
without importing or depending on the internal laptop application.
"""
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional

class DatasetReader:
    """
    Zero-dependency dataset reader for MultiCamera Capture System output.
    """
    def __init__(self, datasets_root: str | Path):
        self.root = Path(datasets_root).resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"Datasets root folder not found: {self.root}")

    def list_sessions(self, completed_only: bool = False) -> List[str]:
        """Return sorted list of session IDs."""
        sessions = []
        for p in sorted(self.root.iterdir()):
            if p.is_dir() and p.name.startswith("Session_"):
                if completed_only:
                    manifest_path = p / "manifest.json"
                    if manifest_path.exists():
                        try:
                            with open(manifest_path, "r", encoding="utf-8") as f:
                                manifest = json.load(f)
                            if manifest.get("capture_status") == "COMPLETED":
                                sessions.append(p.name)
                        except Exception:
                            pass
                else:
                    sessions.append(p.name)
        return sessions

    def get_session_dir(self, session_id: str) -> Path:
        """Get absolute path to a session directory."""
        sdir = self.root / session_id
        if not sdir.exists():
            raise FileNotFoundError(f"Session '{session_id}' not found in {self.root}")
        return sdir

    def get_manifest(self, session_id: str) -> Dict[str, Any]:
        """Read and parse manifest.json for a session."""
        mpath = self.get_session_dir(session_id) / "manifest.json"
        if not mpath.exists():
            raise FileNotFoundError(f"Manifest missing for session '{session_id}'")
        with open(mpath, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_capture_info(self, session_id: str) -> Dict[str, Any]:
        """Read metadata/capture_info.json containing per-camera sensor & exposure info."""
        cpath = self.get_session_dir(session_id) / "metadata" / "capture_info.json"
        if not cpath.exists():
            raise FileNotFoundError(f"capture_info.json missing for session '{session_id}'")
        with open(cpath, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_image_paths(self, session_id: str) -> Dict[str, Path]:
        """
        Return mapping of camera_id -> absolute Path to image file.
        e.g. {'camera_01': Path('/.../Session_001/images/camera_01.jpg')}
        """
        images_dir = self.get_session_dir(session_id) / "images"
        if not images_dir.exists():
            return {}
        res = {}
        for img in sorted(images_dir.glob("*.jpg")):
            cam_id = img.stem  # e.g. camera_01
            res[cam_id] = img
        return res

    def get_synchronization_stats(self, session_id: str) -> Dict[str, Any]:
        """Return capture timestamp synchronization stats across cameras."""
        info = self.get_capture_info(session_id)
        return info.get("synchronization_metrics", {})
