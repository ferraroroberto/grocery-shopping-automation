"""Local-only usage log: ``POST /api/usage/events`` + ``GET /api/usage/summary``.

The vendored ``src/usage_log.py`` owns the contract (kinds, id pattern, device
classes — no content, names, queries or IPs); this module only picks where the
SQLite file lives. It is resolved through the fleet's ``runtime_data`` helper,
``<root>/grocery-shopping-automation/usage.sqlite3`` — the path
project-scaffolding's ``scripts/usage_summary.py --app grocery-shopping-automation``
reads. ``GROCERY_SHOPPING_AUTOMATION_DATA_DIR`` redirects it (tests and the
synthetic design-review instance set it so they never touch the real file).

The router needs no ``dependencies=``: ``BearerTokenMiddleware`` already guards
every non-exempt path, loopback bypass included.
"""

from src.runtime_data import runtime_db_path
from src.usage_log import usage_router

APP_SLUG = "grocery-shopping-automation"

# No `create=True`: importing the app must not create a directory (a test run
# or a stripped checkout would litter the data root); `UsageLog` creates its
# parent on the first write.
router = usage_router(runtime_db_path(APP_SLUG, "usage.sqlite3"))
