import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .local_preprocessing import (
    ALGORITHM_VERSION,
    ANALYSIS_LONG_EDGE,
    ANALYSIS_SAMPLE_RATE,
    CONTACT_SHEET_CELL_LONG_EDGE,
    FFMPEG_TIMEOUT_SECONDS,
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
    AnalysisProxySummary,
    FrameMetric,
    LocalPreprocessing,
    MotionSummary,
    ReproducibilityAssessment,
    SceneChange,
    STAGE_ORDER,
    StageName,
    assess_reproducibility,
    detect_scene_changes,
    parse_scdet_metadata,
    select_keyframe_times,
    summarize_motion,
)
from .local_preprocessing_storage import (
    commit_keyframe_stage,
    reset_stage_artifacts,
    validate_completed_stages,
    write_stage_json,
)
from .reference_video import ReferenceVideo


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
REQUIRED_FILTERS = frozenset({"scdet", "scale", "metadata", "drawtext", "tile"})


class LocalPreprocessingFailure(Exception):
    def __init__(self, code: str, message: str, stage: StageName) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = True


@dataclass(frozen=True)
class PreprocessingRunResult:
    proxy_summary: AnalysisProxySummary
    assessment: ReproducibilityAssessment


def run_command(
    command: list[str], *, stage: StageName, code: str, message: str, run: CommandRunner,
) -> subprocess.CompletedProcess[str]:
    try:
        result = run(
            command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False, shell=False,
        )
    except (FileNotFoundError, PermissionError) as error:
        raise LocalPreprocessingFailure(
            "ffmpeg_unavailable",
            "本地未找到或无法启动 FFmpeg，请确认 ffmpeg -version 可运行。",
            stage,
        ) from error
    except subprocess.TimeoutExpired as error:
        raise LocalPreprocessingFailure(code, message, stage) from error
    if result.returncode != 0:
        logging.getLogger(__name__).warning(
            "FFmpeg 阶段失败：stage=%s exitCode=%s stderrChars=%s",
            stage, result.returncode, min(len(result.stderr or ""), 2000),
        )
        raise LocalPreprocessingFailure(code, message, stage)
    return result


def check_ffmpeg_capabilities(ffmpeg_path: str, *, run: CommandRunner) -> None:
    result = run_command(
        [ffmpeg_path, "-hide_banner", "-filters"],
        stage="decoding", code="ffmpeg_filters_unavailable",
        message="FFmpeg 能力检查失败，请确认本地 FFmpeg 可用。", run=run,
    )
    available = _filter_names(result.stdout)
    missing = sorted(REQUIRED_FILTERS - available)
    if missing:
        raise LocalPreprocessingFailure(
            "ffmpeg_filters_unavailable",
            f"本地 FFmpeg 缺少必需滤镜：{', '.join(missing)}。",
            "decoding",
        )


def decode_reference_video(source_path: Path, *, ffmpeg_path: str, run: CommandRunner) -> None:
    run_command(
        [
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i", str(source_path),
            "-map", "0:V:0", "-an", "-f", "null", "-",
        ],
        stage="decoding", code="video_decode_failed",
        message="本地解码失败，请确认参考视频可播放后重试。", run=run,
    )


def run_local_preprocessing(
    *,
    source_path: Path,
    reference: ReferenceVideo,
    preprocessing: LocalPreprocessing,
    output_directory: Path,
    ffmpeg_path: str,
    on_stage_started: Callable[[StageName], None],
    on_stage_completed: Callable[[StageName], None],
    run: CommandRunner = subprocess.run,
) -> PreprocessingRunResult:
    normalized = _normalized_preprocessing(preprocessing)
    validated = validate_completed_stages(normalized, output_directory).preprocessing
    states = _stage_states_by_name(validated)
    handlers: dict[StageName, Callable[[], None]] = {
        "decoding": lambda: run_decoding_stage(
            source_path, reference, output_directory, ffmpeg_path, run,
        ),
        "sceneDetection": lambda: run_scene_detection_stage(
            source_path, output_directory, ffmpeg_path, run,
        ),
        "keyframeExtraction": lambda: run_keyframe_stage(
            source_path, reference, output_directory, ffmpeg_path, run,
        ),
        "motionAnalysis": lambda: run_motion_stage(output_directory),
        "reproducibilityAssessment": lambda: run_assessment_stage(
            reference, output_directory,
        ),
    }
    for stage in STAGE_ORDER:
        state = states[stage]
        if state.status == "completed":
            continue
        on_stage_started(state.name)
        try:
            handlers[state.name]()
        except LocalPreprocessingFailure:
            try:
                reset_stage_artifacts(output_directory, state.name)
            except OSError as error:
                raise storage_failure_for(state.name) from error
            raise
        except OSError as error:
            try:
                reset_stage_artifacts(output_directory, state.name)
            except OSError:
                pass
            raise storage_failure_for(state.name) from error
        except (ValueError, ValidationError) as error:
            try:
                reset_stage_artifacts(output_directory, state.name)
            except OSError as cleanup_error:
                raise storage_failure_for(state.name) from cleanup_error
            raise stage_failure_for(state.name) from error
        on_stage_completed(state.name)
    return load_preprocessing_result(output_directory)


def run_decoding_stage(
    source_path: Path,
    reference: ReferenceVideo,
    output_directory: Path,
    ffmpeg_path: str,
    run: CommandRunner,
) -> None:
    check_ffmpeg_capabilities(ffmpeg_path, run=run)
    decode_reference_video(source_path, ffmpeg_path=ffmpeg_path, run=run)
    write_stage_json(output_directory, "decode.json", {
        "schemaVersion": 1,
        "algorithmVersion": ALGORITHM_VERSION,
        "sourceReferenceVideoId": reference.id,
        "expectedFrameCount": round(reference.durationSeconds * reference.frameRate),
        "completedAt": _timestamp(),
        "ffmpegVersion": _ffmpeg_version(ffmpeg_path, run),
    })


def run_scene_detection_stage(
    source_path: Path,
    output_directory: Path,
    ffmpeg_path: str,
    run: CommandRunner,
) -> None:
    analysis_filter = (
        "fps=8,"
        "scale=w='if(gte(iw,ih),320,-2)':h='if(gte(iw,ih),-2,320)',"
        "format=gray,scdet=t=10,metadata=mode=print:file=-"
    )
    result = run_command(
        [
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i", str(source_path),
            "-map", "0:V:0", "-an", "-vf", analysis_filter, "-f", "null", "-",
        ],
        stage="sceneDetection", code="scene_detection_failed",
        message="镜头检测失败，请重试。", run=run,
    )
    try:
        metrics = parse_scdet_metadata(result.stdout)
    except ValueError as error:
        raise LocalPreprocessingFailure(
            "scene_detection_failed", "镜头检测结果无效，请重试。", "sceneDetection",
        ) from error
    changes = detect_scene_changes(metrics)
    write_stage_json(output_directory, "scene-changes.json", {
        "schemaVersion": 1,
        "algorithmVersion": ALGORITHM_VERSION,
        "sampleRate": ANALYSIS_SAMPLE_RATE,
        "samples": [_metric_payload(item) for item in metrics],
        "sceneChanges": [_scene_payload(item) for item in changes],
    })


def run_keyframe_stage(
    source_path: Path,
    reference: ReferenceVideo,
    output_directory: Path,
    ffmpeg_path: str,
    run: CommandRunner,
) -> None:
    changes = _load_scene_changes(output_directory, stage="keyframeExtraction")[1]
    times = select_keyframe_times(reference.durationSeconds, reference.frameRate, changes)
    workspace = Path(tempfile.mkdtemp(prefix=".local-preprocessing-keyframes-"))
    frame_names = [f"frame-{index:04d}.jpg" for index in range(1, len(times) + 1)]
    try:
        frames = workspace / "keyframes"
        frames.mkdir()
        for index, time_seconds in enumerate(times, start=1):
            frame = frames / f"frame-{index:04d}.jpg"
            seek_time = _keyframe_seek_time(time_seconds, reference)
            run_command(
                [
                    ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss",
                    f"{seek_time:.6f}", "-i", str(source_path), "-map", "0:V:0",
                    "-frames:v", "1", "-an", "-vf", _keyframe_scale_filter(), "-q:v", "3",
                    str(frame),
                ],
                stage="keyframeExtraction", code="keyframe_extraction_failed",
                message="关键帧提取失败，请重试。", run=run,
            )
            if not _nonempty_regular_file(frame):
                raise LocalPreprocessingFailure(
                    "keyframe_extraction_failed", "关键帧输出无效，请重试。", "keyframeExtraction",
                )
        _build_contact_sheet(workspace, frame_names, times, ffmpeg_path, run)
        commit_keyframe_stage(workspace, output_directory, frame_names)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_motion_stage(output_directory: Path) -> None:
    metrics, changes = _load_scene_changes(output_directory, stage="motionAnalysis")
    motion = summarize_motion(metrics, changes)
    write_stage_json(output_directory, "motion.json", {
        "schemaVersion": 1,
        "algorithmVersion": ALGORITHM_VERSION,
        **motion.model_dump(),
    })


def run_assessment_stage(
    reference: ReferenceVideo,
    output_directory: Path,
) -> None:
    metrics, changes = _load_scene_changes(output_directory, stage="reproducibilityAssessment")
    motion = _load_motion(output_directory, stage="reproducibilityAssessment")
    assessment = assess_reproducibility(changes, motion)
    frame_names = sorted(path.name for path in (output_directory / "keyframes").glob("frame-*.jpg"))
    proxy = {
        "schemaVersion": 1,
        "source": {
            "durationSeconds": reference.durationSeconds,
            "width": reference.width,
            "height": reference.height,
            "frameRate": reference.frameRate,
        },
        "keyframes": [
            {"index": index, "timeSeconds": _keyframe_time(index, reference, changes)}
            for index in range(1, len(frame_names) + 1)
        ],
        "scene": {
            "changeCount": len(changes),
            "changeTimesSeconds": [round(item.timeSeconds, 3) for item in changes],
        },
        "motion": {
            "samples": [
                sample.model_dump()
                for sample in sorted(motion.validSamples, key=lambda item: item.timeSeconds)[:80]
            ],
            "p50": motion.p50,
            "p90": motion.p90,
            "peak": motion.peak,
            "level": motion.level,
        },
        "contactSheetFile": "contact-sheet.jpg",
    }
    summary = _summary_from_proxy(proxy)
    manifest = {
        "schemaVersion": 1,
        "algorithmVersion": ALGORITHM_VERSION,
        "sourceReferenceVideoId": reference.id,
        "generatedAt": _timestamp(),
        "parameters": _fixed_parameters(),
        "keyframes": proxy["keyframes"],
        "artifacts": {
            "decode": "decode.json", "sceneChanges": "scene-changes.json",
            "keyframes": [f"keyframes/{name}" for name in frame_names],
            "contactSheet": "contact-sheet.jpg", "motion": "motion.json",
            "analysisProxy": "analysis-proxy.json",
        },
        "proxySummary": summary.model_dump(),
        "reproducibilityAssessment": assessment.model_dump(),
    }
    write_stage_json(output_directory, "analysis-proxy.json", proxy)
    write_stage_json(output_directory, "manifest.json", manifest)


def load_preprocessing_result(output_directory: Path) -> PreprocessingRunResult:
    try:
        motion = MotionSummary(**_read_json(output_directory / "motion.json"))
        proxy = _read_json(output_directory / "analysis-proxy.json")
        manifest = _read_json(output_directory / "manifest.json")
        proxy_summary = _summary_from_proxy(proxy)
        manifest_summary = AnalysisProxySummary(**manifest["proxySummary"])
        assessment = ReproducibilityAssessment(**manifest["reproducibilityAssessment"])
        if proxy_summary != manifest_summary or any(
            proxy["motion"][field] != getattr(motion, field)
            for field in ("p50", "p90", "peak", "level")
        ):
            raise ValueError("预处理汇总不一致")
        return PreprocessingRunResult(proxy_summary=manifest_summary, assessment=assessment)
    except OSError as error:
        raise LocalPreprocessingFailure(
            "preprocessing_storage_unavailable", "本地预处理结果无法读取，请检查数据目录后重试。", "reproducibilityAssessment",
        ) from error
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError, ValueError) as error:
        raise LocalPreprocessingFailure(
            "assessment_failed", "本地预处理结果无效，请重试。", "reproducibilityAssessment",
        ) from error


def stage_failure_for(stage: StageName) -> LocalPreprocessingFailure:
    code, message = {
        "decoding": ("video_decode_failed", "本地解码失败，请确认参考视频可播放后重试。"),
        "sceneDetection": ("scene_detection_failed", "镜头检测失败，请重试。"),
        "keyframeExtraction": ("keyframe_extraction_failed", "关键帧提取失败，请重试。"),
        "motionAnalysis": ("motion_analysis_failed", "运动分析失败，请重试。"),
        "reproducibilityAssessment": ("assessment_failed", "可复现性判断失败，请重试。"),
    }[stage]
    return LocalPreprocessingFailure(code, message, stage)


def storage_failure_for(stage: StageName) -> LocalPreprocessingFailure:
    return LocalPreprocessingFailure(
        "preprocessing_storage_unavailable", "本地预处理产物无法安全读写，请检查数据目录后重试。", stage,
    )


def _build_contact_sheet(
    workspace: Path,
    frame_names: list[str],
    times: list[float],
    ffmpeg_path: str,
    run: CommandRunner,
) -> None:
    annotated = workspace / "annotated"
    annotated.mkdir()
    for index, (name, time_seconds) in enumerate(zip(frame_names, times), start=1):
        target = annotated / name
        run_command(
            [
                ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i",
                str(workspace / "keyframes" / name), "-map", "0:V:0", "-frames:v", "1", "-an",
                "-vf", _annotation_filter(_format_timecode(time_seconds)), "-q:v", "3", str(target),
            ],
            stage="keyframeExtraction", code="keyframe_extraction_failed",
            message="联系表生成失败，请重试。", run=run,
        )
        if not _nonempty_regular_file(target):
            raise LocalPreprocessingFailure(
                "keyframe_extraction_failed", "联系表生成失败，请重试。", "keyframeExtraction",
            )
    rows = math.ceil(len(frame_names) / 3)
    contact_filter = (
        "scale=w='if(gte(iw,ih),min(384,iw),-2)':h='if(gte(iw,ih),-2,min(384,ih))',"
        f"tile=3x{rows}:padding=8:margin=8:color=0x202427"
    )
    sheet = workspace / "contact-sheet.jpg"
    run_command(
        [
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-start_number", "1",
            "-i", str(annotated / "frame-%04d.jpg"), "-map", "0:V:0", "-frames:v", "1", "-an",
            "-vf", contact_filter, "-q:v", "3", str(sheet),
        ],
        stage="keyframeExtraction", code="keyframe_extraction_failed",
        message="联系表生成失败，请重试。", run=run,
    )
    if not _nonempty_regular_file(sheet):
        raise LocalPreprocessingFailure(
            "keyframe_extraction_failed", "联系表生成失败，请重试。", "keyframeExtraction",
        )


def _load_scene_changes(
    output_directory: Path, *, stage: StageName,
) -> tuple[list[FrameMetric], list[SceneChange]]:
    try:
        payload = _read_json(output_directory / "scene-changes.json")
        metrics = [FrameMetric(**item) for item in payload["samples"]]
        changes = [SceneChange(**item) for item in payload["sceneChanges"]]
        return metrics, changes
    except OSError as error:
        raise LocalPreprocessingFailure(
            "preprocessing_storage_unavailable", "本地预处理产物无法读取，请检查数据目录后重试。", stage,
        ) from error
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError, ValueError) as error:
        raise stage_failure_for(stage) from error


def _load_motion(output_directory: Path, *, stage: StageName) -> MotionSummary:
    try:
        return MotionSummary(**_read_json(output_directory / "motion.json"))
    except OSError as error:
        raise LocalPreprocessingFailure(
            "preprocessing_storage_unavailable", "本地预处理产物无法读取，请检查数据目录后重试。", stage,
        ) from error
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError, ValueError) as error:
        raise stage_failure_for(stage) from error


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("阶段 JSON 必须是对象")
    return payload


def _filter_names(output: str) -> set[str]:
    names = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and set(fields[0]) <= {"T", "S", "C", "."}:
            names.add(fields[1])
    return names


def _metric_payload(metric: FrameMetric) -> dict[str, float]:
    return {
        "timeSeconds": round(metric.timeSeconds, 3),
        "mafd": round(metric.mafd, 3),
        "sceneScore": round(metric.sceneScore, 3),
    }


def _scene_payload(change: SceneChange) -> dict[str, float]:
    return {"timeSeconds": round(change.timeSeconds, 3), "score": round(change.score, 3)}


def _ffmpeg_version(ffmpeg_path: str, run: CommandRunner) -> str:
    result = run_command(
        [ffmpeg_path, "-hide_banner", "-version"],
        stage="decoding", code="ffmpeg_filters_unavailable",
        message="FFmpeg 版本读取失败，请确认本地 FFmpeg 可用。", run=run,
    )
    match = re.search(r"^ffmpeg version\s+([^\s]+)", result.stdout, re.MULTILINE)
    if match is None:
        raise LocalPreprocessingFailure(
            "ffmpeg_filters_unavailable", "FFmpeg 版本信息无效，请确认本地 FFmpeg 可用。", "decoding",
        )
    return match.group(1)


def _fixed_parameters() -> dict:
    return {
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


def _stage_states_by_name(preprocessing: LocalPreprocessing) -> dict[StageName, object]:
    states = {state.name: state for state in preprocessing.stages}
    if len(states) != len(STAGE_ORDER) or set(states) != set(STAGE_ORDER) or len(preprocessing.stages) != len(STAGE_ORDER):
        raise LocalPreprocessingFailure(
            "preprocessing_unexpected_error", "本地预处理阶段状态无效，请重试。", "decoding",
        )
    return states


def _normalized_preprocessing(preprocessing: LocalPreprocessing) -> LocalPreprocessing:
    normalized = preprocessing.model_copy(deep=True)
    states = _stage_states_by_name(normalized)
    normalized.stages = [states[stage] for stage in STAGE_ORDER]
    return normalized


def _keyframe_scale_filter() -> str:
    return "scale=w='if(gte(iw,ih),min(768,iw),-2)':h='if(gte(iw,ih),-2,min(768,ih))'"


def _keyframe_seek_time(time_seconds: float, reference: ReferenceVideo) -> float:
    last_frame_time = max(0.0, reference.durationSeconds - 1 / reference.frameRate)
    safe_last_frame_time = math.floor(last_frame_time * 1_000_000) / 1_000_000
    return min(time_seconds, safe_last_frame_time)


def _format_timecode(value: float) -> str:
    minutes, seconds = divmod(max(0.0, value), 60)
    return f"{int(minutes):02d}:{seconds:06.3f}"


def _annotation_filter(timecode: str) -> str:
    escaped = timecode.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")
    return (
        f"drawtext=font=monospace:text='{escaped}':fontsize=22:fontcolor=white:"
        "box=1:boxcolor=0x202427@0.7:boxborderw=8:x=8:y=8"
    )


def _keyframe_time(index: int, reference: ReferenceVideo, changes: list[SceneChange]) -> float:
    return select_keyframe_times(reference.durationSeconds, reference.frameRate, changes)[index - 1]


def _summary_from_proxy(proxy: dict) -> AnalysisProxySummary:
    motion = proxy["motion"]
    return AnalysisProxySummary(
        keyframeCount=len(proxy["keyframes"]),
        sceneChangeCount=proxy["scene"]["changeCount"],
        motionP50=motion["p50"],
        motionP90=motion["p90"],
        motionPeak=motion["peak"],
        motionLevel=motion["level"],
    )


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nonempty_regular_file(path: Path) -> bool:
    return not path.is_symlink() and path.is_file() and path.stat().st_size > 0
