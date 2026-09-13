import json
import hashlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.local_preprocessing_storage as storage
from app.local_preprocessing import (
    ANALYSIS_LONG_EDGE,
    ANALYSIS_SAMPLE_RATE,
    CONTACT_SHEET_CELL_LONG_EDGE,
    KEYFRAME_LONG_EDGE,
    LIGHT_MOTION_P90_MAX,
    MAX_KEYFRAMES,
    MIN_KEYFRAMES,
    MIN_MOTION_SAMPLES,
    MODERATE_MOTION_P90_MAX,
    MOTION_EXCLUSION_SECONDS,
    SCENE_MERGE_WINDOW_SECONDS,
    SCENE_SCORE_THRESHOLD,
    SCENE_START_IGNORE_SECONDS,
    STAGE_ORDER,
    new_local_preprocessing,
)
from app.local_preprocessing_runner import (
    LocalPreprocessingFailure,
    check_ffmpeg_capabilities,
    decode_reference_video,
    load_preprocessing_result,
    run_command,
    run_keyframe_stage,
    run_local_preprocessing,
    run_motion_stage,
    run_assessment_stage,
    stage_failure_for,
)
from app.local_preprocessing_storage import preprocessing_directory, write_stage_json
from app.reference_video import ReferenceVideo


def completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["ffmpeg"], 0, stdout=stdout, stderr="")


def reference(*, video_id="video-001", duration=2.5, width=854, height=480, frame_rate=24.0) -> ReferenceVideo:
    return ReferenceVideo(
        id=video_id, originalName="private clip.mp4", format="mp4", sizeBytes=5,
        durationSeconds=duration, width=width, height=height, frameRate=frame_rate,
    )


def preprocessing():
    return new_local_preprocessing(
        preprocessing_id="prep-001", reference_media_id="video-001", media_type="video",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )


def ffmpeg_has_required_filters() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, check=False,
    )
    names = {
        line.split()[1] for line in result.stdout.splitlines()
        if len(line.split()) >= 2 and set(line.split()[0]) <= {"T", "S", "C", "."}
    }
    return result.returncode == 0 and {"scdet", "scale", "metadata", "drawtext", "tile"} <= names


def real_ffmpeg_available() -> bool:
    return ffmpeg_has_required_filters() and shutil.which("ffprobe") is not None


def fake_full_run(command, **kwargs):
    if command[-1] == "-filters":
        return completed("\n".join([
            " T. scdet V->V", " T.C scale V->V", " ... metadata V->V",
            " ... drawtext V->V", " ... tile V->V",
        ]))
    if command[-1] == "-version":
        return completed("ffmpeg version 8.1.1 Copyright")
    if "scdet=t=10" in " ".join(command):
        return completed(
            "frame:0 pts:0 pts_time:0.000\n"
            "lavfi.scd.mafd=0.000\nlavfi.scd.score=0.000\n"
            "frame:1 pts:1 pts_time:0.125\n"
            "lavfi.scd.mafd=1.000\nlavfi.scd.score=0.000\n"
        )
    target = Path(command[-1])
    if target.suffix.lower() == ".jpg":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"jpeg")
    return completed()


def test_decode_uses_non_attached_video_map_and_no_shell(tmp_path):
    commands = []
    source = tmp_path / "clip with spaces.mp4"
    source.write_bytes(b"video")

    def capture(command, **kwargs):
        commands.append((command, kwargs))
        return completed()

    decode_reference_video(source, ffmpeg_path="/custom/ffmpeg", run=capture)

    command, kwargs = commands[0]
    assert command == [
        "/custom/ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(source), "-map", "0:V:0", "-an", "-f", "null", "-",
    ]
    assert kwargs == {
        "capture_output": True, "text": True, "timeout": 120.0,
        "check": False, "shell": False,
    }


def test_missing_required_filter_is_a_stable_decoding_error():
    def no_drawtext(*args, **kwargs):
        return completed(" .. scdet V->V\n .. scale V->V\n .. metadata V->V\n .. tile V->V")

    with pytest.raises(LocalPreprocessingFailure) as captured:
        check_ffmpeg_capabilities("ffmpeg", run=no_drawtext)

    assert captured.value.code == "ffmpeg_filters_unavailable"
    assert captured.value.stage == "decoding"
    assert "drawtext" in captured.value.message


def test_capabilities_read_the_filter_name_column_for_annotated_flags():
    check_ffmpeg_capabilities(
        "ffmpeg",
        run=lambda *args, **kwargs: completed("\n".join([
            " T. scdet V->V", " T.C scale V->V", " ..S metadata V->V",
            " .S. drawtext V->V", " ... tile V->V",
        ])),
    )


@pytest.mark.parametrize(
    ("error", "code", "stage"),
    [
        (FileNotFoundError(), "ffmpeg_unavailable", "decoding"),
        (PermissionError(), "ffmpeg_unavailable", "decoding"),
        (subprocess.TimeoutExpired("ffmpeg", 120), "scene_detection_failed", "sceneDetection"),
    ],
)
def test_command_launch_and_timeout_failures_have_stable_public_errors(error, code, stage):
    def fail(*args, **kwargs):
        raise error

    with pytest.raises(LocalPreprocessingFailure) as captured:
        run_command(
            ["ffmpeg"], stage=stage, code="scene_detection_failed",
            message="镜头检测失败，请重试。", run=fail,
        )

    assert (captured.value.code, captured.value.stage) == (code, stage)
    assert "/" not in captured.value.message
    assert "stderr" not in captured.value.message


def test_nonzero_exit_hides_source_path_and_stderr(tmp_path):
    source = tmp_path / "private-source.mp4"

    def fail(*args, **kwargs):
        return subprocess.CompletedProcess(
            ["ffmpeg"], 1, stdout="", stderr=f"cannot open {source}: secret",
        )

    with pytest.raises(LocalPreprocessingFailure) as captured:
        decode_reference_video(source, ffmpeg_path="ffmpeg", run=fail)

    assert captured.value.code == "video_decode_failed"
    assert captured.value.stage == "decoding"
    assert str(source) not in captured.value.message
    assert "secret" not in captured.value.message


def test_nonzero_exit_log_redacts_paths_customer_directories_and_secrets(tmp_path, caplog):
    source = tmp_path / "客户项目" / "private-source.mp4"
    stderr = f"cannot open {source}; token=top-secret; api_key=customer-key; secret=do-not-log"

    with pytest.raises(LocalPreprocessingFailure):
        run_command(
            ["ffmpeg"], stage="sceneDetection", code="scene_detection_failed",
            message="镜头检测失败，请重试。",
            run=lambda *args, **kwargs: subprocess.CompletedProcess(
                ["ffmpeg"], 1, stdout="", stderr=stderr,
            ),
        )

    logged = caplog.text
    assert str(source) not in logged
    assert "客户项目" not in logged
    assert "top-secret" not in logged
    assert "customer-key" not in logged
    assert "do-not-log" not in logged
    assert "sceneDetection" in logged
    assert "exitCode=1" in logged


def test_full_run_writes_only_proxy_contract_and_not_source_metadata(tmp_path):
    source = tmp_path / "original customer video.mp4"
    source.write_bytes(b"video")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    started = []
    completed_stages = []

    result = run_local_preprocessing(
        source_path=source, reference=reference(), preprocessing=preprocessing(),
        output_directory=output, ffmpeg_path="ffmpeg", on_stage_started=started.append,
        on_stage_completed=completed_stages.append, run=fake_full_run,
    )

    proxy = json.loads((output / "analysis-proxy.json").read_text(encoding="utf-8"))
    assert started == completed_stages == [
        "decoding", "sceneDetection", "keyframeExtraction", "motionAnalysis",
        "reproducibilityAssessment",
    ]
    assert result.proxy_summary.keyframeCount == 4
    assert proxy["contactSheetFile"] == "contact-sheet.jpg"
    assert proxy["source"] == {
        "durationSeconds": 2.5, "width": 854, "height": 480, "frameRate": 24.0,
    }
    assert json.loads((output / "decode.json").read_text(encoding="utf-8"))["ffmpegVersion"] == "8.1.1"
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8"))["parameters"] == {
        "analysisSampleRate": ANALYSIS_SAMPLE_RATE,
        "analysisLongEdge": ANALYSIS_LONG_EDGE,
        "keyframeLongEdge": KEYFRAME_LONG_EDGE,
        "contactSheetCellLongEdge": CONTACT_SHEET_CELL_LONG_EDGE,
        "minKeyframes": MIN_KEYFRAMES,
        "maxKeyframes": MAX_KEYFRAMES,
        "sceneScoreThreshold": SCENE_SCORE_THRESHOLD,
        "sceneStartIgnoreSeconds": SCENE_START_IGNORE_SECONDS,
        "sceneMergeWindowSeconds": SCENE_MERGE_WINDOW_SECONDS,
        "motionExclusionSeconds": MOTION_EXCLUSION_SECONDS,
        "minMotionSamples": MIN_MOTION_SAMPLES,
        "lightMotionP90Max": LIGHT_MOTION_P90_MAX,
        "moderateMotionP90Max": MODERATE_MOTION_P90_MAX,
    }
    proxy_text = json.dumps(proxy, ensure_ascii=False)
    assert str(source) not in proxy_text
    assert "originalName" not in proxy_text
    assert "project-001" not in proxy_text
    assert not list(output.glob("*.mp4"))
    motion = json.loads((output / "motion.json").read_text(encoding="utf-8"))
    assert motion["validSamples"] == [
        {"timeSeconds": 0.0, "mafd": 0.0},
        {"timeSeconds": 0.125, "mafd": 1.0},
    ]
    assert motion["excludedTimesSeconds"] == []
    assert proxy["motion"]["samples"] == motion["validSamples"]


def test_motion_artifacts_and_proxy_keep_only_valid_time_ordered_samples(tmp_path):
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    samples = [
        {"timeSeconds": index / 8, "mafd": float(index), "sceneScore": 12.0 if index == 4 else 0.0}
        for index in range(16)
    ]
    write_stage_json(output, "scene-changes.json", {
        "schemaVersion": 1,
        "algorithmVersion": 1,
        "sampleRate": 8.0,
        "samples": samples,
        "sceneChanges": [{"timeSeconds": 0.5, "score": 12.0}],
    })
    frames = output / "keyframes"
    frames.mkdir(parents=True)
    for index in range(1, 5):
        (frames / f"frame-{index:04d}.jpg").write_bytes(b"jpeg")

    run_motion_stage(output)
    motion = json.loads((output / "motion.json").read_text(encoding="utf-8"))
    run_assessment_stage(reference(), output)
    proxy = json.loads((output / "analysis-proxy.json").read_text(encoding="utf-8"))

    assert list(motion) == [
        "schemaVersion", "algorithmVersion", "p50", "p90", "peak", "level",
        "validSampleCount", "excludedSampleCount", "validSamples", "excludedTimesSeconds",
    ]
    assert motion["excludedTimesSeconds"] == [0.25, 0.375, 0.5, 0.625, 0.75]
    assert [sample["timeSeconds"] for sample in motion["validSamples"]] == [
        0.0, 0.125, 0.875, 1.0, 1.125, 1.25, 1.375, 1.5, 1.625, 1.75, 1.875,
    ]
    assert proxy["motion"]["samples"] == motion["validSamples"]


def test_invalid_scene_metadata_resets_scene_and_following_artifacts(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    output.mkdir(parents=True)
    (output / "scene-changes.json").write_text("{}", encoding="utf-8")
    (output / "motion.json").write_text("{}", encoding="utf-8")

    def fake_run(command, **kwargs):
        if command[-1] == "-filters":
            return completed("\n".join(f" ... {name} V->V" for name in (
                "scdet", "scale", "metadata", "drawtext", "tile",
            )))
        if command[-1] == "-version":
            return completed("ffmpeg version 8.1.1 Copyright")
        if "scdet=t=10" in " ".join(command):
            return completed("frame:0 pts:0 pts_time:not-a-number")
        return completed()

    task = preprocessing()
    task.stages[0].status = "completed"
    with pytest.raises(LocalPreprocessingFailure) as captured:
        run_local_preprocessing(
            source_path=source, reference=reference(), preprocessing=task,
            output_directory=output, ffmpeg_path="ffmpeg", on_stage_started=lambda _: None,
            on_stage_completed=lambda _: None, run=fake_run,
        )

    assert (captured.value.code, captured.value.stage) == (
        "scene_detection_failed", "sceneDetection",
    )
    assert not (output / "scene-changes.json").exists()
    assert not (output / "motion.json").exists()


def test_empty_keyframe_jpeg_is_rejected_before_it_is_committed(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    write_stage_json(output, "scene-changes.json", {
        "schemaVersion": 1, "algorithmVersion": 1, "sampleRate": 8,
        "samples": [], "sceneChanges": [],
    })

    with pytest.raises(LocalPreprocessingFailure) as captured:
        run_keyframe_stage(source, reference(), output, "ffmpeg", run=lambda *args, **kwargs: completed())

    assert (captured.value.code, captured.value.stage) == (
        "keyframe_extraction_failed", "keyframeExtraction",
    )
    assert not (output / "keyframes").exists()


def test_contact_sheet_nonzero_exit_has_a_stable_error_and_no_partial_output(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    write_stage_json(output, "scene-changes.json", {
        "schemaVersion": 1, "algorithmVersion": 1, "sampleRate": 8,
        "samples": [], "sceneChanges": [],
    })

    def fake_run(command, **kwargs):
        target = Path(command[-1])
        if target.name == "contact-sheet.jpg":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="private error")
        if target.suffix == ".jpg":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"jpeg")
        return completed()

    with pytest.raises(LocalPreprocessingFailure) as captured:
        run_keyframe_stage(source, reference(), output, "ffmpeg", run=fake_run)

    assert (captured.value.code, captured.value.stage) == (
        "keyframe_extraction_failed", "keyframeExtraction",
    )
    assert not (output / "keyframes").exists()
    assert not (output / "contact-sheet.jpg").exists()


def test_stage_disk_write_error_becomes_a_stable_stage_failure(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")

    monkeypatch.setattr(
        "app.local_preprocessing_runner.write_stage_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(LocalPreprocessingFailure) as captured:
        run_local_preprocessing(
            source_path=source, reference=reference(), preprocessing=preprocessing(),
            output_directory=output, ffmpeg_path="ffmpeg", on_stage_started=lambda _: None,
            on_stage_completed=lambda _: None,
            run=lambda command, **kwargs: completed(
                "ffmpeg version 8.1.1 Copyright" if command[-1] == "-version" else "\n".join(
                    f" ... {name} V->V" for name in ("scdet", "scale", "metadata", "drawtext", "tile")
                )
            ),
        )

    assert (captured.value.code, captured.value.stage) == (
        "preprocessing_storage_unavailable", "decoding",
    )
    assert "disk full" not in captured.value.message


def test_runner_executes_shuffled_stage_states_in_fixed_order(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    task = preprocessing()
    task.stages.reverse()
    started = []

    run_local_preprocessing(
        source_path=source, reference=reference(), preprocessing=task,
        output_directory=output, ffmpeg_path="ffmpeg", on_stage_started=started.append,
        on_stage_completed=lambda _: None, run=fake_full_run,
    )

    assert started == [
        "decoding", "sceneDetection", "keyframeExtraction", "motionAnalysis",
        "reproducibilityAssessment",
    ]


def test_runner_rejects_duplicate_or_missing_stage_states_before_running(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    task = preprocessing()
    task.stages[-1] = task.stages[0].model_copy(deep=True)

    with pytest.raises(LocalPreprocessingFailure) as captured:
        run_local_preprocessing(
            source_path=source, reference=reference(), preprocessing=task,
            output_directory=output, ffmpeg_path="ffmpeg", on_stage_started=lambda _: None,
            on_stage_completed=lambda _: None, run=fake_full_run,
        )

    assert (captured.value.code, captured.value.stage) == (
        "preprocessing_unexpected_error", "decoding",
    )


def test_recovery_normalizes_mixed_shuffled_states_before_storage_validation(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    write_stage_json(output, "decode.json", {"decoded": True})
    write_stage_json(output, "scene-changes.json", {
        "schemaVersion": 1, "algorithmVersion": 1, "sampleRate": 8,
        "samples": [], "sceneChanges": [],
    })
    write_stage_json(output, "motion.json", {"p90": 1.0})
    task = preprocessing()
    states = {state.name: state for state in task.stages}
    for name in ("decoding", "sceneDetection", "keyframeExtraction", "motionAnalysis"):
        states[name].status = "completed"
    task.stages = [
        states["motionAnalysis"], states["decoding"], states["sceneDetection"],
        states["keyframeExtraction"], states["reproducibilityAssessment"],
    ]
    supplied_orders = []
    recovered_statuses = []

    def capture_validation(candidate, directory):
        supplied_orders.append([state.name for state in candidate.stages])
        validated = storage.validate_completed_stages(candidate, directory)
        recovered_statuses.append([state.status for state in validated.preprocessing.stages])
        return validated

    monkeypatch.setattr(
        "app.local_preprocessing_runner.validate_completed_stages", capture_validation,
    )
    started = []
    run_local_preprocessing(
        source_path=source, reference=reference(), preprocessing=task,
        output_directory=output, ffmpeg_path="ffmpeg", on_stage_started=started.append,
        on_stage_completed=lambda _: None, run=fake_full_run,
    )

    assert supplied_orders == [list(STAGE_ORDER)]
    assert recovered_statuses == [["completed", "completed", "pending", "pending", "pending"]]
    assert started == ["keyframeExtraction", "motionAnalysis", "reproducibilityAssessment"]


def test_invalid_scene_artifact_is_mapped_at_the_loading_boundary(tmp_path):
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    write_stage_json(output, "scene-changes.json", {"schemaVersion": 1})

    with pytest.raises(LocalPreprocessingFailure) as captured:
        run_motion_stage(output)

    assert (captured.value.code, captured.value.stage) == (
        "motion_analysis_failed", "motionAnalysis",
    )
    assert "project-001" not in captured.value.message


def test_result_load_maps_file_read_failures_without_leaking_paths(tmp_path, monkeypatch):
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    output.mkdir(parents=True)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("private path")))

    with pytest.raises(LocalPreprocessingFailure) as captured:
        load_preprocessing_result(output)

    assert captured.value.code == "preprocessing_storage_unavailable"
    assert "private path" not in captured.value.message


def test_result_load_rejects_proxy_summary_or_motion_mismatch(tmp_path):
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    write_stage_json(output, "motion.json", {
        "schemaVersion": 1, "algorithmVersion": 1, "p50": 1.0, "p90": 2.0,
        "peak": 3.0, "level": "light", "validSampleCount": 8, "excludedSampleCount": 0,
    })
    write_stage_json(output, "analysis-proxy.json", {
        "schemaVersion": 1, "keyframes": [{"index": 1, "timeSeconds": 0.0}] * 4,
        "scene": {"changeCount": 0, "changeTimesSeconds": []},
        "motion": {"samples": [], "p50": 1.0, "p90": 9.0, "peak": 3.0, "level": "light"},
        "contactSheetFile": "contact-sheet.jpg",
    })
    write_stage_json(output, "manifest.json", {
        "schemaVersion": 1, "algorithmVersion": 1, "sourceReferenceVideoId": "video-001",
        "proxySummary": {
            "keyframeCount": 4, "contactSheetCount": 1, "sceneChangeCount": 0,
            "motionP50": 1.0, "motionP90": 2.0, "motionPeak": 3.0, "motionLevel": "light",
        },
        "reproducibilityAssessment": {"status": "pending_semantic_confirmation", "checks": []},
    })

    with pytest.raises(LocalPreprocessingFailure) as captured:
        load_preprocessing_result(output)

    assert captured.value.code == "assessment_failed"


@pytest.mark.parametrize(
    ("stage", "code"),
    [
        ("decoding", "video_decode_failed"),
        ("sceneDetection", "scene_detection_failed"),
        ("keyframeExtraction", "keyframe_extraction_failed"),
        ("motionAnalysis", "motion_analysis_failed"),
        ("reproducibilityAssessment", "assessment_failed"),
    ],
)
def test_stage_failures_use_only_the_stable_error_contract(stage, code):
    failure = stage_failure_for(stage)

    assert (failure.code, failure.stage) == (code, stage)


@pytest.mark.skipif(
    not ffmpeg_has_required_filters(),
    reason="真实媒体集成测试需要具备 scdet、scale、metadata、drawtext、tile 的 FFmpeg",
)
def test_real_ffmpeg_creates_proxy_without_a_low_resolution_video_copy(tmp_path):
    source = tmp_path / "static.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=0x343938:s=854x480:r=24", "-t", "2.5", "-c:v", "libx264", "-pix_fmt",
        "yuv420p", str(source),
    ], check=True)
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")

    result = run_local_preprocessing(
        source_path=source, reference=reference(), preprocessing=preprocessing(),
        output_directory=output, ffmpeg_path="ffmpeg", on_stage_started=lambda _: None,
        on_stage_completed=lambda _: None,
    )

    assert result.assessment.status == "pending_semantic_confirmation"
    assert (output / "contact-sheet.jpg").stat().st_size > 0
    assert len(list((output / "keyframes").glob("frame-*.jpg"))) == 4
    assert not list(output.rglob("*.mp4"))


def make_lavfi_video(
    path: Path,
    source: str,
    *,
    duration: float,
    width=854,
    height=480,
    frame_rate=24,
) -> None:
    separator = ":" if "=" in source else "="
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"{source}{separator}size={width}x{height}:rate={frame_rate}", "-t", str(duration), "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def media_dimensions(path: Path) -> tuple[int, int]:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height", "-of", "json", str(path),
    ], capture_output=True, text=True, check=True)
    stream = json.loads(result.stdout)["streams"][0]
    return stream["width"], stream["height"]


def run_real_preprocessing(
    tmp_path,
    source: Path,
    *,
    video_id: str,
    duration: float,
    width=854,
    height=480,
    frame_rate=24.0,
    run=subprocess.run,
):
    output = preprocessing_directory(tmp_path, "project-001", f"prep-{video_id}")
    task = new_local_preprocessing(
        preprocessing_id=f"prep-{video_id}", reference_media_id=video_id, media_type="video",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    return output, run_local_preprocessing(
        source_path=source, reference=reference(
            video_id=video_id, duration=duration, width=width, height=height, frame_rate=frame_rate,
        ), preprocessing=task, output_directory=output, ffmpeg_path="ffmpeg",
        on_stage_started=lambda _: None, on_stage_completed=lambda _: None, run=run,
    )


@pytest.mark.skipif(
    not real_ffmpeg_available(),
    reason="真实媒体集成测试需要具备必需滤镜的 ffmpeg 和 ffprobe",
)
def test_real_static_video_has_no_cuts_light_motion_and_annotated_size_limited_artifacts(tmp_path):
    source = tmp_path / "static.mp4"
    make_lavfi_video(source, "color=c=0x343938", duration=2.5)
    commands = []

    def capture(command, **kwargs):
        commands.append(command)
        return subprocess.run(command, **kwargs)

    output = preprocessing_directory(tmp_path, "project-001", "prep-static")
    task = new_local_preprocessing(
        preprocessing_id="prep-static", reference_media_id="static", media_type="video",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    result = run_local_preprocessing(
        source_path=source, reference=reference(video_id="static"), preprocessing=task,
        output_directory=output, ffmpeg_path="ffmpeg", on_stage_started=lambda _: None,
        on_stage_completed=lambda _: None, run=capture,
    )

    proxy = json.loads((output / "analysis-proxy.json").read_text(encoding="utf-8"))
    assert result.assessment.status == "pending_semantic_confirmation"
    assert proxy["scene"] == {"changeCount": 0, "changeTimesSeconds": []}
    assert proxy["motion"]["level"] == "light"
    assert all(max(media_dimensions(frame)) <= 768 for frame in (output / "keyframes").glob("*.jpg"))
    assert max(media_dimensions(output / "contact-sheet.jpg")) <= 3 * 384 + 32
    assert any(
        "drawtext=font=monospace:text='00\\:00.000'" in argument
        for command in commands for argument in command
    )
    assert not list(output.rglob("*.mp4"))


@pytest.mark.skipif(
    not real_ffmpeg_available(),
    reason="真实媒体集成测试需要具备必需滤镜的 ffmpeg 和 ffprobe",
)
def test_real_regular_motion_has_no_hard_cut_and_needs_semantic_confirmation(tmp_path):
    source = tmp_path / "moving.mp4"
    make_lavfi_video(source, "testsrc2", duration=3.0)

    output, result = run_real_preprocessing(tmp_path, source, video_id="moving", duration=3.0)

    proxy = json.loads((output / "analysis-proxy.json").read_text(encoding="utf-8"))
    assert result.assessment.status == "pending_semantic_confirmation"
    assert proxy["scene"]["changeCount"] == 0


@pytest.mark.skipif(
    not real_ffmpeg_available(),
    reason="真实媒体集成测试需要具备必需滤镜的 ffmpeg 和 ffprobe",
)
def test_real_30fps_video_extracts_the_last_keyframe_without_seeking_past_it(tmp_path):
    source = tmp_path / "moving-30fps.mp4"
    make_lavfi_video(source, "testsrc2", duration=3.0, width=640, height=480, frame_rate=30)
    commands = []

    def capture(command, **kwargs):
        commands.append(command)
        return subprocess.run(command, **kwargs)

    output, result = run_real_preprocessing(
        tmp_path,
        source,
        video_id="moving-30fps",
        duration=3.0,
        width=640,
        height=480,
        frame_rate=30.0,
        run=capture,
    )

    keyframe_commands = [command for command in commands if "-ss" in command]
    last_keyframe_command = keyframe_commands[-1]
    assert last_keyframe_command[last_keyframe_command.index("-ss") + 1] == "2.966666"
    proxy = json.loads((output / "analysis-proxy.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert result.assessment.status == "pending_semantic_confirmation"
    assert len(list((output / "keyframes").glob("frame-*.jpg"))) == 4
    assert proxy["keyframes"][-1] == {"index": 4, "timeSeconds": 2.967}
    assert manifest["keyframes"][-1] == {"index": 4, "timeSeconds": 2.967}


@pytest.mark.skipif(
    not real_ffmpeg_available(),
    reason="真实媒体集成测试需要具备必需滤镜的 ffmpeg 和 ffprobe",
)
def test_real_hard_cuts_are_out_of_scope(tmp_path):
    source = tmp_path / "hard-cuts.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=black:s=854x480:r=24:d=1", "-f", "lavfi", "-i",
        "color=c=white:s=854x480:r=24:d=1", "-f", "lavfi", "-i",
        "color=c=black:s=854x480:r=24:d=1", "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True)

    output, result = run_real_preprocessing(tmp_path, source, video_id="cuts", duration=3.0)

    proxy = json.loads((output / "analysis-proxy.json").read_text(encoding="utf-8"))
    assert result.assessment.status == "out_of_scope"
    assert proxy["scene"]["changeCount"] >= 1


@pytest.mark.skipif(
    not real_ffmpeg_available(),
    reason="真实媒体集成测试需要具备必需滤镜的 ffmpeg 和 ffprobe",
)
def test_real_high_motion_has_p90_over_eight(tmp_path):
    source = tmp_path / "high-motion.mp4"
    make_lavfi_video(source, "life=ratio=0.5:seed=1", duration=3.0)

    output, _ = run_real_preprocessing(tmp_path, source, video_id="life", duration=3.0)

    proxy = json.loads((output / "analysis-proxy.json").read_text(encoding="utf-8"))
    assert proxy["motion"]["p90"] > 8.0


@pytest.mark.skipif(
    not real_ffmpeg_available(),
    reason="真实媒体集成测试需要具备必需滤镜的 ffmpeg 和 ffprobe",
)
def test_real_4k_source_is_unchanged_and_keyframes_are_scaled(tmp_path):
    source = tmp_path / "four-k.mp4"
    make_lavfi_video(source, "color=c=0x343938", duration=2.0, width=3840, height=2160)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    output, _ = run_real_preprocessing(
        tmp_path, source, video_id="four-k", duration=2.0, width=3840, height=2160,
    )

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert all(max(media_dimensions(frame)) <= 768 for frame in (output / "keyframes").glob("*.jpg"))
    assert not list(output.rglob("*.mp4"))
