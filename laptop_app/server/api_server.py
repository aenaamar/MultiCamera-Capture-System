"""FastAPI application endpoints for camera registration, commands, and uploads."""
import os
import subprocess
from pathlib import Path
from fastapi import FastAPI, WebSocket, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from laptop_app.config.settings import settings
from laptop_app.services.device_manager import device_manager
from laptop_app.services.session_manager import session_manager
from laptop_app.services.capture_manager import capture_manager
from laptop_app.server.websocket_manager import ws_manager
from laptop_app.server.upload_manager import upload_manager
from laptop_app.utils.async_loop import set_server_loop
import asyncio

app = FastAPI(title=settings.app_name, version=settings.version)

@app.on_event("startup")
async def on_startup():
    loop = asyncio.get_running_loop()
    set_server_loop(loop)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/status")
async def get_system_status():
    """Return overview of server, connected devices, and active session."""
    devices = device_manager.get_all_devices()
    ready_devices = device_manager.get_ready_devices()
    curr_session = session_manager.current_session
    return {
        "status": "ONLINE",
        "host": settings.host,
        "port": settings.port,
        "total_connected": len(devices),
        "ready_cameras": len(ready_devices),
        "devices": [d.dict() for d in devices],
        "current_session": curr_session.dict() if curr_session else None
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time bi-directional channel for camera registration and triggers."""
    await ws_manager.handle_connection(websocket)

@app.post("/api/capture")
async def trigger_capture_all():
    """Trigger simultaneous capture command to all connected ready cameras."""
    res = await capture_manager.trigger_capture_all()
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@app.post("/api/upload")
async def upload_image(
    session_id: str = Form(...),
    camera_id: str = Form(...),
    capture_request_id: str = Form(...),
    sha256_checksum: str = Form(...),
    metadata_json: str = Form(default="{}"),
    file: UploadFile = File(...)
):
    """Receive, verify, and store image with SHA-256 and JPEG validation."""
    return await upload_manager.process_image_upload(
        session_id=session_id,
        camera_id=camera_id,
        capture_request_id=capture_request_id,
        sha256_checksum=sha256_checksum,
        metadata_json_str=metadata_json,
        file=file
    )

@app.post("/api/session/new")
async def new_session():
    """Manually initiate a new session folder."""
    ready_devices = device_manager.get_ready_devices()
    cams = [d.camera_id for d in ready_devices]
    sess = await session_manager.create_session(cams)
    return {"status": "success", "session": sess.dict()}

@app.get("/api/sessions")
async def list_sessions():
    """List all available capture sessions in Datasets directory."""
    if not settings.dataset_dir.exists():
        return {"sessions": []}
    sessions = []
    for p in sorted(settings.dataset_dir.iterdir()):
        if p.is_dir() and p.name.startswith("Session_"):
            manifest_file = p / "manifest.json"
            has_manifest = manifest_file.exists()
            sessions.append({
                "session_id": p.name,
                "path": str(p.resolve()),
                "has_manifest": has_manifest
            })
    return {"sessions": sessions}
