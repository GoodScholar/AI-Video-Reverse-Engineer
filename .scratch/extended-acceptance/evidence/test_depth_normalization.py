import importlib.util
from pathlib import Path
import pytest

def load_worker_module():
    worker = '/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/depth_worker/run_depth.py'
    spec = importlib.util.spec_from_file_location("isolated_depth_worker_for_fix", worker)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_worker_normalizes_the_whole_clip_with_p2_p98_clipping_and_a_deterministic_degenerate_span():
    worker = load_worker_module()
    np = pytest.importorskip("numpy", reason="隔离 worker 环境提供 NumPy")

    worker.np = np
    normalized = worker._normalize_depths(np.asarray([[[0.0]], [[10.0]], [[20.0]], [[100.0]]], dtype=np.float32))

    assert normalized[:, 0, 0].tolist() == pytest.approx([0.0, 0.0993658, 0.2050740, 1.0])
    assert worker._normalize_depths(np.ones((2, 2, 2), dtype=np.float32)).tolist() == [[[0.5, 0.5], [0.5, 0.5]], [[0.5, 0.5], [0.5, 0.5]]]