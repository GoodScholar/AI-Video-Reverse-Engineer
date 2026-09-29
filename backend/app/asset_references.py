"""Locate persisted uses before removing a workspace asset."""
from typing import Any


def aigc_content_asset_references(state: dict[str, Any], asset_id: str) -> list[dict[str, str]]:
    references = []
    if asset_id in state.get('brief', {}).get('assetIds', []):
        references.append({'kind': 'content_brief', 'label': '创作简报 · 可用素材'})

    for candidate in state.get('candidates', []):
        title = candidate.get('sellingPoint') or candidate.get('id') or '未命名候选'
        for index, beat in enumerate(candidate.get('beats', []), start=1):
            if beat.get('assetId') == asset_id:
                reference = {
                    'kind': 'script_candidate',
                    'label': f'脚本候选「{title}」· 镜头 {index}',
                    'beatIndex': str(index),
                }
                if candidate.get('id'):
                    reference['candidateId'] = candidate['id']
                references.append(reference)
    return references


def _tracks_contain_asset(tracks: list[dict[str, Any]], asset_id: str) -> bool:
    return any(
        clip.get('assetId') == asset_id
        for track in tracks
        for clip in track.get('clips', [])
    )


def _production_record_contains_asset(record: dict[str, Any], asset_id: str) -> bool:
    if asset_id in record.get('sources', {}):
        return True
    if asset_id in record.get('assetIds', []):
        return True
    if any(asset.get('id') == asset_id for asset in record.get('assets', [])):
        return True
    if any(clip.get('assetId') == asset_id for clip in record.get('clips', [])):
        return True
    if any(match.get('assetId') == asset_id for match in record.get('matches', [])):
        return True
    return _tracks_contain_asset(record.get('snapshot', {}).get('tracks', []), asset_id)


def batch_asset_references(state: dict[str, Any], asset_id: str) -> list[dict[str, str]]:
    references = []

    for task in state.get('tasks', []):
        task_id = task.get('id', '')
        title = task.get('sellingPoint') or task_id or '未命名任务'
        variant = task.get('variant', {})

        for track in variant.get('tracks', []):
            for clip in track.get('clips', []):
                if clip.get('assetId') == asset_id:
                    references.append({
                        'kind': 'batch_variant',
                        'label': f'短视频变体「{title}」· {track.get("name") or track.get("id")} · 片段 {clip.get("id")}',
                        'taskId': task_id,
                        'trackId': track.get('id', ''),
                        'clipId': clip.get('id', ''),
                    })

        proposal = task.get('proposal')
        if proposal and _production_record_contains_asset(proposal, asset_id):
            references.append({
                'kind': 'batch_proposal',
                'label': f'短视频变体「{title}」· 推荐草案',
                'taskId': task_id,
            })

        aigc_source = task.get('aigcSource')
        if aigc_source and (
            _production_record_contains_asset(aigc_source, asset_id)
            or asset_id in aigc_source.get('brief', {}).get('assetIds', [])
            or any(beat.get('assetId') == asset_id for beat in aigc_source.get('beats', []))
        ):
            references.append({
                'kind': 'batch_aigc_source',
                'label': f'短视频变体「{title}」· 脚本交接 {aigc_source.get("candidateId")}',
                'taskId': task_id,
                'candidateId': aigc_source.get('candidateId', ''),
            })

        voiceover = task.get('voiceover')
        if voiceover and (
            _production_record_contains_asset(voiceover, asset_id)
            or _production_record_contains_asset(voiceover.get('plan', {}), asset_id)
        ):
            references.append({
                'kind': 'batch_voiceover',
                'label': f'短视频变体「{title}」· 配音 {voiceover.get("id")}',
                'taskId': task_id,
                'voiceoverId': voiceover.get('id', ''),
            })

        for recognition in variant.get('subtitles', {}).get('recognitions', []):
            if _production_record_contains_asset(recognition, asset_id):
                references.append({
                    'kind': 'batch_recognition',
                    'label': f'短视频变体「{title}」· 字幕识别 {recognition.get("id")}',
                    'taskId': task_id,
                    'recognitionId': recognition.get('id', ''),
                })

        runs = {run.get('id'): run for run in variant.get('runs', [])}
        referenced_run_ids = set()
        for run_id, run in runs.items():
            if _production_record_contains_asset(run, asset_id):
                referenced_run_ids.add(run_id)
                references.append({
                    'kind': 'batch_preview',
                    'label': f'短视频变体「{title}」· 预览 {run_id}',
                    'taskId': task_id,
                    'runId': run_id or '',
                })

        for review in task.get('reviews', []):
            if review.get('runId') in referenced_run_ids:
                references.append({
                    'kind': 'batch_review',
                    'label': f'短视频变体「{title}」· 审核 {review.get("id")}',
                    'taskId': task_id,
                    'runId': review.get('runId', ''),
                    'reviewId': review.get('id', ''),
                })

        for delivery in variant.get('exports', []):
            if _production_record_contains_asset(delivery, asset_id):
                references.append({
                    'kind': 'batch_delivery',
                    'label': f'短视频变体「{title}」· 交付 {delivery.get("id")}',
                    'taskId': task_id,
                    'deliveryId': delivery.get('id', ''),
                })

    return references


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
