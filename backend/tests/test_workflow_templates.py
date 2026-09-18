import pytest


def test_wan_i2v_builds_a_candidate_api_graph_with_bound_inputs():
    from app.workflow_templates import build_workflow, template_info

    workflow = build_workflow(
        "wan22_i2v", "a dancer moves", "blurry", 832, 480, 81, 16, 42, "input.png"
    )

    assert template_info("wan22_i2v")["status"] == "candidate"
    assert workflow["1"] == {"class_type": "LoadImage", "inputs": {"image": "input.png"}}
    assert workflow["6"]["class_type"] == "WanImageToVideo"
    assert workflow["6"]["inputs"]["width"] == 832
    assert workflow["6"]["inputs"]["height"] == 480
    assert workflow["6"]["inputs"]["length"] == 81
    assert workflow["9"]["inputs"]["noise_seed"] == 42
    assert workflow["14"]["inputs"]["fps"] == 16


def test_fun_control_binds_depth_video_to_official_control_node():
    from app.workflow_templates import build_workflow, template_info

    workflow = build_workflow(
        "wan22_fun_control", "a dancer moves", "blurry", 832, 480, 81, 16, 42,
        "input.png", "controls/depth.mp4",
    )

    info = template_info("wan22_fun_control")
    assert info["status"] == "candidate"
    assert "Wan22FunControlToVideo" in info["requiredNodes"]
    assert workflow["2"] == {"class_type": "LoadVideo", "inputs": {"file": "controls/depth.mp4"}}
    assert workflow["17"] == {"class_type": "GetVideoComponents", "inputs": {"video": ["2", 0]}}
    assert workflow["7"]["class_type"] == "Wan22FunControlToVideo"
    assert workflow["7"]["inputs"]["control_video"] == ["17", 0]


def test_candidate_template_accepts_the_ui_frame_and_fps_range_without_claiming_verification():
    from app.workflow_templates import build_workflow, template_info

    workflow = build_workflow(
        "wan22_i2v", "a dancer moves", "blurry", 832, 480, 17, 8, 42, "input.png"
    )

    assert workflow["6"]["inputs"]["length"] == 17
    assert workflow["14"]["inputs"]["fps"] == 8
    assert template_info("wan22_i2v")["status"] == "candidate"


@pytest.mark.parametrize(
    "strategy,width,height,frames,fps,depth_video",
    [
        ("unknown", 832, 480, 81, 16, None),
        ("wan22_i2v", 831, 480, 81, 16, None),
        ("wan22_i2v", 832, 480, 16, 16, None),
        ("wan22_i2v", 832, 480, 18, 16, None),
        ("wan22_i2v", 832, 480, 165, 16, None),
        ("wan22_i2v", 832, 480, 81, 7, None),
        ("wan22_i2v", 832, 480, 81, 25, None),
        ("wan22_fun_control", 832, 480, 81, 16, None),
    ],
)
def test_build_workflow_rejects_inputs_that_cannot_form_the_fixed_template(
    strategy, width, height, frames, fps, depth_video
):
    from app.workflow_templates import build_workflow

    with pytest.raises(ValueError):
        build_workflow(strategy, "positive", "negative", width, height, frames, fps, 1, "input.png", depth_video)
