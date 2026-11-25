from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.preproduction import PreproductionStore
from app.transcription import (
    TranscriptionBlock,
    TranscriptionError,
    TranscriptionModelUnavailable,
    build_transcription_blocks,
    create_transcription_router,
    normalize_block_transcript,
)


PROJECT_ID = "project-1"
INTENT = {"x-aivre-intent": "semantic-analysis"}


class ManualQueue:
    def __init__(self):
        self.jobs = []

    def submit(self, run_kind, owner_id, handler, **_kwargs):
        self.jobs.append((run_kind, owner_id, handler))
        return True

    def run(self, index=0):
        _, owner_id, handler = self.jobs.pop(index)
        handler(owner_id)


def install_video(tmp_path: Path, asset_id: str, duration: float):
    store = PreproductionStore(tmp_path)
    state = store.load(PROJECT_ID)
    state["assets"].append({
        "id": asset_id, "assetVersionId": f"{asset_id}-frozen-v1", "name": "授权素材", "kind": "video",
        "role": "motion", "file": f"{asset_id}.mp4", "duration": duration,
    })
    store.save(PROJECT_ID, state)
    source = store.path(PROJECT_ID, "assets", f"{asset_id}.mp4")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"authorized-video")


def client_for(tmp_path, queue, runner, *, duration=1800.1):
    install_video(tmp_path, "video-a", duration)
    app = FastAPI()
    app.include_router(create_transcription_router(
        tmp_path, lambda project_id: {"id": project_id} if project_id == PROJECT_ID else None, queue,
        runner=runner, duration_probe=lambda _source: duration,
        model_identity_resolver=lambda _settings: "e2-fixture-model-sha256",
    ))
    return TestClient(app)


def test_later_blocks_convert_relative_word_times_to_absolute_once_and_drop_overlap_duplicates():
    block = TranscriptionBlock(index=2, start_seconds=1140.0, end_seconds=2340.0)

    transcript = normalize_block_transcript(block, {
        "segments": [{
            "start": 0.0,
            "end": 4.0,
            "text": "第二块开始",
            "words": [
                {"start": 0.0, "end": 1.0, "word": "第二"},
                {"start": 1.0, "end": 2.0, "word": "块"},
            ],
        }],
    }, previous_end_seconds=1141.0)

    assert [(segment.start_seconds, segment.end_seconds) for segment in transcript.segments] == [(1141.0, 1144.0)]
    assert [(word.start_seconds, word.end_seconds, word.text) for word in transcript.words] == [
        (1141.0, 1142.0, "块"),
    ]

    third = normalize_block_transcript(TranscriptionBlock(index=3, start_seconds=2280.0, end_seconds=3480.0), {
        "segments": [{"start": 0, "end": 2, "text": "第三块", "words": [{"start": 1, "end": 2, "word": "三"}]}],
    }, previous_end_seconds=2281.0)
    assert [(word.start_seconds, word.end_seconds) for word in third.words] == [(2281.0, 2282.0)]


def test_exactly_thirty_minutes_stays_one_block_but_longer_video_has_sixty_second_overlap():
    assert [(item.start_seconds, item.end_seconds) for item in build_transcription_blocks(1800.0)] == [(0.0, 1800.0)]
    assert [(item.start_seconds, item.end_seconds) for item in build_transcription_blocks(1800.1)] == [
        (0.0, 1200.0), (1140.0, 1800.1),
    ]


def test_transcription_persists_frozen_identity_progress_and_absolute_word_evidence(tmp_path):
    def runner(_source, block, _cancelled, _work_dir, _settings):
        if block.index == 1:
            return {"segments": [{"start": 1139, "end": 1141, "text": "块一", "words": [{"start": 1139, "end": 1141, "word": "块一"}]}]}
        return {"segments": [{"start": 0, "end": 2, "text": "块二", "words": [{"start": 0, "end": 1, "word": "重复"}, {"start": 1, "end": 2, "word": "块二"}]}]}

    queue = ManualQueue()
    client = client_for(tmp_path, queue, runner)
    started = client.post(f"/api/projects/{PROJECT_ID}/transcriptions", json={"assetId": "video-a", "language": "zh", "model": "small"}, headers=INTENT)

    assert started.status_code == 202, started.text
    assert started.json()["assetVersionId"] == "video-a-frozen-v1"
    assert started.json()["blockCount"] == 2
    queue.run()
    result = client.get(f"/api/projects/{PROJECT_ID}/transcriptions").json()["transcriptions"][0]

    assert result["status"] == "completed"
    assert result["progress"] == 1
    assert result["completedBlocks"] == 2
    assert [(word["startSeconds"], word["endSeconds"], word["text"]) for word in result["words"]] == [
        (1139.0, 1141.0, "块一"), (1141.0, 1142.0, "块二"),
    ]
    assert result["cacheKey"]
    assert result["runs"][-1]["idempotencyKey"] == result["cacheKey"]


def test_no_speech_is_a_deterministic_failure_and_retry_keeps_prior_run_history(tmp_path):
    queue = ManualQueue()
    client = client_for(tmp_path, queue, lambda *_args: {"segments": []}, duration=12)
    started = client.post(f"/api/projects/{PROJECT_ID}/transcriptions", json={"assetId": "video-a"}, headers=INTENT).json()
    queue.run()
    failed = client.get(f"/api/projects/{PROJECT_ID}/transcriptions").json()["transcriptions"][0]

    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "no_speech"
    assert failed["words"] == []
    retried = client.post(f"/api/projects/{PROJECT_ID}/transcriptions/assets/video-a/retry", headers=INTENT).json()

    assert retried["runId"] != started["runId"]
    assert retried["previousRunId"] == started["runId"]
    assert [(item["id"], item["status"]) for item in retried["runs"]] == [
        (started["runId"], "failed"), (retried["runId"], "queued"),
    ]
    queue.run()
    assert client.get(f"/api/projects/{PROJECT_ID}/transcriptions").json()["transcriptions"][0]["status"] == "failed"


def test_input_changes_keep_prior_runs_even_when_checkpoints_cannot_be_reused(tmp_path):
    queue = ManualQueue()
    client = client_for(tmp_path, queue, lambda *_args: {"segments": [{"start": 0, "end": 1, "text": "语音", "words": [{"start": 0, "end": 1, "word": "语音"}]}]}, duration=12)
    first = client.post(f"/api/projects/{PROJECT_ID}/transcriptions", json={"assetId": "video-a", "model": "small"}, headers=INTENT).json()
    queue.run()
    second = client.post(f"/api/projects/{PROJECT_ID}/transcriptions", json={"assetId": "video-a", "model": "medium"}, headers=INTENT).json()
    queue.run()
    third = client.post(f"/api/projects/{PROJECT_ID}/transcriptions", json={"assetId": "video-a", "model": "medium", "language": "en"}, headers=INTENT).json()
    queue.run()
    PreproductionStore(tmp_path).path(PROJECT_ID, "assets", "video-a.mp4").write_bytes(b"changed-source")
    fourth = client.post(f"/api/projects/{PROJECT_ID}/transcriptions", json={"assetId": "video-a", "model": "medium", "language": "en"}, headers=INTENT).json()

    assert [item["id"] for item in fourth["runs"]] == [first["runId"], second["runId"], third["runId"], fourth["runId"]]
    assert fourth["previousRunId"] == third["runId"]
    assert fourth["completedBlocks"] == 0


def test_model_loading_failure_is_reported_as_model_unavailable(tmp_path):
    queue = ManualQueue()
    def unavailable(*_args):
        raise TranscriptionModelUnavailable("模型权重不可读")
    client = client_for(tmp_path, queue, unavailable, duration=12)
    client.post(f"/api/projects/{PROJECT_ID}/transcriptions", json={"assetId": "video-a"}, headers=INTENT)
    queue.run()

    result = client.get(f"/api/projects/{PROJECT_ID}/transcriptions").json()["transcriptions"][0]
    assert result["status"] == "failed"
    assert result["error"] == {"code": "model_unavailable", "message": "模型权重不可读", "retryable": True}


def test_cancelled_or_damaged_audio_never_becomes_completed_empty_transcript(tmp_path):
    queue = ManualQueue()
    client = client_for(tmp_path, queue, lambda *_args: (_ for _ in ()).throw(TranscriptionError("音轨损坏")), duration=12)
    client.post(f"/api/projects/{PROJECT_ID}/transcriptions", json={"assetId": "video-a"}, headers=INTENT)
    cancelled = client.post(f"/api/projects/{PROJECT_ID}/transcriptions/assets/video-a/cancel", headers=INTENT).json()
    queue.run()
    assert cancelled["status"] == "cancelled"
    assert client.get(f"/api/projects/{PROJECT_ID}/transcriptions").json()["transcriptions"][0]["status"] == "cancelled"

    retried = client.post(f"/api/projects/{PROJECT_ID}/transcriptions/assets/video-a/retry", headers=INTENT).json()
    queue.run()
    damaged = client.get(f"/api/projects/{PROJECT_ID}/transcriptions").json()["transcriptions"][0]
    assert retried["runId"] != cancelled["runId"]
    assert damaged["status"] == "failed"
    assert damaged["error"]["code"] == "transcription_failed"


def test_restart_requeues_the_same_queued_run(tmp_path):
    queue = ManualQueue()
    client = client_for(tmp_path, queue, lambda *_args: {"segments": [{"start": 0, "end": 1, "text": "证据", "words": [{"start": 0, "end": 1, "word": "证据"}]}]}, duration=1800.1)
    started = client.post(f"/api/projects/{PROJECT_ID}/transcriptions", json={"assetId": "video-a"}, headers=INTENT).json()

    resumed_queue = ManualQueue()
    app = FastAPI()
    app.include_router(create_transcription_router(
        tmp_path, lambda project_id: {"id": project_id} if project_id == PROJECT_ID else None, resumed_queue,
        runner=lambda *_args: {"segments": [{"start": 0, "end": 1, "text": "证据", "words": [{"start": 0, "end": 1, "word": "证据"}]}]},
        duration_probe=lambda _source: 1800.1, model_identity_resolver=lambda _settings: "e2-fixture-model-sha256",
    ))
    resumed = TestClient(app).get(f"/api/projects/{PROJECT_ID}/transcriptions").json()["transcriptions"][0]

    assert resumed["runId"] == started["runId"]
    assert resumed["status"] == "queued"
    assert len(resumed_queue.jobs) == 1
