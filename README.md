# MultiCamera Capture System - Desktop Application

A high-integrity multi-angle smartphone capture and dataset acquisition system designed for 3D Gaussian Splatting (3DGS) reconstruction workflows.

---

## Stage 2: Laptop Application & Local Server

### 1. Requirements & Prerequisites
- Windows 10/11
- Python 3.10+ (Installed: Python 3.13)
- Required packages:
  ```bash
  pip install fastapi uvicorn websockets pydantic pillow python-multipart PySide6 requests pytest
  ```

---

### 2. How to Run the Desktop Application

Open PowerShell or Command Prompt in the repository root (`D:\MultiCamera_Capture_System`):

```powershell
python -m laptop_app.main
```

When started, the application will:
1. Detect your machine's primary local network IP (e.g. `192.168.1.6`).
2. Launch the FastAPI server in the background listening on `http://0.0.0.0:8000` (and `ws://0.0.0.0:8000/ws`).
3. Open the PySide6 modern dark desktop user interface displaying:
   - Real-time connected smartphone camera cards (`camera_01` .. `camera_0N`)
   - Battery indicators and readiness status
   - Session manager controls (`Capture All`, `New Session`, `Open Dataset Folder`)
   - Progress bar and verification log.

---

### 3. Server Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/status` | `GET` | Health status, server IP/port, registered cameras, active session info |
| `/ws` | `WebSocket` | Bi-directional channel for camera registration, heartbeat, and capture triggers |
| `/api/capture` | `POST` | Dispatches simultaneous capture command to all ready cameras |
| `/api/upload` | `POST` | Streaming multipart upload with SHA-256 and JPEG verification |
| `/api/session/new` | `POST` | Creates a new session folder in `Datasets/` |
| `/api/sessions` | `GET` | Lists all existing datasets and manifest statuses |

---

### 4. Running the Stage 2 Automated Verification Suite

To run the verification test suite covering device registration, session creation, corrupt SHA-256 rejection, atomic file staging, and metadata generation:

```powershell
python tests/verify_stage2.py
```
