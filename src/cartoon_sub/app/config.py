from pathlib import Path
import logging

def configure_logging():
    folder = Path.home() / ".cartoon_sub"
    folder.mkdir(exist_ok=True)
    logging.basicConfig(filename=folder / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", encoding="utf-8")
    # HTTP debug logs may include request details. Keep network clients quiet.
    for name in ("google.genai", "google_genai", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
