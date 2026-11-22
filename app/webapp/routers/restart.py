"""The build readout's restart: ``GET``/``POST /api/restart``.

Runs this repo's own ``tray.bat --restart`` through the vendored ``src/self_restart.py``.
Behind the same bearer gate as every other write route (the PC itself bypasses it), and
``require_authenticated`` makes the route refuse when that gate is missing.
"""

from app.middleware import require_authenticated
from app.webapp.routers.usage import APP_SLUG
from src.runtime_data import runtime_data_dir
from src.self_restart import TRAY_RESTART, RestartTarget, restart_router

# Like the usage log's file, resolving the folder creates nothing: the run record
# creates it on the first restart.
router = restart_router(
    targets=[RestartTarget("app", "Restart app", TRAY_RESTART)],
    status_dir=runtime_data_dir(APP_SLUG) / "restart",
    guard=require_authenticated,
)
