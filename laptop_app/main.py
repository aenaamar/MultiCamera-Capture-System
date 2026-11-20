"""Entrypoint for the Laptop Application and FastAPI Server."""
import sys
import threading
import uvicorn
from PySide6.QtWidgets import QApplication
from laptop_app.config.settings import settings
from laptop_app.server.api_server import app
from laptop_app.ui.main_window import MainWindow

def run_server():
    """Runs the FastAPI server in a dedicated daemon thread."""
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")

def main():
    # 1. Start server thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 2. Launch Qt UI
    qt_app = QApplication(sys.argv)
    window = MainWindow()
    window.show()

    sys.exit(qt_app.exec())

if __name__ == "__main__":
    main()
