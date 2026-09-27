from app.video_aspect import resolve_product_aspect


def test_smart_aspect_primary_asset_is_stable_across_input_order():
    assets = [
        {'id': 'asset-b', 'width': 1600, 'height': 1000},
        {'id': 'asset-a', 'width': 1616, 'height': 1000},
    ]

    assert resolve_product_aspect('smart', assets) == resolve_product_aspect('smart', list(reversed(assets)))
