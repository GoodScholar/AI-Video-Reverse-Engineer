from app import asset_references


def test_aigc_references_include_brief_and_all_persisted_candidate_beats():
    state = {
        "brief": {"assetIds": ["asset-1", "asset-2"]},
        "candidates": [
            {
                "id": "candidate-current",
                "sellingPoint": "当前候选",
                "beats": [{"assetId": "asset-1"}, {"assetId": "asset-2"}],
            },
            {
                "id": "candidate-history",
                "sellingPoint": "历史候选",
                "beats": [{"assetId": "asset-1"}],
            },
        ],
    }

    assert hasattr(asset_references, "aigc_content_asset_references")
    references = asset_references.aigc_content_asset_references(state, "asset-1")

    assert references == [
        {"kind": "content_brief", "label": "创作简报 · 可用素材"},
        {
            "kind": "script_candidate",
            "label": "脚本候选「当前候选」· 镜头 1",
            "candidateId": "candidate-current",
            "beatIndex": "1",
        },
        {
            "kind": "script_candidate",
            "label": "脚本候选「历史候选」· 镜头 1",
            "candidateId": "candidate-history",
            "beatIndex": "1",
        },
    ]


def test_batch_references_cover_current_and_historical_production_objects():
    clip = {"id": "clip-1", "assetId": "asset-1"}
    snapshot = {"tracks": [{"id": "track-1", "name": "主视频", "clips": [clip]}]}
    state = {
        "tasks": [{
            "id": "task-1",
            "sellingPoint": "核心卖点",
            "variant": {
                "tracks": snapshot["tracks"],
                "subtitles": {"recognitions": [{"id": "recognition-1", "snapshot": snapshot, "sources": {}}]},
                "runs": [{"id": "run-1", "snapshot": snapshot, "sources": {}}],
                "exports": [{
                    "id": "delivery-1",
                    "snapshot": {"tracks": []},
                    "sources": {"asset-1": "asset-copy.mp4"},
                    "assets": [{"id": "asset-1"}],
                }],
            },
            "proposal": {
                "assetIds": ["asset-1"],
                "clips": [clip],
                "matches": [{"clipId": "clip-1", "assetId": "asset-1"}],
            },
            "aigcSource": {
                "candidateId": "candidate-1",
                "brief": {"assetIds": ["asset-1"]},
                "beats": [{"assetId": "asset-1"}],
                "assets": [{"id": "asset-1"}],
            },
            "voiceover": {"id": "voiceover-1", "assetIds": ["asset-1"]},
            "reviews": [{"id": "review-1", "runId": "run-1"}],
        }],
    }

    assert hasattr(asset_references, "batch_asset_references")
    references = asset_references.batch_asset_references(state, "asset-1")

    assert [reference["kind"] for reference in references] == [
        "batch_variant",
        "batch_proposal",
        "batch_aigc_source",
        "batch_voiceover",
        "batch_recognition",
        "batch_preview",
        "batch_review",
        "batch_delivery",
    ]
    assert all(reference["taskId"] == "task-1" for reference in references)
    assert next(reference for reference in references if reference["kind"] == "batch_variant") == {
        "kind": "batch_variant",
        "label": "短视频变体「核心卖点」· 主视频 · 片段 clip-1",
        "taskId": "task-1",
        "trackId": "track-1",
        "clipId": "clip-1",
    }
    assert next(reference for reference in references if reference["kind"] == "batch_review")["reviewId"] == "review-1"
    assert next(reference for reference in references if reference["kind"] == "batch_delivery")["deliveryId"] == "delivery-1"
