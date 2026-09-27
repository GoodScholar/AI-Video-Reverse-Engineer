import json
from types import SimpleNamespace

import pytest

from app.reproduction import OutputSettings, ReproductionStore, source_hash, default_settings


def project():
    return SimpleNamespace(
        id='project-1', referenceMedia=SimpleNamespace(id='video-1', width=320, height=568, type='video', durationSeconds=14.53),
        semanticAnalysis=SimpleNamespace(id='analysis-1', status='completed', sourceReferenceMediaId='video-1', result={'subject': '人物'}),
        localPreprocessing=SimpleNamespace(id='pre-1'), activeDepthCaptureId=None,
    )


def test_generation_dimensions_preserve_portrait_and_explain_duration_adjustment():
    settings, adjustments = default_settings(project())
    assert settings.aspectMode == 'smart'
    assert settings.height > settings.width
    assert settings.width % 16 == settings.height % 16 == 0
    assert settings.frames == 81 and settings.fps == 16
    assert any('14.53' in message for message in adjustments)


def test_source_hash_changes_when_analysis_changes():
    p = project()
    old = source_hash(p)
    p.semanticAnalysis.result = {'subject': '产品'}
    assert source_hash(p) != old


def test_store_roundtrip_and_rejects_symlink(tmp_path):
    store = ReproductionStore(tmp_path)
    state = store.load(project())
    state.revision = 2
    store.save('project-1', state)
    assert store.load(project()).revision == 2
    path = tmp_path / 'project-files/project-1/reproduction/state.json'
    path.unlink()
    external = tmp_path / 'external.json'
    external.write_text('{}')
    path.symlink_to(external)
    with pytest.raises(OSError):
        store.load(project())


@pytest.mark.parametrize('change', [{'width': 321}, {'frames': 80}, {'frames': 5000}, {'fps': 0}, {'seed': -1}])
def test_output_settings_reject_unsupported_values(change):
    with pytest.raises(ValueError):
        OutputSettings(**change)


@pytest.mark.parametrize(('mode', 'dimensions'), [
    ('21:9', (1120, 480)), ('16:9', (1024, 576)), ('4:3', (960, 720)),
    ('1:1', (720, 720)), ('3:4', (720, 960)), ('9:16', (576, 1024)),
])
def test_output_settings_resolve_each_explicit_aspect(mode, dimensions):
    settings = OutputSettings.for_aspect(mode)

    assert settings.aspectMode == mode
    assert (settings.width, settings.height) == dimensions
