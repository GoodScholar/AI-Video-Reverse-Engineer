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


def test_worker_keeps_early_cfr_frame_pts_and_rejects_repeated_frames():
    worker = Path(__file__).parents[1] / "person_worker" / "run_reframe.py"
    spec = importlib.util.spec_from_file_location("reframe_worker_pts", worker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.cfr_frame_time(23, 24, 10) == 23 / 24
    assert module.cfr_frame_time(23, 24, 23) is None


def test_worker_selects_real_pts_intersections_not_index_divided_by_fps():
    worker = Path(__file__).parents[1] / "person_worker" / "run_reframe.py"
    spec = importlib.util.spec_from_file_location("reframe_worker_pts_selection", worker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    selected = module.select_sample_indices([
        {"elapsedSeconds": 0.0, "durationSeconds": 1 / 24},
        {"elapsedSeconds": .5, "durationSeconds": 1 / 24},
        {"elapsedSeconds": 1.0, "durationSeconds": 1 / 24},
        {"elapsedSeconds": 1.5, "durationSeconds": 1 / 24},
        {"elapsedSeconds": 1.958333, "durationSeconds": 1 / 24},
    ], 0.1, 1.99, 2.0)

    assert selected == [1, 2, 3, 4]


def test_worker_uses_the_ffprobe_microsecond_boundary_for_the_last_half_open_frame():
    """30fps PTS strings must keep 2.066667's display interval ending at 2.1."""
    worker = Path(__file__).parents[1] / "person_worker" / "run_reframe.py"
    spec = importlib.util.spec_from_file_location("reframe_worker_half_open_boundary", worker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    selected = module.select_sample_indices([
        {"elapsedSeconds": 0.0, "durationSeconds": .033333},
        {"elapsedSeconds": 4.033334 - 2.0, "durationSeconds": .033333},
        {"elapsedSeconds": 4.066667 - 2.0, "durationSeconds": .033333},
        {"elapsedSeconds": 4.1 - 2.0, "durationSeconds": .033333},
    ], 2.0, 2.1, 2.0)

    assert selected == [1, 2]


def test_worker_keeps_every_real_frame_needed_for_manual_packaging_coverage():
    """A 2 Hz trajectory sample cannot stand in for unobserved subject frames."""
    worker = Path(__file__).parents[1] / "person_worker" / "run_reframe.py"
    spec = importlib.util.spec_from_file_location("reframe_worker_packaging_coverage", worker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frames = [
        {"elapsedSeconds": index * .04, "durationSeconds": .04}
        for index in range(4)
    ]

    assert module.select_sample_indices(frames, .01, .15, 2.0) == [0, 1, 2, 3]
    # Timeline clips permit 300 output seconds at 4x speed.  The reframe
    # contract supports CFR sources through 60fps, so the full legal source
    # interval is 1,200 seconds / 72,000 display frames rather than 9,000.
    long_frames = [
        {"elapsedSeconds": index / 60, "durationSeconds": 1 / 60}
        for index in range(72_000)
    ]
    complete = module.select_sample_indices(long_frames, 0, 1_200, 2.0)
    assert len(complete) == 72_000 == module.MAX_VIDEO_SAMPLES


def test_worker_retains_a_conservative_obstacle_envelope_for_packaging():
    worker = Path(__file__).parents[1] / "person_worker" / "run_reframe.py"
    spec = importlib.util.spec_from_file_location("reframe_worker_obstacles", worker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    person = {"x": .4, "y": .1, "width": .2, "height": .7, "label": "person", "confidence": .9}
    product = {"x": .05, "y": .4, "width": .2, "height": .2, "label": "bottle", "confidence": .8}
    overlay = {"x": .75, "y": .3, "width": .2, "height": .4, "label": "screen", "confidence": .7}

    retained = module.packaging_boxes([person, product, overlay], person)

    assert retained[0] == person
    assert retained[1] == {"x": .05, "y": .3, "width": .9, "height": .4,
                           "label": "detected-obstacle", "confidence": .8}
