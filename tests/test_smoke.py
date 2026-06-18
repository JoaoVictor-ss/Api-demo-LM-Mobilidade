"""
Smoke test — confirms both modules import cleanly and the most basic
deterministic behavior works. One assertion per module, zero network.
"""

import webmotors_scraper as wm
import vehicle_search as vs


def test_slugify_anchor():
    """Documented contract: '1.5 i-VTEC FLEX HATCH EXL CVT' → '15-i-vtec-flex-hatch-exl-cvt'."""
    assert wm.slugify("1.5 i-VTEC FLEX HATCH EXL CVT") == "15-i-vtec-flex-hatch-exl-cvt"


def test_to_number_native_float():
    """Native float passes through as-is (no string manipulation)."""
    assert vs._to_number(115900.0) == 115900.0


def test_modules_import_without_env():
    """Both modules are importable with no environment variables set."""
    assert hasattr(wm, "WebmotorsClient")
    assert hasattr(vs, "app")
