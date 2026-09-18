import io
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

from app.analysis_providers.base import ProviderResult
from app.reference_video import ReferenceVideo
from app.shot_analysis import analyze_preparation_shot, build_shot_analysis_input


def source(tmp_path):
    binary = shutil.which('ffmpeg')
    if not binary:
        pytest.skip('ffmpeg unavailable')
    path = tmp_path / 'project-files/project-001/reference-media/video-001.mp4'
    path.parent.mkdir(parents=True)
    subprocess.run([binary, '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=64x64:r=8:d=1',
                    '-f', 'lavfi', '-i', 'color=blue:s=64x64:r=8:d=1', '-filter_complex',
                    '[0:v][1:v]concat=n=2:v=1:a=0', '-pix_fmt', 'yuv420p', str(path)], check=True)
    media = ReferenceVideo(id='video-001', originalName='private.mp4', format='mp4',
                           sizeBytes=path.stat().st_size, durationSeconds=2, width=64, height=64, frameRate=8)
    return SimpleNamespace(id='project-001', referenceMedia=media), binary


def test_shot_contact_sheet_only_contains_selected_shot(tmp_path):
    project, ffmpeg = source(tmp_path)
    value = build_shot_analysis_input(tmp_path, project, {'startSeconds': 1, 'endSeconds': 2}, ffmpeg)
    assert value.analysisProxy.source.durationSeconds == 1
    assert all(0 <= item.timeSeconds < 1 for item in value.analysisProxy.keyframes)
    assert len(value.analysisProxy.keyframes) == 4
    with Image.open(io.BytesIO(value.contactSheetBytes)) as sheet:
        pixel = sheet.getpixel((sheet.width // 4, sheet.height // 4))
        assert pixel[2] > 180 and pixel[0] < 50
    assert b'private.mp4' not in value.contactSheetBytes


def test_analysis_uses_shot_frames_then_text_only_prompt_request(tmp_path):
    project, ffmpeg = source(tmp_path)
    facts = {'observedFacts': {'staticVisual': dict.fromkeys(
        ['subject', 'scene', 'composition', 'viewpoint', 'lighting', 'color', 'visualStyle'], '蓝色画面'),
        'temporal': dict.fromkeys(['subjectMotion', 'environmentalMotion', 'cameraMotion', 'rhythm'], '无法确认')},
        'generationSuggestions': {**dict.fromkeys(['subjectMotion', 'environmentalMotion', 'cameraMotion', 'rhythm', 'audio'], '保持静止'), 'suggestedDuration': 1}}
    prompts = dict.fromkeys(['positiveZh', 'negativeZh', 'positiveEn', 'negativeEn'], 'example')
    requests = []
    class Provider:
        def analyze(self, request):
            requests.append(request)
            return ProviderResult(rawText=json.dumps(facts if len(requests) == 1 else prompts))
    result = analyze_preparation_shot(tmp_path, project, {'startSeconds': 1, 'endSeconds': 2}, Provider(), 'test', ffmpeg)
    assert result['prompts'] == prompts
    assert len(requests) == 2
    assert requests[0].analysisInput.analysisProxy.source.durationSeconds == 1
    assert requests[1].analysisInput is None
    assert str(tmp_path) not in requests[0].prompt
    assert '1.00' in result['notes'] and '2.00' in result['notes']

@pytest.mark.parametrize('bounds', [(-1, 1), (1, 1), (1, 3), (float('nan'), 2)])
def test_shot_sampling_rejects_invalid_bounds_before_decoding(tmp_path, bounds):
    media = ReferenceVideo(id='video-001', originalName='test.mp4', format='mp4', sizeBytes=1,
                          durationSeconds=2, width=64, height=64, frameRate=8)
    project = SimpleNamespace(id='project-001', referenceMedia=media)
    with pytest.raises(ValueError, match='时间范围'):
        build_shot_analysis_input(tmp_path, project, {'startSeconds': bounds[0], 'endSeconds': bounds[1]})


def test_sparse_vfr_shot_reuses_valid_frames_without_crossing_cut(tmp_path):
    project, ffmpeg = source(tmp_path)
    original = tmp_path / 'project-files/project-001/reference-media/video-001.mp4'
    sparse = tmp_path / 'sparse.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-i', str(original), '-vf',
                    "select='lt(t,0.3)+gte(t,1)'", '-fps_mode', 'vfr', str(sparse)], check=True)
    sparse.replace(original)
    project.referenceMedia.sizeBytes = original.stat().st_size
    project.referenceMedia.frameRate = 5.5
    value = build_shot_analysis_input(tmp_path, project, {'startSeconds': 0, 'endSeconds': 1}, ffmpeg)
    assert max(item.timeSeconds for item in value.analysisProxy.keyframes) <= .25
    with Image.open(io.BytesIO(value.contactSheetBytes)) as sheet:
        for x, y in [(160, 90), (480, 90), (160, 294), (480, 294)]:
            r, g, b = sheet.getpixel((x, y))
            assert r > 180 and b < 50


def test_single_frame_shot_produces_four_honest_repeated_samples(tmp_path):
    project, ffmpeg = source(tmp_path)
    value = build_shot_analysis_input(tmp_path, project, {'startSeconds': 0, 'endSeconds': .125}, ffmpeg)
    assert [frame.timeSeconds for frame in value.analysisProxy.keyframes] == [0, 0, 0, 0]


def test_main_preparation_routing_reuses_configured_provider_and_rejects_foreign_origin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from test_depth_capture_api import _completed_project, ManualComputeQueue
    from test_semantic_analysis_api import InMemoryCredentials
    project = _completed_project(tmp_path)
    directory = tmp_path / 'project-files/project-001/local-preprocessing/preprocess-001'
    (directory / 'scene-changes.json').write_text(json.dumps({'sceneChanges': [{'timeSeconds': 1}]}))
    project['semanticAnalysis'] = {
        'id': 'analysis-001', 'sourceReferenceMediaId': 'video-001', 'sourcePreprocessingId': 'preprocess-001',
        'provider': 'bailian', 'model': 'qwen3.7-flash', 'promptVersion': 1, 'status': 'completed',
        'createdAt': project['createdAt'], 'updatedAt': project['updatedAt'], 'result': None,
    }
    (tmp_path / 'projects.json').write_text(json.dumps([project]))
    observed = []
    def analyze(*args):
        observed.append(args)
        return {'notes': '当前镜头', 'prompts': dict.fromkeys(['positiveZh', 'negativeZh', 'positiveEn', 'negativeEn'], 'text')}
    monkeypatch.setattr(main, 'analyze_preparation_shot', analyze)
    provider = object()
    client = TestClient(main.create_app(data_dir=tmp_path, credential_store=InMemoryCredentials(),
        local_compute_queue=ManualComputeQueue(), provider_registry=lambda **kwargs: provider))
    assert client.put('/api/analysis-providers/bailian/configuration', json={'model': 'qwen3.7-flash'}).status_code == 200
    base = '/api/projects/project-001/preparation'
    state = client.get(base).json()
    assert len(state['shots']) == 2
    body = {key: state[key] for key in ['revision', 'sourceId', 'preprocessingId']}
    body['disclosureAccepted'] = True
    endpoint = base + '/shots/shot-002/analyze'
    assert client.post(endpoint, json=body, headers={'origin': 'https://foreign.example'}).status_code == 403
    assert not observed
    response = client.post(endpoint, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['shots'][1]['notes'] == '当前镜头'
    assert observed[0][2]['startSeconds'] == 1
    assert observed[0][3] is provider
