"""Locate persisted uses before removing a workspace asset."""
from typing import Any


def preproduction_asset_references(state: dict[str, Any], asset_id: str) -> list[dict[str, str]]:
    references = []

    def add(kind, label, **location):
        references.append({'kind': kind, 'label': label, **location})

    for shot in state['shots']:
        title = shot.get('title') or shot['id']
        if asset_id in shot.get('assetIds', []):
            add('shot_binding', f'镜头「{title}」· 绑定素材')
        if shot.get('resultAssetId') == asset_id:
            add('shot_result', f'镜头「{title}」· 当前采用结果')
        for index, version in enumerate(shot.get('_resultVersions', [])):
            if version.get('assetId') == asset_id:
                add('candidate', f'镜头「{title}」· 候选 {index + 1}')
        for node in shot.get('nodes', []):
            if node.get('input') == f'asset:{asset_id}':
                add('node', f'镜头「{title}」· 节点 {node["id"]}')

    return references


def timeline_asset_references(timeline: dict[str, Any], asset_id: str) -> list[dict[str, str]]:
    references = []

    def add(kind, label, **location):
        references.append({'kind': kind, 'label': label, **location})

    def track_refs(tracks, kind, prefix, run_id=None):
        for track in tracks:
            for clip in track.get('clips', []):
                if clip.get('assetId') == asset_id:
                    add(kind, f'{prefix} · {track.get("name") or track["id"]} · 片段 {clip["id"]}',
                        trackId=track['id'], clipId=clip['id'], **({'runId': run_id} if run_id else {}))

    track_refs(timeline['tracks'], 'timeline', '时间线')
    for run in timeline['runs']:
        before = len(references)
        track_refs(run.get('snapshot', {}).get('tracks', []), 'timeline_history', f'历史输出 {run["id"]}', run['id'])
        if len(references) == before and asset_id in run.get('sources', {}):
            add('timeline_history', f'历史输出 {run["id"]} · 源素材', runId=run['id'])
    return references


def asset_references(state: dict[str, Any], timeline: dict[str, Any], asset_id: str) -> list[dict[str, str]]:
    """Compatibility composition for callers that already hold both workflow states."""
    return preproduction_asset_references(state, asset_id) + timeline_asset_references(timeline, asset_id)
