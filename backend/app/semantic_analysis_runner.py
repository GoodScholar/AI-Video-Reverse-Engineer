from pathlib import Path
from typing import TYPE_CHECKING

from .analysis_input import build_analysis_input
from .analysis_prompt import build_analysis_prompt
from .analysis_providers.base import AnalysisProvider, ProviderRequest
from .analysis_response import validate_or_repair
from .semantic_analysis import StructuredVisualAnalysis

if TYPE_CHECKING:
    from .main import Project


def run_semantic_analysis(
    *,
    data_dir: Path,
    project: "Project",
    provider: AnalysisProvider,
    model: str,
) -> StructuredVisualAnalysis:
    """Run exactly one provider analysis and, if needed, its one safe repair."""

    analysis_input = build_analysis_input(data_dir, project)
    request = ProviderRequest(
        analysisInput=analysis_input,
        prompt=build_analysis_prompt(analysis_input),
        model=model,
    )
    return validate_or_repair(provider, provider.analyze(request), request)
