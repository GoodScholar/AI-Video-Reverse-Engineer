import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .image_preprocessing import (
    DecodedImageFacts,
    ImagePreprocessingFailure,
    NormalizedImageFacts,
    ProxyImageFacts,
    assess_image_reproducibility,
    inspect_image,
    normalize_image,
    write_analysis_proxy,
)
from .local_preprocessing import (
    ALGORITHM_VERSION,
    ImageSize,
    ImageProxySummary,
    LocalPreprocessing,
    MediaType,
    ReproducibilityAssessment,
    StageName,
    stage_order_for,
)
from .local_preprocessing_runner import LocalPreprocessingFailure, PreprocessingRunResult
from .local_preprocessing_storage import (
    reset_stage_artifacts,
    stage_artifacts_are_valid,
    validate_completed_stages,
    write_stage_json,
)
from .reference_media import ReferenceImage


def run_image_preprocessing(
    *,
    source_path: Path,
    reference: ReferenceImage,
    preprocessing: LocalPreprocessing,
    output_directory: Path,
    on_stage_started: Callable[[StageName], None],
    on_stage_completed: Callable[[StageName], None],
) -> PreprocessingRunResult:
    if preprocessing.mediaType != "image":
        raise LocalPreprocessingFailure(
            "preprocessing_unexpected_error", "本地预处理媒体类型无效，请重试。", "imageDecoding",
        )
    validated = validate_completed_stages(preprocessing, output_directory).preprocessing
    states = {state.name: state for state in validated.stages}
    order = stage_order_for(preprocessing.mediaType)
    if len(states) != len(order) or set(states) != set(order):
        raise LocalPreprocessingFailure(
            "preprocessing_unexpected_error", "本地预处理阶段状态无效，请重试。", "imageDecoding",
        )
    assessment: ReproducibilityAssessment | None = None
    summary: ImageProxySummary | None = None
    decoded_facts: DecodedImageFacts | None = None
    normalized_facts: NormalizedImageFacts | None = None
    proxy_facts: ProxyImageFacts | None = None
    for stage in order:
        if states[stage].status == "completed":
            continue
        on_stage_started(stage)
        try:
            if stage == "imageDecoding":
                decoded_facts = inspect_image(source_path)
            elif stage == "imageNormalization":
                normalized_facts = _commit_image_artifact(
                    output_directory / "normalized.png",
                    lambda target: normalize_image(source_path, target),
                    inspect_image,
                )
            elif stage == "proxyGeneration":
                proxy_facts = _commit_image_artifact(
                    output_directory / "analysis-proxy.jpg",
                    lambda target: write_analysis_proxy(output_directory / "normalized.png", target),
                    inspect_image,
                )
            else:
                assessment = assess_image_reproducibility(
                    output_directory / "normalized.png", output_directory / "analysis-proxy.jpg",
                )
                decoded_facts = decoded_facts or inspect_image(source_path)
                normalized_facts = normalized_facts or _normalized_facts(output_directory)
                proxy_facts = proxy_facts or _proxy_facts(output_directory)
                summary = _image_summary(decoded_facts, normalized_facts, proxy_facts, assessment)
                _write_manifest(output_directory, reference, assessment, summary)
            if not stage_artifacts_are_valid(stage, output_directory, validated):
                raise ValueError("阶段产物无效")
        except ImagePreprocessingFailure as error:
            _reset_after_failure(output_directory, stage, preprocessing.mediaType)
            raise LocalPreprocessingFailure(error.code, error.message, stage) from error
        except OSError as error:
            _reset_after_failure(output_directory, stage, preprocessing.mediaType)
            raise LocalPreprocessingFailure(
                "preprocessing_storage_unavailable", "本地预处理产物无法安全读写，请检查数据目录后重试。", stage,
            ) from error
        except (ValueError, ValidationError) as error:
            _reset_after_failure(output_directory, stage, preprocessing.mediaType)
            raise LocalPreprocessingFailure(_failure_code_for(stage), _failure_message_for(stage), stage) from error
        on_stage_completed(stage)
    if assessment is None or summary is None:
        return _load_result(output_directory)
    return PreprocessingRunResult(proxy_summary=summary, assessment=assessment)


def _commit_image_artifact(
    destination: Path,
    writer: Callable[[Path], object],
    validator: Callable[[Path], object],
) -> object:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}-", suffix=".part",
    )
    temporary = Path(raw_path)
    os.close(descriptor)
    try:
        facts = writer(temporary)
        validator(temporary)
        os.replace(temporary, destination)
        return facts
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_manifest(
    output_directory: Path,
    reference: ReferenceImage,
    assessment: ReproducibilityAssessment,
    summary: ImageProxySummary,
) -> None:
    write_stage_json(output_directory, "manifest.json", {
        "schemaVersion": 1,
        "algorithmVersion": ALGORITHM_VERSION,
        "mediaType": "image",
        "sourceReferenceMediaId": reference.id,
        "artifacts": {
            "normalized": "normalized.png", "analysisProxy": "analysis-proxy.jpg",
        },
        "proxySummary": summary.model_dump(),
        "reproducibilityAssessment": assessment.model_dump(),
    })


def _normalized_facts(output_directory: Path) -> NormalizedImageFacts:
    facts = inspect_image(output_directory / "normalized.png")
    return NormalizedImageFacts(displaySize=facts.displaySize, transparencyFlattened=False)


def _proxy_facts(output_directory: Path) -> ProxyImageFacts:
    path = output_directory / "analysis-proxy.jpg"
    facts = inspect_image(path)
    return ProxyImageFacts(displaySize=facts.displaySize, byteSize=path.stat().st_size)


def _image_summary(
    decoded: DecodedImageFacts,
    normalized: NormalizedImageFacts,
    proxy: ProxyImageFacts,
    assessment: ReproducibilityAssessment,
) -> ImageProxySummary:
    return ImageProxySummary(
        originalDisplaySize=ImageSize(width=decoded.displaySize[0], height=decoded.displaySize[1]),
        normalizedSize=ImageSize(width=normalized.displaySize[0], height=normalized.displaySize[1]),
        proxySize=ImageSize(width=proxy.displaySize[0], height=proxy.displaySize[1]),
        transparencyFlattened=normalized.transparencyFlattened or decoded.hasTransparency,
        applicabilityStatus=assessment.status,
    )


def _load_result(output_directory: Path) -> PreprocessingRunResult:
    try:
        with (output_directory / "manifest.json").open(encoding="utf-8") as file:
            manifest = json.load(file)
        assessment = ReproducibilityAssessment(**manifest["reproducibilityAssessment"])
        summary = ImageProxySummary(**manifest["proxySummary"])
        normalized = _normalized_facts(output_directory)
        proxy = _proxy_facts(output_directory)
        if (
            summary.originalDisplaySize != summary.normalizedSize
            or summary.normalizedSize != ImageSize(width=normalized.displaySize[0], height=normalized.displaySize[1])
            or summary.proxySize != ImageSize(width=proxy.displaySize[0], height=proxy.displaySize[1])
            or summary.applicabilityStatus != assessment.status
        ):
            raise ValueError("图片预处理汇总不一致")
        return PreprocessingRunResult(proxy_summary=summary, assessment=assessment)
    except OSError as error:
        raise LocalPreprocessingFailure(
            "preprocessing_storage_unavailable", "本地预处理结果无法读取，请检查数据目录后重试。", "reproducibilityAssessment",
        ) from error
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError, ValueError) as error:
        raise LocalPreprocessingFailure(
            "assessment_failed", "可复现性判断结果无效，请重试。", "reproducibilityAssessment",
        ) from error


def _reset_after_failure(output_directory: Path, stage: StageName, media_type: MediaType) -> None:
    try:
        reset_stage_artifacts(output_directory, stage, media_type)
    except OSError as error:
        raise LocalPreprocessingFailure(
            "preprocessing_storage_unavailable", "本地预处理产物无法安全读写，请检查数据目录后重试。", stage,
        ) from error


def _failure_code_for(stage: StageName) -> str:
    return {
        "imageDecoding": "image_decode_failed",
        "imageNormalization": "image_normalization_failed",
        "proxyGeneration": "proxy_generation_failed",
        "reproducibilityAssessment": "assessment_failed",
    }[stage]


def _failure_message_for(stage: StageName) -> str:
    return {
        "imageDecoding": "参考图片无法完成本地解码。",
        "imageNormalization": "参考图片标准化失败，请重试。",
        "proxyGeneration": "分析代理无法压缩到安全上限。",
        "reproducibilityAssessment": "图片预处理产物无法完成适用性检查。",
    }[stage]
