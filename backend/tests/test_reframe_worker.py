"""Pure geometry checks for the isolated local reframe worker."""
import importlib.util
from pathlib import Path


def test_worker_checks_height_against_the_real_full_bleed_crop_window():
    worker = Path(__file__).parents[1] / "person_worker" / "run_reframe.py"
    spec = importlib.util.spec_from_file_location("reframe_worker", worker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    retained_width, retained_height = module.crop_window_ratios(720, 1280, 1280, 720)

    assert retained_width == 1
    assert retained_height < .4
    assert .8 > retained_height  # A tall subject must become review_required, not be treated as fitting.
