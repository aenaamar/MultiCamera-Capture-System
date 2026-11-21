"""Upload Manager service for streaming image ingestion and strict verification."""
import json
import logging
import shutil
import traceback
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import UploadFile, HTTPException
from laptop_app.config.settings import settings
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager
from laptop_app.models.schemas import CameraState
from laptop_app.utils.file_utils import compute_sha256, validate_image_integrity

logger = logging.getLogger("UploadManager")

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
        8. Physically verifies that the file exists and is non-empty at final_path.
        9. Updates session manifest and metadata.
        """
        original_filename = getattr(file, "filename", "unknown")
        logger.info(
            f"[UPLOAD START] session_id={session_id}, camera_id={camera_id}, "
            f"capture_request_id={capture_request_id}, original_filename={original_filename}, "
            f"client_sha256={sha256_checksum}"
        )

        # Validate device registration
        device = device_manager.get_device(camera_id)
        if not device:
            err_msg = f"Camera ID '{camera_id}' is not registered."
            logger.warning(f"[UPLOAD REJECTED] {err_msg}")
            raise HTTPException(status_code=403, detail=err_msg)

        # Parse client metadata
        metadata = {}
        if metadata_json_str:
            try:
                metadata = json.loads(metadata_json_str)
            except Exception:
                metadata = {"raw": metadata_json_str}

        # Add capture_request_id to metadata
        metadata["capture_request_id"] = capture_request_id

        # Resolve session directory from session_manager's dataset_dir (ensuring consistency)
        session_dir = session_manager.dataset_dir / session_id
        if not session_dir.exists():
            # If session directory does not exist on disk, create it so uploads don't get lost
            logger.warning(f"[UPLOAD] Session directory '{session_dir}' did not exist. Auto-creating.")
            session_dir.mkdir(parents=True, exist_ok=True)

        staging_dir = session_dir / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        final_images_dir = session_dir / "images"
        final_images_dir.mkdir(parents=True, exist_ok=True)

        staging_path = staging_dir / f"{camera_id}_{capture_request_id}.tmp"
        final_filename = f"{camera_id}.jpg"
        final_path = final_images_dir / final_filename

        logger.info(f"[UPLOAD PATHS] staging={staging_path}, destination={final_path}")

        # Deduplication check: if exact request was already completed successfully and file exists
        req_key = f"{session_id}_{camera_id}_{capture_request_id}"
        if req_key in self._processed_requests and final_path.exists():
            existing_sha = compute_sha256(final_path)
            if existing_sha.lower() == sha256_checksum.lower():
                logger.info(f"[UPLOAD IDEMPOTENT] Identical file already verified at {final_path}")
                return {
                    "status": "success",
                    "session_id": session_id,
                    "camera_id": camera_id,
                    "filename": final_filename,
                    "file_size": final_path.stat().st_size,
                    "sha256": existing_sha,
                    "destination_path": str(final_path),
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

            logger.info(f"[UPLOAD STAGED] bytes_written={bytes_written} to {staging_path}")

            if bytes_written == 0:
                staging_path.unlink(missing_ok=True)
                err_msg = "Uploaded file is empty (0 bytes)."
                logger.error(f"[UPLOAD ERROR] {err_msg}")
                raise HTTPException(status_code=400, detail=err_msg)

            # 2. Check SHA-256 checksum
            computed_sha256 = compute_sha256(staging_path)
            logger.info(f"[UPLOAD SHA256] computed={computed_sha256}, client={sha256_checksum}")
            if sha256_checksum and computed_sha256.lower() != sha256_checksum.lower():
                staging_path.unlink(missing_ok=True)
                err_msg = f"Checksum mismatch! Client: {sha256_checksum}, Server: {computed_sha256}"
                logger.error(f"[UPLOAD ERROR] {err_msg}")
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
                logger.error(f"[UPLOAD ERROR] {err_msg}")
                await session_manager.record_upload_event(
                    session_id=session_id,
                    camera_id=camera_id,
                    success=False,
                    error_msg=err_msg
                )
                await device_manager.update_status(camera_id, state=CameraState.ERROR)
                raise HTTPException(status_code=422, detail=err_msg)

            logger.info(f"[UPLOAD VALIDATED] Dimensions: {width}x{height}")

            # 4. Atomic rename from staging to final
            shutil.move(str(staging_path), str(final_path))

            # 5. PHYSICAL VERIFICATION: Ensure the file physically exists and size > 0 at final destination
            if not final_path.exists() or final_path.stat().st_size == 0:
                err_msg = f"Critical file system error: File missing or 0 bytes after move to {final_path}"
                logger.critical(f"[UPLOAD CRITICAL] {err_msg}")
                await session_manager.record_upload_event(
                    session_id=session_id,
                    camera_id=camera_id,
                    success=False,
                    error_msg=err_msg
                )
                await device_manager.update_status(camera_id, state=CameraState.ERROR)
                raise HTTPException(status_code=500, detail=err_msg)

            final_file_size = final_path.stat().st_size
            logger.info(
                f"[UPLOAD COMMITTED] final_path={final_path}, size={final_file_size} bytes, "
                f"sha256={computed_sha256}"
            )

            # 6. Record verified upload in session and mark request processed
            self._processed_requests.add(req_key)
            await session_manager.record_upload_event(
                session_id=session_id,
                camera_id=camera_id,
                success=True,
                filename=final_filename,
                file_size=final_file_size,
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
                "file_size": final_file_size,
                "sha256": computed_sha256,
                "width": width,
                "height": height,
                "destination_path": str(final_path)
            }

        except HTTPException:
            raise
        except Exception as e:
            if staging_path.exists():
                staging_path.unlink(missing_ok=True)
            err_msg = f"Upload processing error: {str(e)}"
            logger.error(f"[UPLOAD EXCEPTION] {err_msg}\n{traceback.format_exc()}")
            await session_manager.record_upload_event(
                session_id=session_id,
                camera_id=camera_id,
                success=False,
                error_msg=err_msg
            )
            await device_manager.update_status(camera_id, state=CameraState.ERROR)
            raise HTTPException(status_code=500, detail=err_msg)

upload_manager = UploadManager()
