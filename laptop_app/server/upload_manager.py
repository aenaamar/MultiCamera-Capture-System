"""Upload Manager service for streaming image ingestion and strict verification."""
import json
import shutil
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import UploadFile, HTTPException
from laptop_app.config.settings import settings
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager
from laptop_app.models.schemas import CameraState
from laptop_app.utils.file_utils import compute_sha256, validate_image_integrity

class UploadManager:
    def __init__(self):
        # In-memory registry of processed capture request IDs to prevent duplicate uploads
        self._processed_requests: set[str] = set()

    async def process_image_upload(
        self,
        session_id: str,
        camera_id: str,
        capture_request_id: str,
        sha256_checksum: str,
        metadata_json_str: str,
        file: UploadFile
    ) -> Dict[str, Any]:
        """
        Receives captured image file and performs rigorous verification:
        1. Confirms camera is recognized.
        2. Checks for idempotent retries vs duplicate collisions.
        3. Streams bytes into staging file .staging/<camera_id>_<capture_request_id>.tmp.
        4. Validates received byte length > 0.
        5. Calculates SHA-256 and compares with client checksum.
        6. Validates JPEG headers/integrity.
        7. Atomically moves file to final images/<camera_id>.jpg.
        8. Updates session manifest and metadata.
        """
        # Validate device registration
        device = device_manager.get_device(camera_id)
        if not device:
            raise HTTPException(status_code=403, detail=f"Camera ID '{camera_id}' is not registered.")

        # Parse client metadata
        metadata = {}
        if metadata_json_str:
            try:
                metadata = json.loads(metadata_json_str)
            except Exception:
                metadata = {"raw": metadata_json_str}

        # Add capture_request_id to metadata
        metadata["capture_request_id"] = capture_request_id

        session_dir = settings.dataset_dir / session_id
        if not session_dir.exists():
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

        staging_dir = session_dir / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        final_images_dir = session_dir / "images"
        final_images_dir.mkdir(parents=True, exist_ok=True)

        staging_path = staging_dir / f"{camera_id}_{capture_request_id}.tmp"
        final_filename = f"{camera_id}.jpg"
        final_path = final_images_dir / final_filename

        # Deduplication check: if exact request was already completed successfully and file exists
        req_key = f"{session_id}_{camera_id}_{capture_request_id}"
        if req_key in self._processed_requests and final_path.exists():
            existing_sha = compute_sha256(final_path)
            if existing_sha.lower() == sha256_checksum.lower():
                return {
                    "status": "success",
                    "session_id": session_id,
                    "camera_id": camera_id,
                    "filename": final_filename,
                    "file_size": final_path.stat().st_size,
                    "sha256": existing_sha,
                    "note": "Idempotent re-upload: identical file already verified and present."
                }

        try:
            # 1. Stream file into staging path
            bytes_written = 0
            with open(staging_path, "wb") as buffer:
                while True:
                    chunk = await file.read(settings.upload_chunk_size)
                    if not chunk:
                        break
                    buffer.write(chunk)
                    bytes_written += len(chunk)

            if bytes_written == 0:
                staging_path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="Uploaded file is empty (0 bytes).")

            # 2. Check SHA-256 checksum
            computed_sha256 = compute_sha256(staging_path)
            if sha256_checksum and computed_sha256.lower() != sha256_checksum.lower():
                staging_path.unlink(missing_ok=True)
                err_msg = f"Checksum mismatch! Client: {sha256_checksum}, Server: {computed_sha256}"
                await session_manager.record_upload_event(
                    session_id=session_id,
                    camera_id=camera_id,
                    success=False,
                    error_msg=err_msg
                )
                await device_manager.update_status(camera_id, state=CameraState.ERROR)
                raise HTTPException(status_code=422, detail=err_msg)

            # 3. Validate image integrity (JPEG SOI/EOI, PIL decode)
            is_valid, width, height, err = validate_image_integrity(staging_path)
            if not is_valid:
                staging_path.unlink(missing_ok=True)
                err_msg = f"Corrupt image payload: {err}"
                await session_manager.record_upload_event(
                    session_id=session_id,
                    camera_id=camera_id,
                    success=False,
                    error_msg=err_msg
                )
                await device_manager.update_status(camera_id, state=CameraState.ERROR)
                raise HTTPException(status_code=422, detail=err_msg)

            # 4. Atomic rename from staging to final
            shutil.move(str(staging_path), str(final_path))

            # 5. Record verified upload in session and mark request processed
            self._processed_requests.add(req_key)
            await session_manager.record_upload_event(
                session_id=session_id,
                camera_id=camera_id,
                success=True,
                filename=final_filename,
                file_size=final_path.stat().st_size,
                width=width,
                height=height,
                sha256=computed_sha256,
                metadata=metadata
            )

            # Set camera back to READY
            await device_manager.update_status(camera_id, state=CameraState.READY)

            return {
                "status": "success",
                "session_id": session_id,
                "camera_id": camera_id,
                "filename": final_filename,
                "file_size": final_path.stat().st_size,
                "sha256": computed_sha256,
                "width": width,
                "height": height
            }

        except HTTPException:
            raise
        except Exception as e:
            if staging_path.exists():
                staging_path.unlink(missing_ok=True)
            err_msg = f"Upload processing error: {str(e)}"
            await session_manager.record_upload_event(
                session_id=session_id,
                camera_id=camera_id,
                success=False,
                error_msg=err_msg
            )
            await device_manager.update_status(camera_id, state=CameraState.ERROR)
            raise HTTPException(status_code=500, detail=err_msg)

upload_manager = UploadManager()
