"""Windows GUI launcher. Double-click this file to run without a terminal."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))

from cartoon_sub.app.main import main

raise SystemExit(main())
