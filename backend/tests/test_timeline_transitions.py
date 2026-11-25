import pytest

from app.timeline import validate_transitions, validate_workspace


def _clip(clip_id, asset_id, start, duration, *, speed=1):
    return {
        "id": clip_id, "assetId": asset_id, "start": start, "inPoint": 0,
        "duration": duration, "speed": speed, "volume": 1, "fadeIn": 0, "fadeOut": 0,
    }


def _tracks(second_start=1.6, *, second_speed=1):
    return [{
        "id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False,
        "clips": [
            _clip("left", "shot-a", 0, 2),
            _clip("right", "shot-b", second_start, 2, speed=second_speed),
        ],
    }]


def _transition(template="dissolve", **values):
    item = {
        "id": "boundary-1", "leftClipId": "left", "rightClipId": "right",
        "template": template, "enabled": True, "duration": .4, "overlap": .4,
        "audioCrossfade": True, "reason": "场景变化并贴合卖点强调",
    }
    item.update(values)
    return item


ASSETS = {
    "shot-a": {"id": "shot-a", "kind": "video", "duration": 4},
    "shot-b": {"id": "shot-b", "kind": "video", "duration": 4},
}


@pytest.mark.parametrize("template", ["dissolve", "push", "wipe", "flash-white", "pixelize"])
def test_transition_templates_are_saved_as_normalized_adjacent_boundaries(template):
    tracks = _tracks()
    validate_workspace({
        "revision": 0, "settings": {"width": 1280, "height": 720, "fps": 30},
        "tracks": tracks, "transitions": [_transition(template)],
    }, ASSETS)

    assert validate_transitions([_transition(template)], tracks) == [_transition(template)]


def test_hard_cut_and_controlled_speed_change_do_not_claim_overlap_or_audio_crossfade():
    cut_tracks = _tracks(second_start=2)
    cut = _transition("cut", duration=0, overlap=0, audioCrossfade=False, reason="普通叙事保持硬切")
    speed = _transition("speed-change", duration=0, overlap=0, audioCrossfade=False,
                        reason="受控速度变化突出卖点")

    assert validate_transitions([cut], cut_tracks) == [cut]
    assert validate_transitions([speed], _tracks(second_start=2, second_speed=1.25)) == [speed]

    with pytest.raises(ValueError, match="速度变化"):
        validate_transitions([speed], _tracks(second_start=2))


def test_disabled_transition_is_a_contiguous_hard_cut_but_keeps_its_suggestion():
    transition = _transition(enabled=False)

    assert validate_transitions([transition], _tracks(second_start=2)) == [transition]
    with pytest.raises(ValueError, match="禁用"):
        validate_transitions([transition], _tracks(second_start=1.6))


@pytest.mark.parametrize(("change", "message"), [
    ({"rightClipId": "missing"}, "相邻"),
    ({"overlap": .5}, "持续时间"),
    ({"duration": 2, "overlap": 2}, "短于相邻片段"),
])
def test_transition_rejects_unsupported_boundary_combinations(change, message):
    with pytest.raises(ValueError, match=message):
        validate_transitions([_transition(**change)], _tracks())
