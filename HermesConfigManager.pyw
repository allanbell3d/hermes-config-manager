"""Double-click launcher (Windows: runs without a console window via pythonw)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hermes_config_manager.gui.app import main  # noqa: E402

raise SystemExit(main())
