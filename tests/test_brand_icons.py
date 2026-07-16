"""Contracts for the generated Grocery application-icon family."""

from PIL import Image

from app.tray.tray import PROJECT_ROOT, _build_icon

STATIC_DIR = PROJECT_ROOT / "app" / "static"


def test_generated_pngs_are_opaque_rgb_at_expected_sizes() -> None:
    expected = {
        "icon-180.png": (180, 180),
        "icon-192.png": (192, 192),
        "icon-512.png": (512, 512),
        "icon-512-maskable.png": (512, 512),
    }

    for filename, size in expected.items():
        with Image.open(STATIC_DIR / filename) as image:
            assert image.size == size
            assert image.mode == "RGB"


def test_external_surface_assets_exist() -> None:
    expected = (
        STATIC_DIR / "favicon.ico",
        PROJECT_ROOT / "assets" / "tray" / "grocery-shopping-automation.ico",
        PROJECT_ROOT
        / "assets"
        / "stream-deck"
        / "grocery-shopping-automation-144.png",
    )
    assert all(path.is_file() for path in expected)


def test_tray_loads_generated_asset() -> None:
    icon = _build_icon()
    assert icon.mode == "RGBA"
    assert icon.size[0] >= 32
    assert icon.size[1] >= 32
