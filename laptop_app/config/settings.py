"""Application settings and configuration."""
from pathlib import Path
import socket
from pydantic import BaseModel, Field

def get_local_ip() -> str:
    """Auto-detect primary local IPv4 address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not actually send packets, used to determine outbound interface
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

class AppSettings(BaseModel):
    app_name: str = "MultiCamera Capture System"
    host: str = Field(default_factory=get_local_ip)
    port: int = 8000
    dataset_dir: Path = Path("Datasets").resolve()
    heartbeat_interval_sec: float = 3.0
    heartbeat_timeout_sec: float = 10.0
    upload_chunk_size: int = 1024 * 64  # 64 KB
    max_image_size_bytes: int = 50 * 1024 * 1024  # 50 MB
    allowed_image_types: list[str] = ["image/jpeg", "image/png"]
    version: str = "1.0.0"

settings = AppSettings()
