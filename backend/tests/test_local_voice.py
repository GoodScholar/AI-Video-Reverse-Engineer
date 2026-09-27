import json
from app.local_voice import LocalVoiceService


def test_catalog_rejects_incompatible_model_without_starting_worker(tmp_path):
    model=tmp_path/'model';model.mkdir()
    model.joinpath('model.safetensors').write_bytes(b'weights')
    model.joinpath('config.json').write_text(json.dumps({'model_type':'qwen3_tts','tts_model_type':'custom_voice','tts_model_size':'1b7','quantization':{'bits':8}}))
    python=tmp_path/'python';python.write_text('python')
    service=LocalVoiceService(root=tmp_path,python=python)
    assert service.catalog()['available'] is False


def test_unverified_custom_directory_is_not_labelled_with_official_revision(tmp_path):
    model=tmp_path/'other-model';model.mkdir()
    model.joinpath('config.json').write_text(json.dumps({'model_type':'qwen3_tts','tts_model_type':'custom_voice','tts_model_size':'0b6','quantization':{'bits':8}}))
    model.joinpath('model.safetensors').write_bytes(b'weights')
    tmp_path.joinpath('model-manifest.json').write_text(json.dumps({'verified':True,'revision':'old-official-revision'}))
    python=tmp_path/'python';python.write_text('python')
    service=LocalVoiceService(root=tmp_path,model=model,python=python)
    assert service.catalog()['available'] is True
    assert service.catalog()['modelRevision']=='unverified-local-directory'
