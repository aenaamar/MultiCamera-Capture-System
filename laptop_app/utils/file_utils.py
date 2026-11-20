"""File and image validation utilities."""
import hashlib
from pathlib import Path
from typing import Tuple, Optional
from PIL import Image

def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()

def validate_image_integrity(file_path: Path) -> Tuple[bool, Optional[int], Optional[int], Optional[str]]:
    """
    Validate that an image file is complete and not corrupt.
    Returns (is_valid, width, height, error_message).
    """
    if not file_path.exists():
        return False, None, None, "File does not exist"
    
    if file_path.stat().st_size == 0:
        return False, None, None, "File is zero bytes (empty)"
    
    # Check JPEG magic bytes if JPEG
    try:
        with open(file_path, "rb") as f:
            header = f.read(2)
            if header == b"\xff\xd8": # JPEG SOI
                f.seek(-2, 2)
                footer = f.read(2)
                if footer != b"\xff\xd9": # JPEG EOI
                    return False, None, None, "JPEG image is truncated (missing EOI 0xFFD9 marker)"
    except Exception as e:
        return False, None, None, f"Failed reading file bytes: {str(e)}"
    
    # Verify using PIL
    try:
        with Image.open(file_path) as img:
            img.verify() # Verify image header and structural integrity
            width, height = img.size
            return True, width, height, None
    except Exception as e:
        return False, None, None, f"Image verification failed: {str(e)}"
