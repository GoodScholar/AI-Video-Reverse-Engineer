import importlib.util
import json
import shutil
import stat
import subprocess
import sys
from array import array
from dataclasses import replace
from pathlib import Path

import pytest

from app.depth_capture_storage import DEPTH_ARTIFACTS
from app.depth_capture_runner import DepthCaptureFailure, DepthCaptureRequest, run_depth_capture


FIXTURES = Path(__file__).parent / "fixtures" / "depth"
def fake_request(tmp_path: Path, device: str = "cpu") -> DepthCaptureRequest:
    data_dir = tmp_path / "data"
    source = data_dir / "project-files/project-1/reference-videos/video-1.mp4"
    checkpoint = tmp_path / "checkpoint.pth"
    upstream = tmp_path / "upstream"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    checkpoint.write_bytes(b"checkpoint")
    upstream.mkdir()
    return DepthCaptureRequest(
        data_dir=data_dir,
        project_id="project-1",
        capture_id="capture-1",
        source_reference_video_id="video-1",
        input_video=source,
        checkpoint=checkpoint,
        upstream_root=upstream,
        execution_device=device,
        expected_duration_seconds=0.25,
    )


def test_capture_request_requires_a_trusted_positive_expected_duration(tmp_path):
    with pytest.raises(TypeError):
        DepthCaptureRequest(
            data_dir=tmp_path / "data", project_id="project-1", capture_id="capture-1",
            source_reference_video_id="video-1", input_video=tmp_path / "input.mp4",
            checkpoint=tmp_path / "checkpoint.pth", upstream_root=tmp_path / "upstream",
            execution_device="cpu",
        )

    with pytest.raises(ValueError, match="expected_duration_seconds"):
        DepthCaptureRequest(
            data_dir=tmp_path / "data", project_id="project-1", capture_id="capture-1",
            source_reference_video_id="video-1", input_video=tmp_path / "input.mp4",
            checkpoint=tmp_path / "checkpoint.pth", upstream_root=tmp_path / "upstream",
            execution_device="cpu", expected_duration_seconds=float("nan"),
        )


def fake_ffmpeg(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-ffmpeg.py"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "audit = os.environ.get('FAKE_FFMPEG_AUDIT')\n"
        "if audit:\n"
        "    path = pathlib.Path(audit)\n"
        "    entries = json.loads(path.read_text()) if path.exists() else []\n"
        "    entries.append(sys.argv[1:])\n"
        "    path.write_text(json.dumps(entries))\n"
        "if os.environ.get('FAKE_FFMPEG_MODE') == 'failure':\n"
        "    print('secret encoding detail', file=sys.stderr)\n"
        "    raise SystemExit(9)\n"
        "pathlib.Path(sys.argv[-1]).write_bytes(b'encoded')\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def run_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: str = "success"):
    monkeypatch.setenv("FAKE_WORKER_MODE", mode)
    return run_depth_capture(
        request=fake_request(tmp_path),
        worker_python=sys.executable,
        worker_script=FIXTURES / "fake_worker.py",
        ffmpeg_path=str(fake_ffmpeg(tmp_path)),
    )


def test_runner_uses_argument_arrays_encodes_both_variants_and_commits_artifacts(tmp_path, monkeypatch):
    worker_audit = tmp_path / "worker-argv.json"
    ffmpeg_audit = tmp_path / "ffmpeg-argv.json"
    monkeypatch.setenv("FAKE_WORKER_AUDIT", str(worker_audit))
    monkeypatch.setenv("FAKE_FFMPEG_AUDIT", str(ffmpeg_audit))

    result = run_fake(tmp_path, monkeypatch)

    assert result.executionDevice == "cpu"
    assert {path.name for path in result.directory.iterdir()} == set(DEPTH_ARTIFACTS)
    assert json.loads((result.directory / "manifest.json").read_text(encoding="utf-8"))["sourceReferenceVideoId"] == "video-1"
    worker_argv = json.loads(worker_audit.read_text(encoding="utf-8"))
    assert worker_argv[0] == "--input"
    assert Path(worker_argv[1]).name == ".source-cfr.mp4"
    assert worker_argv[2] == "--output"
    assert Path(worker_argv[3]).name == "worker-output"
    assert worker_argv[4:] == [
        "--checkpoint", str(tmp_path / "checkpoint.pth"), "--upstream-root", str(tmp_path / "upstream"),
        "--device", "cpu", "--target-fps", "8", "--input-size", "350", "--max-res", "640",
        "--output-short-side", "480", "--backbone-microbatch", "2",
    ]
    commands = json.loads(ffmpeg_audit.read_text(encoding="utf-8"))
    assert len(commands) == 3
    assert "-vf" in commands[0] and commands[0][commands[0].index("-vf") + 1] == "fps=8,scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2"
    for command in commands[1:]:
        assert "-an" in command and command[command.index("-c:v") + 1] == "libx264"
        assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert commands[2][commands[2].index("-vf") + 1] == "format=rgb24,pseudocolor=preset=turbo,format=rgb24"


@pytest.mark.parametrize("extension", ["mp4", "mov"])
def test_runner_accepts_current_managed_media_directory(tmp_path, monkeypatch, extension):
    request = fake_request(tmp_path)
    source = request.input_video.parent.parent / "reference-media" / f"video-1.{extension}"
    source.parent.mkdir()
    request.input_video.rename(source)
    request = replace(request, input_video=source)
    monkeypatch.setenv("FAKE_WORKER_MODE", "success")

    result = run_depth_capture(
        request, sys.executable, FIXTURES / "fake_worker.py", str(fake_ffmpeg(tmp_path)),
    )

    assert result.qualityAssessment.status in {"passed", "review_required"}
    assert (result.directory / "manifest.json").is_file()


def test_runner_commits_exact_returned_quality_assessment_json(tmp_path, monkeypatch):
    result = run_fake(tmp_path, monkeypatch)

    assert result.qualityAssessment.status in {"passed", "review_required"}
    assert json.loads((result.directory / "depth-quality.json").read_text(encoding="utf-8")) == result.qualityAssessment.model_dump(mode="json")


def test_runner_commits_failed_quality_assessment_without_turning_it_into_worker_failure(tmp_path, monkeypatch):
    result = run_fake(tmp_path, monkeypatch, mode="constant")

    assert result.qualityAssessment.status == "failed"
    assert (result.directory / "depth-quality.json").is_file()


def test_runner_emits_stage_callbacks_at_real_execution_boundaries(tmp_path, monkeypatch):
    events = []

    run_depth_capture(
        request=fake_request(tmp_path),
        worker_python=sys.executable,
        worker_script=FIXTURES / "fake_worker.py",
        ffmpeg_path=str(fake_ffmpeg(tmp_path)),
        on_stage_started=lambda stage: events.append(("start", stage)),
        on_stage_completed=lambda stage: events.append(("complete", stage)),
    )

    assert events == [
        ("start", "preparing"), ("complete", "preparing"),
        ("start", "estimatingDepth"), ("complete", "estimatingDepth"),
        ("start", "encoding"), ("complete", "encoding"),
        ("start", "qualityAssessment"), ("complete", "qualityAssessment"),
    ]


def test_runner_rejects_non_finite_or_out_of_range_source_motion_metadata(tmp_path, monkeypatch):
    with pytest.raises(DepthCaptureFailure) as error:
        run_fake(tmp_path, monkeypatch, mode="bad-motion")

    assert error.value.code == "depth_worker_output_invalid"


def test_runner_rejects_missing_source_motion_metadata_with_stable_code_and_cleanup(tmp_path, monkeypatch):
    with pytest.raises(DepthCaptureFailure) as error:
        run_fake(tmp_path, monkeypatch, mode="missing-motion")

    assert error.value.code == "depth_worker_output_invalid"
    parent = tmp_path / "data/project-files/project-1/depth-captures"
    assert not parent.exists() or not list(parent.glob(".capture-1-*"))


@pytest.mark.parametrize("device,profile", [("mps", ("8", "350", "640", "480", "1")), ("cuda", ("16", "350", "640", "480", "2"))])
def test_runner_selects_the_fixed_device_profile(tmp_path, monkeypatch, device, profile):
    audit = tmp_path / "worker-argv.json"
    monkeypatch.setenv("FAKE_WORKER_AUDIT", str(audit))
    request = fake_request(tmp_path, device=device)
    run_depth_capture(request, sys.executable, FIXTURES / "fake_worker.py", str(fake_ffmpeg(tmp_path)))

    argv = json.loads(audit.read_text(encoding="utf-8"))
    assert (argv[argv.index("--target-fps") + 1], argv[argv.index("--input-size") + 1], argv[argv.index("--max-res") + 1], argv[argv.index("--output-short-side") + 1], argv[argv.index("--backbone-microbatch") + 1]) == profile


def test_runner_uses_a_bounded_720p_profile_distinct_from_480p(tmp_path, monkeypatch):
    audit = tmp_path / "worker-argv.json"
    monkeypatch.setenv("FAKE_WORKER_AUDIT", str(audit))
    request = replace(fake_request(tmp_path), output_resolution="720p")

    run_depth_capture(request, sys.executable, FIXTURES / "fake_worker.py", str(fake_ffmpeg(tmp_path)))

    argv = json.loads(audit.read_text(encoding="utf-8"))
    assert (argv[argv.index("--input-size") + 1], argv[argv.index("--max-res") + 1], argv[argv.index("--output-short-side") + 1], argv[argv.index("--backbone-microbatch") + 1]) == ("518", "1280", "720", "2")


@pytest.mark.parametrize("mode,code", [
    ("timeout", "depth_capture_timeout"),
    ("oom", "depth_device_out_of_memory"),
    ("unavailable", "depth_device_unavailable"),
    ("failure", "depth_worker_failed"),
    ("malformed", "depth_worker_output_invalid"),
    ("bad-metadata", "depth_worker_output_invalid"),
])
def test_runner_maps_worker_failures_without_leaking_stderr_and_cleans_workspace(tmp_path, monkeypatch, mode, code):
    if mode == "timeout":
        monkeypatch.setattr("app.depth_capture_runner.DEPTH_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(DepthCaptureFailure) as error:
        run_fake(tmp_path, monkeypatch, mode=mode)

    assert error.value.code == code
    assert "internal worker detail" not in error.value.message
    parent = tmp_path / "data/project-files/project-1/depth-captures"
    assert not parent.exists() or not list(parent.glob(".capture-1-*"))


def test_runner_maps_ffmpeg_failure_and_never_commits_partial_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "failure")

    with pytest.raises(DepthCaptureFailure) as error:
        run_fake(tmp_path, monkeypatch)

    assert error.value.code == "depth_preparation_failed"
    assert "secret encoding detail" not in error.value.message
    assert not (tmp_path / "data/project-files/project-1/depth-captures/capture-1").exists()


def test_worker_cli_parser_is_import_safe_and_rejects_unsupported_profile_value():
    worker = Path(__file__).parents[1] / "depth_worker" / "run_depth.py"
    spec = importlib.util.spec_from_file_location("isolated_depth_worker", worker)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(SystemExit):
        module.build_parser().parse_args([
            "--input", "input.mp4", "--output", "out", "--checkpoint", "checkpoint.pth",
            "--upstream-root", "upstream", "--device", "cpu", "--target-fps", "7",
            "--input-size", "350", "--max-res", "640",
        ])


def load_worker_module():
    worker = Path(__file__).parents[1] / "depth_worker" / "run_depth.py"
    spec = importlib.util.spec_from_file_location("isolated_depth_worker_for_fix", worker)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_mps_compat_avoids_torch_211_autocast_and_restores_it():
    worker = load_worker_module()

    class FakeTorch:
        def __init__(self):
            self.calls = []
            self.mps = type("MPS", (), {
                "set_per_process_memory_fraction": lambda self, value: None,
                "synchronize": lambda self: None,
                "empty_cache": lambda self: None,
            })()

        def autocast(self, *, device_type, enabled=True):
            self.calls.append((device_type, enabled))
            if device_type == "mps":
                raise AssertionError("Torch 2.1.1 MPS autocast must be bypassed")
            return _Context()

    class Model:
        def __init__(self, torch):
            self.torch = torch
            self.arguments = None

        def infer_video_depth(self, frames, fps, *, input_size, device, fp32):
            self.arguments = (frames, fps, input_size, device, fp32)
            with self.torch.autocast(device_type=device, enabled=not fp32):
                pass
            return [0.0, 1.0], fps

    torch = FakeTorch()
    model = Model(torch)
    original = torch.autocast
    _, fps = worker._infer_depths(torch, model, ["frame"], 8.0, 350, "mps")

    assert fps == 8.0
    assert model.arguments == (["frame"], 8.0, 350, "mps", True)
    assert torch.calls == []
    assert torch.autocast.__func__ is original.__func__


@pytest.mark.parametrize("fails", [False, True])
def test_worker_bounds_mps_memory_and_releases_cache_after_each_chunk(fails):
    from types import SimpleNamespace
    worker = load_worker_module()
    events = []
    original_autocast = object()
    torch = SimpleNamespace(autocast=original_autocast, mps=SimpleNamespace(
        set_per_process_memory_fraction=lambda fraction: events.append(("limit", fraction)),
        synchronize=lambda: events.append("synchronize"),
        empty_cache=lambda: events.append("empty_cache"),
    ))

    def infer(*args, **kwargs):
        events.append("infer")
        if fails:
            raise RuntimeError("MPS out of memory")
        return [0.0, 1.0], 8.0

    model = SimpleNamespace(infer_video_depth=infer)
    if fails:
        with pytest.raises(RuntimeError, match="MPS out of memory"):
            worker._infer_depths(torch, model, ["frame"], 8.0, 518, "mps")
    else:
        assert worker._infer_depths(torch, model, ["frame"], 8.0, 518, "mps") == ([0.0, 1.0], 8.0)
    assert events == [("limit", 0.5), "infer", "synchronize", "empty_cache"]
    assert torch.autocast is original_autocast


def test_worker_cuda_keeps_original_autocast_and_fp16_behavior():
    worker = load_worker_module()

    class FakeTorch:
        def __init__(self):
            self.calls = []

        def autocast(self, *, device_type, enabled=True):
            self.calls.append((device_type, enabled))
            return _Context()

    class Model:
        def __init__(self, torch):
            self.torch = torch
            self.arguments = None

        def infer_video_depth(self, frames, fps, *, input_size, device, fp32):
            self.arguments = (frames, fps, input_size, device, fp32)
            with self.torch.autocast(device_type=device, enabled=not fp32):
                pass
            return [0.0, 1.0], fps

    torch = FakeTorch()
    model = Model(torch)
    worker._infer_depths(torch, model, ["frame"], 16.0, 518, "cuda")

    assert model.arguments == (["frame"], 16.0, 518, "cuda", False)
    assert torch.calls == [("cuda", True)]


def test_worker_microbatches_only_backbone_features_and_preserves_frame_order():
    worker = load_worker_module()

    class FakeTorch:
        @staticmethod
        def cat(values, dim=0):
            return sum(values, [])

    class Pretrained:
        def __init__(self):
            self.calls = []

        def get_intermediate_layers(self, frames, *args, **kwargs):
            self.calls.append(list(frames))
            return [([f"a-{frame}" for frame in frames], [f"ca-{frame}" for frame in frames]), ([f"b-{frame}" for frame in frames], [f"cb-{frame}" for frame in frames])]

    pretrained = Pretrained()
    model = type("Model", (), {"pretrained": pretrained})()

    worker._install_backbone_microbatch(model, FakeTorch(), 2)
    features = pretrained.get_intermediate_layers([0, 1, 2, 3, 4], "layers", return_class_token=True)

    assert pretrained.calls == [[0, 1], [2, 3], [4]]
    assert features == (
        (["a-0", "a-1", "a-2", "a-3", "a-4"], ["ca-0", "ca-1", "ca-2", "ca-3", "ca-4"]),
        (["b-0", "b-1", "b-2", "b-3", "b-4"], ["cb-0", "cb-1", "cb-2", "cb-3", "cb-4"]),
    )


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False


def test_worker_relative_depth_normalization_maps_larger_nearer_values_to_white():
    worker = load_worker_module()

    assert [worker._relative_scale(value, 2.0, 4.0) for value in (2.0, 3.0, 4.0)] == [0.0, 0.5, 1.0]


def test_worker_normalizes_the_whole_clip_with_p2_p98_clipping_and_a_deterministic_degenerate_span():
    worker = load_worker_module()
    np = pytest.importorskip("numpy", reason="隔离 worker 环境提供 NumPy")

    worker.np = np
    normalized = worker._normalize_depths(np.asarray([[[0.0]], [[10.0]], [[20.0]], [[100.0]]], dtype=np.float32))

    assert normalized[:, 0, 0].tolist() == pytest.approx([0.0, 0.0993658, 0.2050740, 1.0])
    assert worker._normalize_depths(np.ones((2, 2, 2), dtype=np.float32)).tolist() == [[[0.5, 0.5], [0.5, 0.5]], [[0.5, 0.5], [0.5, 0.5]]]


def test_worker_production_normalization_path_uses_percentile_clip_and_constant_output_without_numpy_dependency():
    worker = load_worker_module()

    class Tensor:
        ndim = 3
        dtype = "float32"

        def __init__(self, values):
            self.values = list(values)
            self.size = len(values)

        def astype(self, _dtype, copy=False):
            return self

        def __sub__(self, value):
            return Tensor([item - value for item in self.values])

        def __truediv__(self, value):
            return Tensor([item / value for item in self.values])

    class Finite:
        def all(self):
            return True

    class FakeNumpy:
        float32 = "float32"
        number = "number"

        def __init__(self):
            self.calls = []

        def issubdtype(self, dtype, number):
            return dtype == "float32" and number == "number"

        def isfinite(self, tensor):
            self.calls.append(("isfinite", tensor.values))
            return Finite()

        def percentile(self, tensor, percentiles):
            self.calls.append(("percentile", tensor.values, percentiles))
            return (tensor.values[0], tensor.values[0]) if len(set(tensor.values)) == 1 else (0.6, 95.2)

        def clip(self, tensor, low, high):
            self.calls.append(("clip", low, high))
            return Tensor([max(low, min(high, value)) for value in tensor.values])

        def full_like(self, tensor, value, dtype):
            self.calls.append(("full_like", value, dtype))
            return Tensor([value] * tensor.size)

    fake = FakeNumpy()
    normalized = worker._normalize_depths(Tensor([0.0, 10.0, 20.0, 100.0]), numpy_module=fake)
    degenerate = worker._normalize_depths(Tensor([1.0, 1.0]), numpy_module=fake)

    assert normalized.values == pytest.approx([0.0, 0.0993658, 0.2050740, 1.0])
    assert degenerate.values == [0.5, 0.5]
    assert ("percentile", [0.0, 10.0, 20.0, 100.0], (2.0, 98.0)) in fake.calls
    assert ("clip", 0.0, 1.0) in fake.calls
    assert ("full_like", 0.5, "float32") in fake.calls


def test_worker_production_motion_path_uses_rgb_diff_mean_clamp_and_final_worker_fps_without_numpy_dependency():
    worker = load_worker_module()

    class Frame:
        def __init__(self, values):
            self.values = list(values)

        def astype(self, _dtype):
            return self

        def __sub__(self, other):
            return Frame([left - right for left, right in zip(self.values, other.values)])

        def mean(self):
            return sum(self.values) / len(self.values)

    class FakeNumpy:
        float32 = "float32"

        def __init__(self):
            self.abs_calls = 0

        def abs(self, frame):
            self.abs_calls += 1
            return Frame([abs(value) for value in frame.values])

    fake = FakeNumpy()
    samples = worker._source_motion_samples([Frame([0.0]), Frame([51.0]), Frame([-510.0])], 12.0, numpy_module=fake)

    assert samples == [
        {"timestampSeconds": 1.0 / 12.0, "magnitude": 0.2},
        {"timestampSeconds": 2.0 / 12.0, "magnitude": 1.0},
    ]
    assert fake.abs_calls == 2


@pytest.mark.parametrize("source_fps,target_fps,frame_count,expected_count", [
    (20.0, 16, 40, 32),
    (30.0, 8, 60, 16),
    (29.97, 8, 60, 16),
    (23.976, 8, 48, 16),
])
def test_worker_uses_time_based_sampling_without_duplicates(source_fps, target_fps, frame_count, expected_count):
    worker = load_worker_module()

    indices, effective_fps = worker._sample_frame_indices(source_fps, target_fps, frame_count)

    assert effective_fps == target_fps
    assert len(indices) == expected_count
    assert len(indices) == len(set(indices))


def test_runner_limits_duration_using_actual_worker_frame_rate(tmp_path):
    from app import depth_capture_runner as runner

    request = fake_request(tmp_path)
    profile = runner._DeviceProfile(target_fps=8, input_size=350, max_res=640, output_short_side=480, backbone_microbatch=2)
    depths = runner._DepthFrames(array("f", [0.0, 1.0] * 4800), 2400, 480, 854)
    metadata = {
        "schemaVersion": 1,
        "modelIdentity": runner.MODEL_IDENTITY,
        "device": "cpu",
        "targetFps": 8,
        "inputSize": 350,
            "maxRes": 640,
            "outputShortSide": 480,
            "backboneMicrobatch": 2,
            "frameCount": 2400,
            "width": 854,
            "height": 480,
        "frameRate": 1.0,
        "depthMin": 0.0,
        "depthMax": 1.0,
        "finite": True,
        "normalizationDirection": "near_white_far_black",
        "normalizationPercentilePolicy": runner.NORMALIZATION_PERCENTILE_POLICY,
    }

    with pytest.raises(DepthCaptureFailure, match="范围无效"):
        runner._validate_worker_metadata(metadata, depths, request, profile)
    metadata["frameRate"] = 8.0
    runner._validate_worker_metadata(metadata, depths, request, profile)


@pytest.mark.parametrize("mode", [
    "missing", "bad-zip", "bad-header-zero", "bad-header-one", "odd", "too-large", "too-large-fps",
])
def test_runner_rejects_unsafe_or_non_normalized_depth_output(tmp_path, monkeypatch, mode):
    with pytest.raises(DepthCaptureFailure) as error:
        run_fake(tmp_path, monkeypatch, mode=mode)

    assert error.value.code == "depth_worker_output_invalid"
    parent = tmp_path / "data/project-files/project-1/depth-captures"
    assert not parent.exists() or not list(parent.glob(".capture-1-*"))


@pytest.mark.parametrize("outside_name", ["outside.mp4", "data/project-files/project-1/reference-videos/video-2.mp4",
    "data/project-files/project-1/reference-media/video-2.mp4",
    "data/project-files/project-2/reference-media/video-1.mp4",
    "data/project-files/project-1/reference-media/video-1.png"])
def test_runner_rejects_source_file_outside_managed_reference_location(tmp_path, monkeypatch, outside_name):
    request = fake_request(tmp_path)
    outside = tmp_path / outside_name
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"outside")
    request = DepthCaptureRequest(
        data_dir=request.data_dir,
        project_id=request.project_id,
        capture_id=request.capture_id,
        source_reference_video_id=request.source_reference_video_id,
        input_video=outside,
        checkpoint=request.checkpoint,
        upstream_root=request.upstream_root,
        execution_device="cpu",
        expected_duration_seconds=request.expected_duration_seconds,
    )

    with pytest.raises(DepthCaptureFailure) as error:
        run_depth_capture(request, sys.executable, FIXTURES / "fake_worker.py", str(fake_ffmpeg(tmp_path)))

    assert error.value.code == "depth_input_invalid"


def test_runner_rejects_unknown_explicit_device_without_fallback(tmp_path):
    request = fake_request(tmp_path)
    request = DepthCaptureRequest(
        data_dir=request.data_dir,
        project_id=request.project_id,
        capture_id=request.capture_id,
        source_reference_video_id=request.source_reference_video_id,
        input_video=request.input_video,
        checkpoint=request.checkpoint,
        upstream_root=request.upstream_root,
        execution_device="unsupported",
        expected_duration_seconds=request.expected_duration_seconds,
    )

    with pytest.raises(DepthCaptureFailure) as error:
        run_depth_capture(request, sys.executable, FIXTURES / "fake_worker.py", str(fake_ffmpeg(tmp_path)))

    assert error.value.code == "depth_device_unavailable"


@pytest.mark.parametrize("mode,code", [("timeout", "depth_capture_timeout"), ("oom", "depth_device_out_of_memory")])
def test_runner_keeps_stable_failure_code_when_workspace_cleanup_fails(tmp_path, monkeypatch, mode, code):
    if mode == "timeout":
        monkeypatch.setattr("app.depth_capture_runner.DEPTH_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.depth_capture_runner.shutil.rmtree", lambda _: (_ for _ in ()).throw(PermissionError()))

    with pytest.raises(DepthCaptureFailure) as error:
        run_fake(tmp_path, monkeypatch, mode=mode)

    assert error.value.code == code


def test_worker_enforces_even_dimensions_within_the_configured_long_edge():
    worker = load_worker_module()

    assert worker._even_dimensions(641, 479, 640) == (640, 478)


def test_worker_verifies_head_and_clean_porcelain_status_with_explicit_git_argv(monkeypatch, tmp_path):
    worker = load_worker_module()
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        output = "4f5ae23172ba60fd7bc11ef671cca678842c7072\n" if command[-2:] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    assert worker._checkout_commit(tmp_path) == "4f5ae23172ba60fd7bc11ef671cca678842c7072"
    assert commands[0][0][-2:] == ["rev-parse", "HEAD"]
    assert commands[1][0][-3:] == ["status", "--porcelain=v1", "--untracked-files=all"]
    assert all(entry[1]["shell"] is False and entry[1]["timeout"] == 5 for entry in commands)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="真实媒体集成测试需要 ffmpeg 和 ffprobe",
)
def test_real_ffmpeg_outputs_h264_turbo_mp4_without_audio(tmp_path, monkeypatch):
    request = fake_request(tmp_path)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "color=size=640x360:rate=8",
        "-frames:v", "2", "-pix_fmt", "yuv420p", str(request.input_video),
    ], check=True)
    result = run_depth_capture(request, sys.executable, FIXTURES / "fake_worker.py", "ffmpeg")
    outputs = [result.directory / "depth-control.mp4", result.directory / "depth-preview.mp4"]
    for output in outputs:
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-count_frames", "-show_entries",
            "stream=codec_name,pix_fmt,codec_type,width,height,avg_frame_rate,nb_read_frames",
            "-of", "json", str(output),
        ], capture_output=True, text=True, check=True)
        streams = json.loads(probe.stdout)["streams"]
        assert len(streams) == 1
        stream = streams[0]
        assert stream["codec_type"] == "video"
        assert stream["codec_name"] == "h264"
        assert stream["pix_fmt"] == "yuv420p"
        assert (stream["width"], stream["height"], stream["nb_read_frames"]) == (854, 480, "2")
        assert stream["avg_frame_rate"] == "8/1"
    decoded = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(outputs[1]),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], capture_output=True, check=True).stdout
    assert any(decoded[index:index + 3][0] != decoded[index:index + 3][1] for index in range(0, len(decoded), 3))


@pytest.mark.parametrize("frames", [117, 2400, 2401])
def test_runner_handles_long_video_duration_boundary(tmp_path, monkeypatch, frames):
    monkeypatch.setenv("FAKE_WORKER_MODE", "long")
    monkeypatch.setenv("FAKE_WORKER_FRAMES", str(frames))
    request = replace(fake_request(tmp_path), expected_duration_seconds=frames / 8)
    args = (request, sys.executable, FIXTURES / "fake_worker.py", str(fake_ffmpeg(tmp_path)))

    if frames > 2400:
        with pytest.raises(DepthCaptureFailure) as error:
            run_depth_capture(*args)
        assert error.value.code == "depth_worker_output_invalid"
    else:
        result = run_depth_capture(*args)
        assert result.frameCount == frames
        assert result.frameRate == 8.0
        assert (result.directory / "manifest.json").is_file()
