from pathlib import Path
import logging
import os

def configure_logging():
    folder = Path.home() / ".cartoon_sub"
    folder.mkdir(exist_ok=True)
    options = {"level": logging.INFO,
               "format": "%(asctime)s %(levelname)s %(name)s %(message)s", "encoding": "utf-8"}
    try:
        logging.basicConfig(filename=folder / "app.log", **options)
    except PermissionError:
        # A second Windows instance may not be allowed to share the active log file.
        try:
            logging.basicConfig(filename=folder / f"app-{os.getpid()}.log", **options)
        except PermissionError:
            logging.basicConfig(level=logging.INFO, format=options["format"])
    # HTTP debug logs may include request details. Keep network clients quiet.
    for name in ("google.genai", "google_genai", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
