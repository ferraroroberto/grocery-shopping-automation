"""The /perf-review ready selector must name a card the landing view really paints."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _selector() -> str:
    cfg = tomllib.loads((ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    return cfg["perf"]["review"]["ready_selector"]


def test_ready_selector_is_declared_on_the_data_card() -> None:
    assert _selector() == '#this-week[data-state="ready"]'


def test_home_paints_the_declared_card_only_after_the_payload_lands() -> None:
    home = (ROOT / "app/webapp/static/modules/home.js").read_text(encoding="utf-8")
    assert 'id="this-week"' in home
    paint = home.split("export function paintThisWeek()", 1)[1].split("\n}\n", 1)[0]
    guard = paint.index("!state.payload")
    ready = paint.index("dataset.state = LoadState.READY")
    assert guard < ready, "ready must be set after the no-payload early return"
