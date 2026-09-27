from copy import deepcopy
import pytest
from app.shot_production import result_signature


@pytest.mark.parametrize('change', ['brief', 'prompt', 'negativePrompt', 'duration', 'assets', 'node'])
def test_generation_plan_changes_require_recheck(change):
    brief = {'theme': '商品', 'duration': 3}
    shot = {'duration': 3, 'prompt': '转动', 'negativePrompt': '', 'assetIds': ['a', 'b'],
            'nodes': [{'id': 'trim', 'kind': 'trim', 'input': 'asset:a', 'params': {'start': 0, 'end': 3}, 'status': 'pending'}]}
    before = result_signature(brief, shot)
    if change == 'brief': brief['theme'] = '新商品'
    elif change == 'assets': shot['assetIds'] = ['b']
    elif change == 'node': shot['nodes'][0]['params']['start'] = 1
    elif change == 'duration': shot['duration'] = 2
    else: shot[change] = '修改'
    assert result_signature(brief, shot) != before


def test_labels_adoption_and_runtime_status_do_not_invalidate_review():
    brief = {'duration': 3}
    shot = {'title': '一', 'duration': 3, 'assetIds': ['a', 'b'], 'nodes': [
        {'id': 'trim', 'kind': 'trim', 'input': 'asset:a', 'params': {'start': 0, 'end': 3}, 'status': 'pending'}]}
    changed = deepcopy(shot)
    changed.update(title='重命名', resultAssetId='new', assetIds=['b', 'a'], duration=3.0)
    changed['nodes'][0].update(status='completed', artifacts=[{'name': 'output.mp4'}])
    changed['nodes'][0]['params']['start'] = 0.0
    assert result_signature(brief, shot) == result_signature({**brief, 'inputKind': 'reference_video'}, changed)
