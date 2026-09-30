"""User-visible batch editing paths through the project API."""
import json
import os
import shutil
import subprocess
import threading
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.batch_editing_api import _shared_montage_usage, create_batch_editing_router
from app.batch_recommendation import recommend
from app.main import create_app
from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint


INTENT = {"x-aivre-intent": "semantic-analysis"}


def test_content_api_persists_explicit_cta_with_omit_null_and_cas(tmp_path):
    class Queue:
        def submit(self, *_args):
            return True

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _project_id: object(), Queue()))
    with TestClient(app) as client:
        base = "/api/projects/p1/batch-edits"
        task = client.post(base, json={"sellingPoint": "卖点", "script": "脚本"}).json()["task"]
        content = f"{base}/{task['id']}/content"

        saved = client.put(content, json={"revision": 0, "sellingPoint": "卖点", "script": "脚本", "cta": "立即咨询"})
        assert saved.status_code == 200
        assert saved.json()["task"]["cta"] == "立即咨询"
        assert saved.json()["task"]["contentRevision"] == 1
        retained = client.put(content, json={"revision": 1, "sellingPoint": "新版卖点", "script": "脚本"})
        assert retained.status_code == 200
        assert retained.json()["task"]["cta"] == "立即咨询"
        assert client.put(content, json={"revision": 1, "sellingPoint": "冲突", "script": "脚本"}).status_code == 409
        cleared = client.put(content, json={"revision": 2, "sellingPoint": "新版卖点", "script": "脚本", "cta": None})
        assert cleared.status_code == 200
        assert "cta" not in cleared.json()["task"]


def test_get_keeps_an_ambiguous_legacy_recognition_readable_for_repair(tmp_path):
    class Queue:
        def submit(self, *_args):
            return True

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _project_id: object(), Queue()))
    with TestClient(app) as client:
        base = "/api/projects/p1/batch-edits"
        task = client.post(base, json={"sellingPoint": "卖点", "script": "脚本"}).json()["task"]
        state_path = tmp_path / "project-files" / "p1" / "batch-edits" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        stored = state["tasks"][0]
        tracks = stored["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "clip", "assetId": "shot", "start": 0, "inPoint": 0, "duration": 2,
                                "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        cue = {"id": "line-1", "start": 0, "end": 1, "text": "旧字幕"}
        recognitions = [{"id": run_id, "status": "completed", "error": None, "language": "zh",
                         "timelineRevision": 0, "subtitleRevision": 0, "cues": [deepcopy(cue)],
                         "snapshot": {"settings": stored["variant"]["settings"], "tracks": tracks},
                         "sources": {"shot": "shot.mp4"}}
                        for run_id in ("recognition-one", "recognition-two")]
        stored["variant"]["subtitles"].update(cues=[cue], recognitions=recognitions)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        response = client.get(base)

        assert response.status_code == 200
        assert response.json()["tasks"][0]["variant"]["subtitles"]["migrationRequired"]["code"] == "legacy_cue_ambiguity"


def test_shared_montage_usage_counts_each_sibling_once_and_ignores_deleted_or_stale_selections():
    sibling = {
        "id": "task-b",
        "variant": {"tracks": [{"clips": [{
            "id": "clip-a", "assetId": "asset-a", "inPoint": 1, "duration": 2,
        }]}]},
        "montageProposals": [
            {
                "status": "applied",
                "appliedSelections": [
                    {"segmentId": "segment-a", "candidateId": "candidate-a", "clipId": "clip-a",
                     "assetId": "asset-a", "inPoint": 1, "duration": 2},
                    {"segmentId": "segment-b", "candidateId": "candidate-b", "clipId": "clip-b",
                     "assetId": "asset-b", "inPoint": 1, "duration": 2},
                ],
            },
            {
                "status": "draft",
                "segments": [{"selectedCandidateId": "candidate-a"}],
            },
        ],
    }

    legacy_sibling = {
        "id": "task-c",
        "contentRevision": 1,
        "variant": {"tracks": [{"clips": [{"id": "legacy-a"}]}]},
        "montageProposals": [
            {
                "status": "applied",
                "segments": [
                    {"selectedCandidateId": "candidate-c"},
                    {"selectedCandidateId": "candidate-d"},
                ],
                "appliedClipIds": ["legacy-a", "legacy-deleted"],
            },
            {
                "status": "draft",
                "inputSnapshot": {"contentRevision": 0},
                "segments": [{"selectedCandidateId": "stale-candidate"}],
            },
        ],
    }
    replaced_sibling = {
        "id": "task-d",
        "variant": {"tracks": [{"clips": [{
            "id": "clip-x", "assetId": "replacement", "inPoint": 0, "duration": 2,
        }]}]},
        "montageProposals": [{
            "status": "applied",
            "appliedSelections": [{
                "segmentId": "segment-x", "candidateId": "candidate-x", "clipId": "clip-x",
                "assetId": "original", "inPoint": 1, "duration": 2,
            }],
        }],
    }

    assert _shared_montage_usage([{"id": "task-a"}, sibling, legacy_sibling, replaced_sibling], "task-a") == {
        "candidate-a": 1, "candidate-c": 1,
    }


class MontageVectorizer:
    version = "montage-test-v1"
    evidence_basis = "测试关键帧视觉证据"

    def embed_text(self, query):
        return [1.0, 0.0] if "杯盖" in query else [0.0, 1.0]

    def embed_image(self, path):
        with Image.open(path) as image:
            red, _green, blue = image.convert("RGB").resize((1, 1)).getpixel((0, 0))
        return [1.0, 0.0] if red > blue else [0.0, 1.0]


def install_montage_indexes(root, project_id):
    from app.source_segment_index import KEYFRAME_MODEL_VERSION, SEGMENT_ALGORITHM_VERSION, source_fingerprint

    preproduction = root / "project-files" / project_id / "preproduction"
    (preproduction / "assets").mkdir(parents=True)
    assets = []
    for asset_id, name, color in (("lid", "杯盖防泼溅", "red"), ("commute", "通勤携带", "blue")):
        filename = f"{asset_id}.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color={color}:size=64x48:rate=24:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(preproduction / "assets" / filename),
        ], check=True, capture_output=True, text=True)
        assets.append({"id": asset_id, "name": name, "kind": "video", "file": filename, "duration": 6})
    (preproduction / "state.json").write_text(json.dumps({
        "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [], "assets": assets,
    }), encoding="utf-8")
    for asset, color in zip(assets, ((220, 20, 20), (20, 20, 220))):
        source = preproduction / "assets" / asset["file"]
        run_id = f"run-{asset['id']}"
        directory = root / "project-files" / project_id / "source-segments" / asset["id"]
        keyframe = directory / "runs" / run_id / "keyframe-1.jpg"
        keyframe.parent.mkdir(parents=True)
        Image.new("RGB", (160, 90), color).save(keyframe)
        fingerprint = source_fingerprint(source)
        candidate_id = f"segment-{asset['id']}"
        (directory / "state.json").write_text(json.dumps({
            "schemaVersion": 1, "assetId": asset["id"], "runId": run_id, "attemptId": f"attempt-{asset['id']}",
            "status": "completed", "sourceFingerprint": fingerprint,
            "algorithmVersion": SEGMENT_ALGORITHM_VERSION, "keyframeModelVersion": KEYFRAME_MODEL_VERSION,
            "queuedAt": "2026-09-30T00:00:00+00:00", "startedAt": "2026-09-30T00:00:01+00:00",
            "completedAt": "2026-09-30T00:00:02+00:00", "updatedAt": "2026-09-30T00:00:02+00:00",
            "error": None, "validity": {"status": "current", "reason": None}, "durationSeconds": 6,
            "candidates": [{"id": candidate_id, "assetId": asset["id"], "sourceFingerprint": fingerprint,
                "algorithmVersion": SEGMENT_ALGORITHM_VERSION, "keyframeModelVersion": KEYFRAME_MODEL_VERSION,
                "inPointSeconds": 1.0, "outPointSeconds": 5.0,
                "keyframe": {"timeSeconds": 3.0, "_filename": "keyframe-1.jpg"}}],
        }), encoding="utf-8")


class DeferredComputeQueue:
    """Keep proposal analysis jobs controllable so adoption races are reproducible."""

    def __init__(self):
        self.jobs = []

    def submit(self, kind, project_id, handler):
        self.jobs.append((kind, project_id, handler))
        return True

    def is_active(self, _kind, _project_id):
        return False

    def is_project_active(self, _project_id):
        return False

    def shutdown(self):
        pass


class _ThrowingComputeQueue(DeferredComputeQueue):
    """Simulate a queue failure after export state was durably written."""

    def submit(self, _kind, _project_id, _handler):
        raise RuntimeError("队列提交失败")


@contextmanager
def _reframe_delivery_client(tmp_path, *, source_lock=None, queue=None):
    """One approved reframed delivery, ready for focused delivery-gate races."""
    from copy import deepcopy
    from app.montage_reframe import normalize_reframe, probe_source_timing

    project_id = "reframe-delivery"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:size=64x48:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True, text=True)
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "source", "name": "source", "kind": "video", "file": "source.mp4", "duration": 2}]}), encoding="utf-8")
    timing = probe_source_timing(source, "ffprobe")
    raw = {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 64, "sourceHeight": 48,
           "targetAspect": {"width": 720, "height": 1280}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
           "samples": [{"timeSeconds": point, "boxes": [{"x": .4, "y": .1, "width": .2, "height": .7, "label": "person", "confidence": .9}], "center": {"x": .5, "y": .5}, "confidence": .9}
                       for point in timing.elapsed], "uncertaintyReasons": [], "status": "ready",
           "override": {"mode": "auto", "keyframes": []}}
    ready = normalize_reframe(raw, source_fingerprint=source_fingerprint(source), source_width=64, source_height=48,
                              source_timing=timing, required_start=0, required_end=2)
    app = FastAPI()
    queue = queue or DeferredComputeQueue()
    app.include_router(create_batch_editing_router(tmp_path, lambda value: SimpleNamespace(id=value) if value == project_id else None,
                                                  queue, source_lock=source_lock))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "x", "script": "x"}).json()["task"]
        state_path = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        stored = state["tasks"][0]
        clip = {"id": "clip", "assetId": "source", "start": 0, "inPoint": 0, "duration": 2,
                "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0, "reframe": ready}
        stored["variant"].update(revision=1, tracks=[{**stored["variant"]["tracks"][0], "clips": [clip]}, stored["variant"]["tracks"][1]])
        snapshot = {"settings": deepcopy(stored["variant"]["settings"]), "tracks": deepcopy(stored["variant"]["tracks"])}
        preview = {"id": "preview", "revision": 1, "contentRevision": 0, "subtitleRevision": 0, "subtitleCues": [],
                   "status": "completed", "error": None, "output": "preview.mp4", "snapshot": deepcopy(snapshot), "sources": {"source": "source.mp4"}}
        review = {"id": "review", "runId": "preview", "decision": "approved", "reason": "确认",
                  "contentRevision": 0, "variantRevision": 1, "subtitleRevision": 0}
        delivery = {"id": "delivery", "status": "completed", "error": None, "output": "delivery.mp4",
                    "contentRevision": 0, "variantRevision": 1, "subtitleRevision": 0, "previewRunId": "preview",
                    "review": deepcopy(review), "sellingPoint": "x", "script": "x", "snapshot": deepcopy(snapshot),
                    "subtitles": [], "assets": [{"id": "source", "name": "source", "kind": "video"}], "sources": {"source": "source.mp4"}}
        stored["variant"]["runs"] = [preview]
        stored["reviews"] = [review]
        stored["variant"]["exports"] = [delivery]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        for directory in ("runs/preview", "exports/delivery"):
            destination = state_path.parent / directory / "sources"
            destination.mkdir(parents=True)
            shutil.copyfile(source, destination / "source.mp4")
        (state_path.parent / "outputs").mkdir()
        (state_path.parent / "outputs" / "preview.mp4").write_bytes(b"preview")
        (state_path.parent / "deliverables").mkdir()
        (state_path.parent / "deliverables" / "delivery.mp4").write_bytes(b"delivery")
        yield SimpleNamespace(client=client, base=base, task=task, source=source, state_path=state_path, snapshot=snapshot,
                              queue=queue)


def _add_export_child(fixture, child_id, *, reframed=True):
    from copy import deepcopy
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    child = deepcopy(state["tasks"][0])
    child.update(id=child_id, batchId=fixture.task["id"], generationId="generation",
                 generation={"status": "ready", "error": None})
    child["variant"]["exports"] = []
    if not reframed:
        for track in child["variant"]["tracks"]:
            for clip in track["clips"]:
                clip.pop("reframe", None)
        for run in child["variant"]["runs"]:
            for track in run["snapshot"]["tracks"]:
                for clip in track["clips"]:
                    clip.pop("reframe", None)
    state["tasks"].append(child)
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    return child


def test_batch_export_keeps_non_reframed_sibling_sources_available_for_reframe_safety(tmp_path):
    """Reframe safety must preflight the whole approved snapshot, not just its reframe clip."""
    with _reframe_delivery_client(tmp_path) as fixture:
        _add_export_child(fixture, "child")
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        child = next(item for item in state["tasks"] if item["id"] == "child")
        preproduction_path = fixture.source.parent.parent / "state.json"
        preproduction = json.loads(preproduction_path.read_text(encoding="utf-8"))
        sibling = fixture.source.parent / "sibling.mp4"
        shutil.copyfile(fixture.source, sibling)
        preproduction["assets"].append({"id": "sibling", "name": "sibling", "kind": "video", "file": "sibling.mp4", "duration": 2})
        preproduction_path.write_text(json.dumps(preproduction), encoding="utf-8")
        plain_clip = {"id": "plain", "assetId": "sibling", "start": 0, "inPoint": 0, "duration": 2,
                      "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0}
        child["variant"]["tracks"][0]["clips"][0]["start"] = 2
        child["variant"]["tracks"][0]["clips"].insert(0, plain_clip)
        preview = child["variant"]["runs"][0]
        preview["snapshot"]["tracks"][0]["clips"][0]["start"] = 2
        preview["snapshot"]["tracks"][0]["clips"].insert(0, dict(plain_clip))
        preview["sources"]["sibling"] = "sibling.mp4"
        frozen_sibling = fixture.state_path.parent / "runs" / "preview" / "sources" / "sibling.mp4"
        shutil.copyfile(sibling, frozen_sibling)
        fixture.state_path.write_text(json.dumps(state), encoding="utf-8")

        response = fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["child"]})

        assert response.status_code == 202, response.text


class _HookSourceLock:
    def __init__(self):
        self.entries = 0
        self.hook = None

    def __enter__(self):
        self.entries += 1
        if self.hook is not None:
            self.hook(self.entries)
        return self

    def __exit__(self, *_args):
        return False


def test_submit_cleans_every_prepared_source_directory_when_later_copy_fails(tmp_path, monkeypatch):
    from app.render_inputs import RenderInputError, RenderInputPreparation

    with _reframe_delivery_client(tmp_path) as fixture:
        _add_export_child(fixture, "first", reframed=False)
        _add_export_child(fixture, "second", reframed=False)
        exports = fixture.state_path.parent / "exports"
        before = {item.name for item in exports.iterdir()}
        original = RenderInputPreparation.freeze_approved
        calls = 0

        def fail_after_copy(self, *args, **kwargs):
            nonlocal calls
            result = original(self, *args, **kwargs)
            calls += 1
            if calls == 2:
                raise RenderInputError("第二个预复制失败")
            return result

        monkeypatch.setattr(RenderInputPreparation, "freeze_approved", fail_after_copy)
        response = fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["first", "second"]})

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "batch_asset_unavailable"
        assert {item.name for item in exports.iterdir()} == before


def test_submit_terminal_source_cas_conflict_returns_409_without_failed_run_or_orphan(tmp_path):
    lock = _HookSourceLock()
    with _reframe_delivery_client(tmp_path, source_lock=lock) as fixture:
        _add_export_child(fixture, "child", reframed=False)
        exports = fixture.state_path.parent / "exports"
        before = {item.name for item in exports.iterdir()}
        lock.entries = 0

        def delete_after_precopy(entry):
            if entry == 2:
                fixture.source.unlink()

        lock.hook = delete_after_precopy
        response = fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["child"]})

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "batch_export_reframe_conflict"
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        child = next(item for item in state["tasks"] if item["id"] == "child")
        assert child["variant"]["exports"] == []
        assert {item.name for item in exports.iterdir()} == before


def test_submit_cleans_a_copy_when_its_source_changes_after_copying(tmp_path, monkeypatch):
    from app.render_inputs import RenderInputPreparation

    with _reframe_delivery_client(tmp_path) as fixture:
        _add_export_child(fixture, "child", reframed=False)
        exports = fixture.state_path.parent / "exports"
        before = {item.name for item in exports.iterdir()}
        original = RenderInputPreparation.freeze_approved

        def copy_then_replace(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            replacement = fixture.source.with_name("copy-race-replacement.mp4")
            replacement.write_bytes(fixture.source.read_bytes())
            os.replace(replacement, fixture.source)
            return result

        monkeypatch.setattr(RenderInputPreparation, "freeze_approved", copy_then_replace)
        response = fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["child"]})

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "batch_export_reframe_conflict"
        assert {item.name for item in exports.iterdir()} == before


def test_submit_keeps_the_prepared_directory_after_a_successful_commit(tmp_path):
    with _reframe_delivery_client(tmp_path) as fixture:
        _add_export_child(fixture, "child", reframed=False)

        response = fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["child"]})

        assert response.status_code == 202
        run_id = response.json()["results"][0]["runId"]
        prepared = fixture.state_path.parent / "exports" / run_id
        assert (prepared / "sources" / "source.mp4").is_file()
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        child = next(item for item in state["tasks"] if item["id"] == "child")
        assert child["variant"]["exports"][0]["id"] == run_id


def test_submit_keeps_every_durably_referenced_variant_source_when_queue_submit_raises(tmp_path):
    queue = _ThrowingComputeQueue()
    with _reframe_delivery_client(tmp_path, queue=queue) as fixture:
        _add_export_child(fixture, "first", reframed=False)
        _add_export_child(fixture, "second", reframed=False)

        with pytest.raises(RuntimeError, match="队列提交失败"):
            fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["first", "second"]})

        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        children = [next(task for task in state["tasks"] if task["id"] == child_id) for child_id in ("first", "second")]
        run_ids = {child["variant"]["exports"][0]["id"] for child in children}
        assert all((fixture.state_path.parent / "exports" / run_id / "sources" / "source.mp4").is_file()
                   for run_id in run_ids)
        assert {item.name for item in (fixture.state_path.parent / "exports").iterdir()} == {"delivery", *run_ids}


def test_download_probe_does_not_block_get_or_cancelling_another_export(tmp_path, monkeypatch):
    from app import montage_reframe

    with _reframe_delivery_client(tmp_path) as fixture:
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        active = deepcopy(state["tasks"][0]["variant"]["exports"][0])
        active.update(id="active", status="queued", error=None)
        state["tasks"][0]["variant"]["exports"].append(active)
        fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        original = montage_reframe.probe_source_timing

        def blocked_probe(path, probe):
            entered.set()
            assert release.wait(5), "测试未释放下载探测"
            return original(path, probe)

        monkeypatch.setattr(montage_reframe, "probe_source_timing", blocked_probe)
        result = {}
        downloading = threading.Thread(target=lambda: result.setdefault(
            "response", fixture.client.get(f"{fixture.base}/{fixture.task['id']}/exports/delivery/output")
        ))
        downloading.start()
        assert entered.wait(2)
        reader = threading.Thread(target=lambda: (fixture.client.get(fixture.base), read_done.set()))
        reader.start()
        try:
            assert read_done.wait(.5)
            assert fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports/active/cancel").status_code == 200
        finally:
            release.set()
        downloading.join(5)
        reader.join(5)
        assert result["response"].content == b"delivery"


def test_packaging_proof_hash_is_outside_lock_and_consumed_once_per_download(tmp_path, monkeypatch):
    """A completed delivery must not hold the project lock while hashing/compiling its font proof."""
    from app import montage_packaging
    from app.montage_packaging import default_packaging

    with _reframe_delivery_client(tmp_path) as fixture:
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        task = state["tasks"][0]
        package = default_packaging()
        task["variant"]["packaging"] = deepcopy(package)
        for record in [task["variant"], *task["variant"]["runs"], *task["variant"]["exports"]]:
            target = record["snapshot"] if "snapshot" in record else record
            target["packaging"] = deepcopy(package)
            for track in target["tracks"]:
                for clip in track["clips"]:
                    clip.pop("reframe", None)
        active = deepcopy(task["variant"]["exports"][0])
        active.update(id="active", status="queued", error=None)
        task["variant"]["exports"].append(active)
        fixture.state_path.write_text(json.dumps(state), encoding="utf-8")

        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        original = montage_packaging._hash_font
        calls = []
        def blocked_hash(path):
            calls.append(path)
            entered.set()
            assert release.wait(5), "测试未释放字体 proof"
            return original(path)
        monkeypatch.setattr(montage_packaging, "_hash_font", blocked_hash)
        result = {}
        downloading = threading.Thread(target=lambda: result.setdefault(
            "response", fixture.client.get(f"{fixture.base}/{fixture.task['id']}/exports/delivery/output")
        ))
        downloading.start()
        assert entered.wait(2)
        reader = threading.Thread(target=lambda: (fixture.client.get(fixture.base), read_done.set()))
        reader.start()
        try:
            assert read_done.wait(.5), "字体 hash/event proof 期间普通读取不应等待项目锁"
            assert fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports/active/cancel").status_code == 200
        finally:
            release.set()
        downloading.join(5)
        reader.join(5)
        assert result["response"].content == b"delivery"
        assert len(calls) == 1, "第二锁窗口只能消费 proof，不能重复读取字体"


def test_submit_reuses_one_packaging_proof_for_current_preview_and_delivery(tmp_path, monkeypatch):
    """The two-stage callback may consume both records, but must prepare identical input once."""
    from app import montage_packaging
    from app.montage_packaging import default_packaging

    with _reframe_delivery_client(tmp_path) as fixture:
        child = _add_export_child(fixture, "child", reframed=False)
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        stored = next(task for task in state["tasks"] if task["id"] == child["id"])
        package = default_packaging()
        stored["variant"]["packaging"] = deepcopy(package)
        for record in [*stored["variant"]["runs"], *stored["variant"]["exports"]]:
            record["snapshot"]["packaging"] = deepcopy(package)
        fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
        original, calls = montage_packaging.prepare_packaging_proof, []
        def counted(snapshot, cues, *, batch):
            calls.append((snapshot, cues, batch))
            return original(snapshot, cues, batch=batch)
        monkeypatch.setattr(montage_packaging, "prepare_packaging_proof", counted)

        response = fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["child"]})

        assert response.status_code == 202, response.text
        assert len(calls) == 1


def test_download_rejects_a_run_identity_changed_during_its_probe(tmp_path, monkeypatch):
    from app import montage_reframe

    with _reframe_delivery_client(tmp_path) as fixture:
        entered, release = threading.Event(), threading.Event()
        original = montage_reframe.probe_source_timing
        monkeypatch.setattr(montage_reframe, "probe_source_timing", lambda path, probe:
                            entered.set() or (release.wait(5) and original(path, probe)))
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault(
            "response", fixture.client.get(f"{fixture.base}/{fixture.task['id']}/exports/delivery/output")
        ))
        thread.start()
        assert entered.wait(2)
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        state["tasks"][0]["variant"]["exports"][0]["review"]["reason"] = "证明期间修改"
        fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
        release.set()
        thread.join(5)

        assert result["response"].status_code == 409
        assert result["response"].json()["detail"]["code"] == "batch_export_reframe_conflict"


def test_submit_rejects_a_preview_identity_changed_during_its_probe(tmp_path, monkeypatch):
    from app import montage_reframe

    with _reframe_delivery_client(tmp_path) as fixture:
        _add_export_child(fixture, "child")
        entered, release = threading.Event(), threading.Event()
        original = montage_reframe.probe_source_timing

        def blocked_probe(path, probe):
            entered.set()
            assert release.wait(5), "测试未释放提交探测"
            return original(path, probe)

        monkeypatch.setattr(montage_reframe, "probe_source_timing", blocked_probe)
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault(
            "response", fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["child"]})
        ))
        thread.start()
        assert entered.wait(2)
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        preview = next(item for item in next(task for task in state["tasks"] if task["id"] == "child")["variant"]["runs"]
                       if item["id"] == "preview")
        preview["snapshot"]["tracks"][0]["clips"][0]["start"] = .1
        fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
        release.set()
        thread.join(5)

        assert result["response"].status_code == 409
        assert result["response"].json()["detail"]["code"] == "batch_export_reframe_conflict"


def test_submit_probe_does_not_block_get_or_cancelling_another_export(tmp_path, monkeypatch):
    from app import montage_reframe

    with _reframe_delivery_client(tmp_path) as fixture:
        _add_export_child(fixture, "child")
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        active = deepcopy(state["tasks"][0]["variant"]["exports"][0])
        active.update(id="active", status="queued", error=None)
        state["tasks"][0]["variant"]["exports"].append(active)
        fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        original = montage_reframe.probe_source_timing

        def blocked_probe(path, probe):
            entered.set()
            assert release.wait(5), "测试未释放提交探测"
            return original(path, probe)

        monkeypatch.setattr(montage_reframe, "probe_source_timing", blocked_probe)
        result = {}
        submitting = threading.Thread(target=lambda: result.setdefault(
            "response", fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["child"]})
        ))
        submitting.start()
        assert entered.wait(2)
        reader = threading.Thread(target=lambda: (fixture.client.get(fixture.base), read_done.set()))
        reader.start()
        try:
            assert read_done.wait(.5)
            assert fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports/active/cancel").status_code == 200
        finally:
            release.set()
        submitting.join(5)
        reader.join(5)
        assert result["response"].status_code == 202


def test_download_treats_same_hash_source_replacement_during_probe_as_conflict(tmp_path, monkeypatch):
    from app import montage_reframe

    with _reframe_delivery_client(tmp_path) as fixture:
        entered, release = threading.Event(), threading.Event()
        original = montage_reframe.probe_source_timing

        def blocked_probe(path, probe):
            entered.set()
            assert release.wait(5), "测试未释放源竞态探测"
            return original(path, probe)

        monkeypatch.setattr(montage_reframe, "probe_source_timing", blocked_probe)
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault(
            "response", fixture.client.get(f"{fixture.base}/{fixture.task['id']}/exports/delivery/output")
        ))
        thread.start()
        assert entered.wait(2)
        replacement = fixture.source.with_name("same-hash-replacement.mp4")
        replacement.write_bytes(fixture.source.read_bytes())
        os.replace(replacement, fixture.source)
        release.set()
        thread.join(5)

        assert result["response"].status_code == 409
        assert result["response"].json()["detail"]["code"] == "batch_export_reframe_conflict"


def test_download_treats_source_deleted_after_hash_as_conflict(tmp_path, monkeypatch):
    from app import montage_reframe

    with _reframe_delivery_client(tmp_path) as fixture:
        original = montage_reframe.source_fingerprint

        def hash_then_delete(path):
            value = original(path)
            if path == fixture.source:
                fixture.source.unlink()
            return value

        monkeypatch.setattr(montage_reframe, "source_fingerprint", hash_then_delete)
        response = fixture.client.get(f"{fixture.base}/{fixture.task['id']}/exports/delivery/output")

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "batch_export_reframe_conflict"


def test_submit_treats_source_deleted_during_probe_as_conflict_without_orphan(tmp_path, monkeypatch):
    from app import montage_reframe

    with _reframe_delivery_client(tmp_path) as fixture:
        _add_export_child(fixture, "child")
        before = {item.name for item in (fixture.state_path.parent / "exports").iterdir()}
        entered, release = threading.Event(), threading.Event()
        original = montage_reframe.probe_source_timing

        def blocked_probe(path, probe):
            entered.set()
            assert release.wait(5), "测试未释放删除源探测"
            return original(path, probe)

        monkeypatch.setattr(montage_reframe, "probe_source_timing", blocked_probe)
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault(
            "response", fixture.client.post(f"{fixture.base}/{fixture.task['id']}/exports", json={"taskIds": ["child"]})
        ))
        thread.start()
        assert entered.wait(2)
        fixture.source.unlink()
        release.set()
        thread.join(5)

        assert result["response"].status_code == 409
        assert result["response"].json()["detail"]["code"] == "batch_export_reframe_conflict"
        assert {item.name for item in (fixture.state_path.parent / "exports").iterdir()} == before
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        child = next(item for item in state["tasks"] if item["id"] == "child")
        assert child["variant"]["exports"] == []


def _proposal_reframe_app(root):
    queue = DeferredComputeQueue()

    def analyzer(path, start, end, aspect):
        return {
            "schemaVersion": 1, "sourceFingerprint": source_fingerprint(path),
            "sourceWidth": 64, "sourceHeight": 48, "targetAspect": aspect,
            "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
            "samples": [
                {"timeSeconds": start, "boxes": [], "center": {"x": .35, "y": .5}, "confidence": .9},
                {"timeSeconds": end, "boxes": [], "center": {"x": .65, "y": .5}, "confidence": .9},
            ],
            "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []},
        }

    return create_app(root, segment_visual_vectorizer=MontageVectorizer(),
                      local_compute_queue=queue, reframe_analyzer=analyzer), queue


def _create_montage_proposal(client, root, *, script="展示杯盖防泼溅。", narration=None):
    project_id = client.post("/api/projects", json={"name": "主体混剪回归"}).json()["id"]
    install_montage_indexes(root, project_id)
    queries = ["展示杯盖防泼溅"] if script.count("。") <= 1 else ["展示杯盖防泼溅", "展示通勤携带效果"]
    searches = [client.post(f"/api/projects/{project_id}/segment-searches", json={"query": query, "limit": 2},
                            headers=INTENT).json() for query in queries]
    base = f"/api/projects/{project_id}/batch-edits"
    task = client.post(base, json={"sellingPoint": "防泼溅", "script": script}, headers=INTENT).json()["task"]
    narration = narration or [{"durationSeconds": 2, "wordBoundariesSeconds": []} for _ in searches]
    proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
        "revision": 0, "searchIds": [item["id"] for item in searches], "narration": narration, "beatTimes": [],
    }, headers=INTENT).json()["proposal"]
    return project_id, base, task, proposal


def test_team_can_create_a_batch_task_and_reopen_its_script(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "客户短视频"}).json()["id"]
        base = f"/api/projects/{project_id}/batch-edits"

        created = client.post(base, json={"sellingPoint": "省时", "script": "先展示操作，再展示结果"}, headers=INTENT)

        assert created.status_code == 201, created.text
        task = created.json()["task"]
        assert task["sellingPoint"] == "省时"
        assert task["script"] == "先展示操作，再展示结果"
        assert task["variant"]["revision"] == 0
        reopened = client.get(base).json()["tasks"]
        assert [(item["id"], item["script"]) for item in reopened] == [(task["id"], "先展示操作，再展示结果")]
        other_id = client.post("/api/projects", json={"name": "另一客户"}).json()["id"]
        other_workspace = client.get(f"/api/projects/{other_id}/batch-edits").json()
        assert other_workspace["tasks"] == [] and other_workspace["assets"] == []
        assert client.put(f"/api/projects/{other_id}/batch-edits/{task['id']}/variant",
                          json={"revision": 0, "tracks": task["variant"]["tracks"]}, headers=INTENT).status_code == 404


def test_variant_edits_keep_other_tasks_and_project_timeline_unchanged(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "客户短视频"}).json()["id"]
        asset_root = tmp_path / "project-files" / project_id / "preproduction"
        (asset_root / "assets").mkdir(parents=True)
        (asset_root / "assets" / "shot.mp4").write_bytes(b"video")
        (asset_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
            "assets": [{"id": "shot", "name": "展示素材", "kind": "video", "file": "shot.mp4", "duration": 4}],
        }), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        available = client.get(base).json()["assets"]
        assert [(asset["id"], asset["name"], asset["kind"]) for asset in available] == [("shot", "展示素材", "video")]
        assert "file" not in available[0]
        first = client.post(base, json={"sellingPoint": "省时", "script": "演示操作"}, headers=INTENT).json()["task"]
        second = client.post(base, json={"sellingPoint": "易用", "script": "展示界面"}, headers=INTENT).json()["task"]
        variant = first["variant"]
        tracks = variant["tracks"]
        tracks[0]["clips"] = [{"id": "clip-a", "assetId": "shot", "start": 0, "inPoint": 1, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        path = f"{base}/{first['id']}/variant"

        saved = client.put(path, json={"revision": 0, "tracks": tracks}, headers=INTENT)

        assert saved.status_code == 200, saved.text
        assert saved.json()["variant"]["revision"] == 1
        reopened = client.get(base).json()["tasks"]
        assert reopened[0]["variant"]["tracks"][0]["clips"][0]["inPoint"] == 1
        assert reopened[1]["id"] == second["id"] and reopened[1]["variant"]["revision"] == 0
        assert client.get(f"/api/projects/{project_id}/timeline").json()["revision"] == 0
        assert client.put(path, json={"revision": 0, "tracks": tracks}, headers=INTENT).status_code == 409
        for revision in ("0", False, 0.0):
            invalid_revision = client.put(path, json={"revision": revision, "tracks": tracks}, headers=INTENT)
            assert invalid_revision.status_code == 409
            assert invalid_revision.json()["detail"]["code"] == "batch_variant_conflict"
        tracks[0]["clips"][0]["assetId"] = "missing"
        assert client.put(path, json={"revision": 1, "tracks": tracks}, headers=INTENT).status_code == 422


def test_variant_candidate_replacement_reads_back_pending_reframe(tmp_path):
    """Batch variant save shares the replacement contract with Timeline."""
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "替换主体素材"}).json()["id"]
        asset_root = tmp_path / "project-files" / project_id / "preproduction"
        (asset_root / "assets").mkdir(parents=True)
        for name in ("old.mp4", "new.mp4"):
            (asset_root / "assets" / name).write_bytes(b"video")
        (asset_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [], "assets": [
            {"id": "old", "name": "旧素材", "kind": "video", "file": "old.mp4", "duration": 4},
            {"id": "new", "name": "新素材", "kind": "video", "file": "new.mp4", "duration": 4},
        ]}), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "省时", "script": "演示"}, headers=INTENT).json()["task"]
        state_path = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state = json.loads(state_path.read_text())
        clip = {"id": "clip-a", "assetId": "old", "start": 0, "inPoint": 0, "duration": 2, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0,
                "reframe": {"schemaVersion": 1, "sourceFingerprint": "", "sourceWidth": 0, "sourceHeight": 0, "targetAspect": {"width": 720, "height": 1280}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256, "samples": [], "uncertaintyReasons": ["待分析"], "status": "review_required", "override": {"mode": "auto", "keyframes": []}}}
        state["tasks"][0]["variant"]["tracks"][0]["clips"] = [clip]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        reopened = client.get(base).json()["tasks"][0]
        tracks = reopened["variant"]["tracks"]
        tracks[0]["clips"][0]["assetId"] = "new"
        tracks[0]["clips"][0].pop("reframe")
        saved = client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        pending = saved.json()["variant"]["tracks"][0]["clips"][0]["reframe"]
        assert pending["sourceFingerprint"] == "" and pending["samples"] == []
        assert pending["override"] == {"mode": "auto", "keyframes": []}
        assert client.get(base).json()["tasks"][0]["variant"]["tracks"][0]["clips"][0]["reframe"] == pending


def test_script_recommendation_uses_asset_notes_and_never_replaces_saved_variant(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "营销视频"}).json()["id"]
        asset_root = tmp_path / "project-files" / project_id / "preproduction"
        (asset_root / "assets").mkdir(parents=True)
        for name in ("speed", "water", "battery"):
            (asset_root / "assets" / f"{name}.mp4").write_bytes(b"video")
        (asset_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
            "assets": [
                {"id": "speed", "name": "快速启动", "notes": "快速启动，节省等待时间", "kind": "video", "file": "speed.mp4", "duration": 4},
                {"id": "water", "name": "防水测试", "notes": "防水测试，雨天拍摄", "kind": "video", "file": "water.mp4", "duration": 4},
                {"id": "battery", "name": "续航展示", "notes": "持久续航，户外使用", "kind": "video", "file": "battery.mp4", "duration": 4},
            ],
        }), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        first = client.post(base, json={"sellingPoint": "省时", "script": "快速启动。持久续航。"}, headers=INTENT).json()["task"]
        second = client.post(base, json={"sellingPoint": "户外", "script": "防水测试。持久续航。"}, headers=INTENT).json()["task"]
        picks = ["speed", "water", "battery"]
        first_response = client.post(f"{base}/{first['id']}/recommendations", json={"revision": 0, "assetIds": picks}, headers=INTENT)
        second_response = client.post(f"{base}/{second['id']}/recommendations", json={"revision": 0, "assetIds": picks}, headers=INTENT)
        assert first_response.status_code == 200, first_response.text
        assert second_response.status_code == 200, second_response.text
        first_proposal = first_response.json()["proposal"]
        second_proposal = second_response.json()["proposal"]
        assert [item["assetId"] for item in first_proposal["matches"]] == ["speed", "battery"]
        assert [item["assetId"] for item in second_proposal["matches"]] == ["water", "battery"]
        assert first_proposal["matches"][0]["scriptSegment"] == "快速启动"
        assert "快速" in first_proposal["matches"][0]["matchedTerms"]
        assert first_proposal["method"] == "asset-metadata-keywords"
        assert first_proposal["assetIds"] == picks
        reopened = client.get(base).json()["tasks"]
        assert reopened[0]["proposal"]["matches"] == first_proposal["matches"]
        assert reopened[0]["variant"]["tracks"][0]["clips"] == []

        tracks = first["variant"]["tracks"]
        tracks[0]["clips"] = first_proposal["clips"]
        tracks[0]["clips"][0]["duration"] = 2
        saved = client.put(f"{base}/{first['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        again = client.post(f"{base}/{first['id']}/recommendations", json={"revision": 1, "assetIds": picks}, headers=INTENT)
        assert again.status_code == 200, again.text
        reopened = client.get(base).json()["tasks"]
        assert reopened[0]["variant"]["tracks"][0]["clips"][0]["duration"] == 2
        assert reopened[0]["variant"]["revision"] == 1
        assert reopened[1]["variant"]["revision"] == 0


def test_recommendation_explains_missing_evidence_and_insufficient_assets(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "营销视频"}).json()["id"]
        asset_root = tmp_path / "project-files" / project_id / "preproduction"
        (asset_root / "assets").mkdir(parents=True)
        (asset_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
            "assets": [{"id": "plain", "name": "clip.mp4", "kind": "video", "file": "plain.mp4", "duration": 4},
                       {"id": "other", "name": "other.mp4", "kind": "video", "file": "other.mp4", "duration": 4},
                       {"id": "audio", "name": "旁白", "kind": "audio", "file": "voice.mp3", "duration": 4}],
        }), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "防水", "script": "防水测试。"}, headers=INTENT).json()["task"]
        url = f"{base}/{task['id']}/recommendations"
        assert client.post(url, json={"revision": 0, "assetIds": ["plain"]}, headers=INTENT).json()["detail"]["code"] == "batch_assets_insufficient"
        result = client.post(url, json={"revision": 0, "assetIds": ["plain", "audio"]}, headers=INTENT)
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "batch_assets_insufficient"
        assert "画面" in result.json()["detail"]["message"]
        unmatched = client.post(url, json={"revision": 0, "assetIds": ["plain", "other"]}, headers=INTENT)
        assert unmatched.status_code == 422
        assert unmatched.json()["detail"]["code"] == "batch_match_unavailable"
        assert "第 1 段" in unmatched.json()["detail"]["message"]
        assert client.get(base).json()["tasks"][0].get("proposal") is None
        damaged = json.loads((asset_root / "state.json").read_text(encoding="utf-8"))
        damaged["assets"][0].update(name="防水测试", duration="broken")
        (asset_root / "state.json").write_text(json.dumps(damaged), encoding="utf-8")
        invalid_duration = client.post(url, json={"revision": 0, "assetIds": ["plain", "other"]}, headers=INTENT)
        assert invalid_duration.status_code == 422
        assert invalid_duration.json()["detail"]["code"] == "batch_asset_unavailable"
        damaged["assets"][0]["duration"] = 4
        (asset_root / "state.json").write_text(json.dumps(damaged), encoding="utf-8")
        missing_file = client.post(url, json={"revision": 0, "assetIds": ["plain", "other"]}, headers=INTENT)
        assert missing_file.status_code == 422
        assert missing_file.json()["detail"]["code"] == "batch_asset_unavailable"
        other = client.post(base, json={"sellingPoint": "防水", "script": "防水测试。"}, headers=INTENT).json()["task"]
        assert client.post(f"{base}/{other['id']}/recommendations", json={"revision": 1, "assetIds": ["plain", "audio"]}, headers=INTENT).status_code == 409


def test_recommendation_rejects_script_without_content(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "营销视频"}).json()["id"]
        root = tmp_path / "project-files" / project_id / "preproduction"
        root.mkdir(parents=True)
        (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 0, "brief": {}, "shots": [], "assets": [
            {"id": "a", "name": "防水", "kind": "image", "file": "a.png"},
            {"id": "b", "name": "防水", "kind": "image", "file": "b.png"},
        ]}), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "防水", "script": "。。。"}, headers=INTENT).json()["task"]
        result = client.post(f"{base}/{task['id']}/recommendations",
                             json={"revision": 0, "assetIds": ["a", "b"]}, headers=INTENT)
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "batch_script_empty"


def test_montage_proposal_is_traceable_and_applies_through_variant_revision_rules(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "可解释混剪"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        searches = [client.post(
            f"/api/projects/{project_id}/segment-searches",
            json={"query": query, "limit": 2}, headers=INTENT,
        ).json() for query in ("展示杯盖防泼溅", "展示通勤携带效果")]
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={
            "sellingPoint": "通勤防泼溅", "script": "展示杯盖防泼溅。展示通勤携带效果。",
        }, headers=INTENT).json()["task"]

        proposal_request = {
            "revision": 0,
            "searchIds": [item["id"] for item in searches],
            "narration": [
                {"durationSeconds": 2.4, "wordBoundariesSeconds": [1.2, 2.4]},
                {"durationSeconds": 2.0, "wordBoundariesSeconds": [1.0, 2.0]},
            ],
            "beatTimes": [2.18, 4.4],
        }
        created = client.post(f"{base}/{task['id']}/montage-proposals", json=proposal_request, headers=INTENT)

        assert created.status_code == 201, created.text
        proposal = created.json()["proposal"]
        assert [item["text"] for item in proposal["segments"]] == ["展示杯盖防泼溅", "展示通勤携带效果"]
        assert [item["suggestion"]["candidateId"] for item in proposal["segments"]] == ["segment-lid", "segment-commute"]
        assert proposal["inputSnapshot"]["segments"][0]["searchId"] == searches[0]["id"]
        assert proposal["revision"] == 0
        assert proposal["transitions"][0]["template"] == "dissolve"
        assert proposal["segments"][0]["suggestion"]["sceneId"] == "lid:segment-lid"
        reopened = client.get(base).json()["tasks"][0]["montageProposals"][0]
        assert reopened["id"] == proposal["id"]

        abandoned = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "abandon",
        }, headers=INTENT)
        assert abandoned.status_code == 200
        assert abandoned.json()["variant"]["revision"] == 0
        assert abandoned.json()["proposal"]["status"] == "abandoned"

        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json=proposal_request, headers=INTENT).json()["proposal"]
        assert proposal["id"] != abandoned.json()["proposal"]["id"]
        assert len(client.get(base).json()["tasks"][0]["montageProposals"]) == 2
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert applied.status_code == 200, applied.text
        assert applied.json()["variant"]["revision"] == 1
        assert [clip["assetId"] for clip in applied.json()["variant"]["tracks"][0]["clips"]] == ["lid", "commute"]
        assert applied.json()["variant"]["transitions"] == proposal["transitions"]
        conflict = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 1, "strategy": "overwrite",
        }, headers=INTENT)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "batch_variant_conflict"

        regenerated = client.post(f"{base}/{task['id']}/montage-proposals", json={
            **proposal_request, "revision": 1,
        }, headers=INTENT).json()["proposal"]
        merged = client.post(f"{base}/{task['id']}/montage-proposals/{regenerated['id']}/apply", json={
            "revision": 1, "proposalRevision": 0, "strategy": "merge",
        }, headers=INTENT)
        assert merged.status_code == 200, merged.text
        merged_clips = merged.json()["variant"]["tracks"][0]["clips"]
        assert len(merged_clips) == 4
        assert len({clip["id"] for clip in merged_clips}) == 4


def test_editing_proposal_does_not_convert_old_transition_handle_into_narration_time(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "转场人工调整"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        searches = [client.post(f"/api/projects/{project_id}/segment-searches",
                               json={"query": query, "limit": 2}, headers=INTENT).json()
                    for query in ("展示杯盖", "展示通勤")]
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "方便", "script": "展示杯盖。展示通勤。"}, headers=INTENT).json()["task"]
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 0, "searchIds": [item["id"] for item in searches],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}] * 2,
            "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        assert proposal["clips"][0]["duration"] == 2.35
        edited = client.put(f"{base}/{task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": 0, "segments": [{"segmentId": item["id"], "candidateId": item["selectedCandidateId"],
                "inPointSeconds": item["selectedClip"]["inPoint"], "durationSeconds": item["selectedClip"]["duration"]}
                for item in proposal["segments"]],
        }, headers=INTENT)
        assert edited.status_code == 200, edited.text
        current = edited.json()["proposal"]
        assert [(clip["start"], clip["duration"]) for clip in current["clips"]] == [(0, 2), (2, 2)]
        assert current["transitions"][0]["template"] == "cut"
        assert current["segments"][0]["suggestionClip"]["duration"] == 2.35


def test_montage_proposal_replacement_preserves_suggestion_and_stale_source_blocks_apply(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "人工微调"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        search = client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": "展示杯盖防泼溅", "limit": 2,
        }, headers=INTENT).json()
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "防泼溅", "script": "展示杯盖防泼溅。"}, headers=INTENT).json()["task"]
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 0, "searchIds": [search["id"]],
            "narration": [{"durationSeconds": 2.0, "wordBoundariesSeconds": [2.0]}], "beatTimes": [2.05],
        }, headers=INTENT).json()["proposal"]
        assert proposal["segments"][0]["timing"]["beatSnap"] == 2.05
        original_clip = proposal["segments"][0]["selectedClip"]

        changed = client.put(f"{base}/{task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": 0,
            "segments": [{"segmentId": proposal["segments"][0]["id"], "candidateId": "segment-commute",
                "inPointSeconds": 1.5, "durationSeconds": 2.0}],
        }, headers=INTENT)

        assert changed.status_code == 200, changed.text
        adjusted = changed.json()["proposal"]
        assert adjusted["segments"][0]["suggestion"]["candidateId"] == "segment-lid"
        assert adjusted["segments"][0]["selectedCandidateId"] == "segment-commute"
        assert adjusted["segments"][0]["selectedClip"]["inPoint"] == 1.5
        assert adjusted["segments"][0]["selectedClip"]["duration"] == 2.0
        assert adjusted["segments"][0]["timing"]["beatSnap"] is None
        assert "人工时长调整改变了节奏位置，已取消原节拍吸附" in adjusted["segments"][0]["reasons"]
        assert any("人工选择候选 segment-commute" in reason for reason in adjusted["segments"][0]["reasons"])
        assert any("当前候选本地视觉相关度" in reason for reason in adjusted["segments"][0]["reasons"])
        assert adjusted["revision"] == 1

        restored = client.put(f"{base}/{task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": 1,
            "segments": [{"segmentId": proposal["segments"][0]["id"], "candidateId": "segment-lid",
                "inPointSeconds": original_clip["inPoint"], "durationSeconds": original_clip["duration"]}],
        }, headers=INTENT)
        assert restored.status_code == 200, restored.text
        restored_proposal = restored.json()["proposal"]
        assert restored_proposal["segments"][0]["selectedClip"]["id"] == original_clip["id"]
        assert restored_proposal["revision"] == 2

        source = tmp_path / "project-files" / project_id / "preproduction" / "assets" / "lid.mp4"
        source.write_bytes(b"changed-source")
        stale = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 2, "strategy": "overwrite",
        }, headers=INTENT)
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "montage_proposal_stale"


def test_montage_gap_can_be_reallocated_and_short_proposal_cannot_be_applied(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "缺口补齐"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        search = client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": "展示杯盖防泼溅", "limit": 2,
        }, headers=INTENT).json()
        restricted_search = client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": "展示杯盖防泼溅", "limit": 2,
        }, headers=INTENT).json()
        restricted_path = (tmp_path / "project-files" / project_id / "segment-retrieval" / "searches"
                           / f"{restricted_search['id']}.json")
        restricted_state = json.loads(restricted_path.read_text(encoding="utf-8"))
        restricted_state["localCandidates"] = [
            item for item in restricted_state["localCandidates"] if item["candidateId"] == "segment-lid"
        ]
        restricted_path.write_text(json.dumps(restricted_state), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={
            "sellingPoint": "防泼溅", "script": "展示杯盖防泼溅。展示杯盖防泼溅。",
        }, headers=INTENT).json()["task"]
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 0, "searchIds": [search["id"], restricted_search["id"]],
            "narration": [
                {"durationSeconds": 2.0, "wordBoundariesSeconds": [2.0]},
                {"durationSeconds": 2.0, "wordBoundariesSeconds": [2.0]},
            ], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]

        assert proposal["segments"][1]["status"] == "gap"
        assert [item["candidate"]["candidateId"] for item in proposal["segments"][1]["alternatives"]] == ["segment-lid"]
        changed = client.put(f"{base}/{task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": 0,
            "segments": [
                {"segmentId": "script-segment-1", "candidateId": "segment-commute",
                    "inPointSeconds": 1.0, "durationSeconds": 2.0},
                {"segmentId": "script-segment-2", "candidateId": "segment-lid",
                    "inPointSeconds": 1.0, "durationSeconds": 2.0},
            ],
        }, headers=INTENT)
        assert changed.status_code == 200, changed.text
        assert [item["status"] for item in changed.json()["proposal"]["segments"]] == ["planned", "planned"]
        assert changed.json()["proposal"]["unmet"] == []

        short_task = client.post(base, json={"sellingPoint": "时长门禁", "script": "展示杯盖防泼溅。"}, headers=INTENT).json()["task"]
        short = client.post(f"{base}/{short_task['id']}/montage-proposals", json={
            "revision": 0, "searchIds": [search["id"]],
            "narration": [{"durationSeconds": 5.0, "wordBoundariesSeconds": [5.0]}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        assert short["unmet"]
        refused = client.post(f"{base}/{short_task['id']}/montage-proposals/{short['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert refused.status_code == 422
        assert refused.json()["detail"]["code"] == "montage_proposal_incomplete"


def test_montage_merge_appends_after_existing_manual_edit(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "合并提案"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        search = client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": "展示杯盖防泼溅", "limit": 2,
        }, headers=INTENT).json()
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "防泼溅", "script": "展示杯盖防泼溅。"}, headers=INTENT).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [{
            "id": "manual-clip", "assetId": "commute", "start": 0, "inPoint": 0, "duration": 1,
            "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0,
        }]
        saved = client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 1, "searchIds": [search["id"]],
            "narration": [{"durationSeconds": 2.0, "wordBoundariesSeconds": [1.0, 2.0]}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]

        merged = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 1, "proposalRevision": 0, "strategy": "merge",
        }, headers=INTENT)

        assert merged.status_code == 200, merged.text
        clips = merged.json()["variant"]["tracks"][0]["clips"]
        assert [clip["id"] for clip in clips[:1]] == ["manual-clip"]
        assert clips[1]["start"] == 1
        assert merged.json()["variant"]["revision"] == 2


def test_montage_beat_spacing_keeps_existing_voiceover_and_subtitles_aligned(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "节拍配音同步"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        preproduction = tmp_path / "project-files" / project_id / "preproduction"
        asset_state = json.loads((preproduction / "state.json").read_text(encoding="utf-8"))
        asset_state["assets"].extend([
            {"id": "voice-a", "name": "第一段配音", "kind": "audio", "file": "voice-a.wav", "duration": 2},
            {"id": "voice-b", "name": "第二段配音", "kind": "audio", "file": "voice-b.wav", "duration": 2},
        ])
        (preproduction / "state.json").write_text(json.dumps(asset_state), encoding="utf-8")
        searches = [client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": query, "limit": 2,
        }, headers=INTENT).json() for query in ("展示杯盖防泼溅", "展示通勤携带效果")]
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={
            "sellingPoint": "同步", "script": "展示杯盖防泼溅。展示通勤携带效果。",
        }, headers=INTENT).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [
            {"id": "old-a", "assetId": "lid", "start": 0, "inPoint": 1, "duration": 2,
             "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0},
            {"id": "old-b", "assetId": "commute", "start": 2, "inPoint": 1, "duration": 2,
             "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0},
        ]
        tracks.append({"id": "voiceover", "name": "AI 配音", "kind": "audio", "muted": False, "hidden": False,
                       "clips": [
                           {"id": "voice-clip-a", "assetId": "voice-a", "start": 0, "inPoint": 0, "duration": 2,
                            "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0},
                           {"id": "voice-clip-b", "assetId": "voice-b", "start": 2, "inPoint": 0, "duration": 2,
                            "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0},
                       ]})
        saved = client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        subtitles = client.put(f"{base}/{task['id']}/subtitles", json={
            "revision": 0, "timelineRevision": 1, "cues": [
                {"id": "cue-a", "start": 0, "end": 2, "text": "展示杯盖防泼溅"},
                {"id": "cue-b", "start": 2, "end": 4, "text": "展示通勤携带效果"},
            ],
        }, headers=INTENT)
        assert subtitles.status_code == 200, subtitles.text
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 1, "searchIds": [item["id"] for item in searches],
            "narration": [
                {"durationSeconds": 2, "wordBoundariesSeconds": [], "source": "measured_voiceover"},
                {"durationSeconds": 2, "wordBoundariesSeconds": [], "source": "measured_voiceover"},
            ], "beatTimes": [2.05, 4.1],
        }, headers=INTENT).json()["proposal"]

        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 1, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)

        assert applied.status_code == 200, applied.text
        variant = applied.json()["variant"]
        video = next(track for track in variant["tracks"] if track["id"] == "video")
        voice = next(track for track in variant["tracks"] if track["id"] == "voiceover")
        assert [clip["start"] for clip in video["clips"]] == [0, 2.05]
        assert [clip["start"] for clip in voice["clips"]] == [0, 2.05]
        assert [cue["start"] for cue in variant["subtitles"]["cues"]] == [0, 2.05]


def test_montage_beat_spacing_moves_saved_subtitles_without_a_voiceover_track(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "节拍字幕同步"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        searches = [client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": query, "limit": 2,
        }, headers=INTENT).json() for query in ("展示杯盖防泼溅", "展示通勤携带效果")]
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={
            "sellingPoint": "同步", "script": "展示杯盖防泼溅。展示通勤携带效果。",
        }, headers=INTENT).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [
            {"id": "old-a", "assetId": "lid", "start": 0, "inPoint": 1, "duration": 2,
             "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0},
            {"id": "old-b", "assetId": "commute", "start": 2, "inPoint": 1, "duration": 2,
             "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0},
        ]
        saved = client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        subtitles = client.put(f"{base}/{task['id']}/subtitles", json={
            "revision": 0, "timelineRevision": 1, "cues": [
                {"id": "cue-a", "start": 0, "end": 2, "text": "展示杯盖防泼溅"},
                {"id": "cue-b", "start": 2, "end": 4, "text": "展示通勤携带效果"},
            ],
        }, headers=INTENT)
        assert subtitles.status_code == 200, subtitles.text
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 1, "searchIds": [item["id"] for item in searches],
            "narration": [
                {"durationSeconds": 2, "wordBoundariesSeconds": [1, 2], "source": "provided"},
                {"durationSeconds": 2, "wordBoundariesSeconds": [1, 2], "source": "provided"},
            ], "beatTimes": [2.05, 4.1],
        }, headers=INTENT).json()["proposal"]

        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 1, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)

        assert applied.status_code == 200, applied.text
        variant = applied.json()["variant"]
        video = next(track for track in variant["tracks"] if track["id"] == "video")
        assert [clip["start"] for clip in video["clips"]] == [0, 2.05]
        assert [cue["start"] for cue in variant["subtitles"]["cues"]] == [0, 2.05]


def test_english_sentences_get_distinct_matches_and_selling_point_evidence():
    task = {"sellingPoint": "fast launch", "script": "Show product. Long battery life.", "variant": {
        "revision": 0, "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": [{"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": []}],
    }}
    assets = {
        "slow": {"id": "slow", "name": "product overview", "kind": "video", "duration": 3},
        "fast": {"id": "fast", "name": "product fast launch", "kind": "video", "duration": 3},
        "battery": {"id": "battery", "name": "long battery life", "kind": "video", "duration": 3},
    }

    proposal = recommend(task, assets, ["slow", "fast", "battery"])

    assert [match["scriptSegment"] for match in proposal["matches"]] == ["Show product", "Long battery life"]
    assert [match["assetId"] for match in proposal["matches"]] == ["fast", "battery"]
    assert proposal["matches"][0]["matchedSellingPointTerms"] == ["fast", "launch"]


def test_recommendation_reserves_the_only_asset_that_matches_a_later_segment():
    task = {"sellingPoint": "快速", "script": "快速启动。持久续航。", "variant": {
        "revision": 0, "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": [{"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": []}],
    }}
    assets = {
        "both": {"id": "both", "name": "快速启动持久续航", "kind": "video", "duration": 3},
        "first": {"id": "first", "name": "快速启动", "kind": "video", "duration": 3},
    }

    proposal = recommend(task, assets, ["both", "first"])

    assert [match["assetId"] for match in proposal["matches"]] == ["first", "both"]


def test_preview_uses_saved_snapshot_and_old_preview_remains_after_edit(tmp_path):
    project_id = "p1"
    asset_root = tmp_path / "project-files" / project_id / "preproduction"
    (asset_root / "assets").mkdir(parents=True)
    source = asset_root / "assets" / "shot.mp4"
    source.write_bytes(b"video")
    (asset_root / "state.json").write_text(json.dumps({
        "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "shot", "name": "展示素材", "kind": "video", "file": "shot.mp4", "duration": 4}],
    }), encoding="utf-8")
    jobs = []
    received = []

    class Queue:
        def submit(self, kind, pid, handler):
            jobs.append((pid, handler))
            return True

    broken = [False]

    def renderer(timeline, sources, output, **kwargs):
        if broken[0]:
            raise RuntimeError("编码失败")
        received.append((timeline, sources["shot"].read_bytes()))
        output.write_bytes(b"preview")

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue(), renderer=renderer))
    client = TestClient(app)
    base = f"/api/projects/{project_id}/batch-edits"
    task = client.post(base, json={"sellingPoint": "省时", "script": "操作演示"}).json()["task"]
    path = f"{base}/{task['id']}/variant"
    assert client.post(path + "/previews", json={"revision": 0}).status_code == 422
    tracks = task["variant"]["tracks"]
    tracks[0]["clips"] = [{"id": "clip-a", "assetId": "shot", "start": 0, "inPoint": 1, "duration": 2,
                           "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
    assert client.put(path, json={"revision": 0, "tracks": tracks}).status_code == 200

    submitted = client.post(path + "/previews", json={"revision": 1})

    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["variant"]["runs"][0]["id"]
    assert client.get(path + f"/previews/{run_id}/output").status_code == 409
    source.write_bytes(b"changed")
    pid, job = jobs.pop()
    job(pid)
    assert received[0][1] == b"video"
    assert received[0][0]["tracks"][0]["clips"][0]["inPoint"] == 1
    assert client.get(path + f"/previews/{run_id}/output").content == b"preview"
    tracks[0]["clips"][0]["inPoint"] = 0
    assert client.put(path, json={"revision": 1, "tracks": tracks}).status_code == 200
    assert client.get(path + f"/previews/{run_id}/output").status_code == 409
    broken[0] = True
    newer = client.post(path + "/previews", json={"revision": 2})
    assert newer.status_code == 202
    pid, job = jobs.pop()
    job(pid)
    assert client.get(base).json()["tasks"][0]["variant"]["runs"][-1]["status"] == "failed"
    assert client.get(path + f"/previews/{run_id}/output").status_code == 409


@pytest.mark.parametrize("tasks", [
    [{"id": "task-a"}],
    [{"id": "task-a", "sellingPoint": "省时", "script": "操作", "variant": {
        "id": "variant-a", "revision": 1, "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": [], "runs": [{"id": "run-a"}],
    }}],
    [{"id": "task-a", "sellingPoint": "省时", "script": "操作", "variant": {
        "id": "variant-a", "revision": 1, "settings": {"width": None, "height": 1280, "fps": 30},
        "tracks": [], "runs": [],
    }}],
    [{"id": "task-a", "sellingPoint": "省时", "script": "操作", "variant": {
        "id": "variant-a", "revision": 1, "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": [{"clips": []}], "runs": [],
    }}],
])
def test_damaged_task_state_is_reported_as_unavailable(tmp_path, tasks):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "客户短视频"}).json()["id"]
        state = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"schemaVersion": 1, "tasks": tasks}), encoding="utf-8")

        result = client.get(f"/api/projects/{project_id}/batch-edits")

        assert result.status_code == 503
        assert result.json()["detail"]["code"] == "batch_storage_invalid"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="需要本地 FFmpeg")
def test_two_source_variant_renders_a_real_vertical_preview(tmp_path):
    project_id = "p1"
    asset_root = tmp_path / "project-files" / project_id / "preproduction"
    (asset_root / "assets").mkdir(parents=True)
    for color in ("red", "blue"):
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"color=c={color}:s=64x48:r=24:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(asset_root / "assets" / f"{color}.mp4")], check=True)
    (asset_root / "state.json").write_text(json.dumps({
        "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": color, "name": color, "notes": "红色画面" if color == "red" else "蓝色画面",
                    "kind": "video", "file": f"{color}.mp4", "duration": 1} for color in ("red", "blue")],
    }), encoding="utf-8")
    jobs = []

    class Queue:
        def submit(self, kind, pid, handler):
            jobs.append((pid, handler))
            return True

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue()))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "色彩对比", "script": "红色画面。蓝色画面。"}).json()["task"]
        tracks = task["variant"]["tracks"]
        recommended = client.post(f"{base}/{task['id']}/recommendations", json={"revision": 0, "assetIds": ["red", "blue"]})
        assert recommended.status_code == 200, recommended.text
        tracks[0]["clips"] = recommended.json()["proposal"]["clips"]
        assert [clip["assetId"] for clip in tracks[0]["clips"]] == ["red", "blue"]
        tracks[0]["clips"][0]["duration"] = 0.5
        tracks[0]["clips"][1]["start"] = 0.5
        path = f"{base}/{task['id']}/variant"
        assert client.put(path, json={"revision": 0, "tracks": tracks}).status_code == 200
        run = client.post(path + "/previews", json={"revision": 1})
        assert run.status_code == 202, run.text
        run_id = run.json()["variant"]["runs"][0]["id"]
        pid, handler = jobs.pop()
        handler(pid)
        assert client.get(base).json()["tasks"][0]["variant"]["runs"][0]["status"] == "completed"
        preview = client.get(path + f"/previews/{run_id}/output")
        assert preview.status_code == 200
        assert client.get(path.replace("/p1/", "/p2/") + f"/previews/{run_id}/output").status_code == 404
        output = tmp_path / "preview.mp4"
        output.write_bytes(preview.content)
        metadata = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                                              "-show_entries", "format=duration", "-of", "json", str(output)],
                                             check=True, capture_output=True, text=True).stdout)
        assert metadata["streams"][0]["height"] > metadata["streams"][0]["width"]
        assert 1.4 <= float(metadata["format"]["duration"]) <= 1.6
        other = client.post(base, json={"sellingPoint": "逆序展示", "script": "蓝色画面。红色画面。"}).json()["task"]
        other_proposal = client.post(f"{base}/{other['id']}/recommendations", json={"revision": 0, "assetIds": ["red", "blue"]})
        assert other_proposal.status_code == 200, other_proposal.text
        assert [clip["assetId"] for clip in other_proposal.json()["proposal"]["clips"]] == ["blue", "red"]
        other_tracks = other["variant"]["tracks"]
        other_tracks[0]["clips"] = other_proposal.json()["proposal"]["clips"]
        other_path = f"{base}/{other['id']}/variant"
        assert client.put(other_path, json={"revision": 0, "tracks": other_tracks}).status_code == 200
        other_run = client.post(other_path + "/previews", json={"revision": 1})
        assert other_run.status_code == 202, other_run.text
        pid, handler = jobs.pop()
        handler(pid)
        other_id = other_run.json()["variant"]["runs"][0]["id"]
        other_preview = client.get(other_path + f"/previews/{other_id}/output")
        assert other_preview.status_code == 200
        assert other_preview.content != preview.content


def test_batch_reframe_repeat_cancel_retry_and_source_cas(tmp_path):
    """A newer analysis token owns the clip; cancelled and stale jobs cannot publish."""
    from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint
    project_id = "reframe-batch"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:size=64x48:rate=24:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True, text=True)
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "source", "name": "source", "kind": "video", "file": "source.mp4", "duration": 8}]}, ensure_ascii=False), encoding="utf-8")
    jobs = []
    class Queue:
        def submit(self, _kind, pid, handler):
            jobs.append((pid, handler)); return True
    def analyzer(path, _start, _end, aspect):
        return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(path), "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": aspect, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
                            {"timeSeconds": 2, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}
    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue(), reframe_analyzer=analyzer))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "x", "script": "x"}).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "clip", "assetId": "source", "start": 0, "inPoint": 0, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}).status_code == 200
        url = f"{base}/{task['id']}/variant/reframes/clip"
        assert client.post(url + "/analyze", json={"revision": 1}).status_code == 202
        assert client.post(url + "/analyze", json={"revision": 1}).status_code == 202
        assert client.post(url + "/cancel", json={"revision": 1}).status_code == 200
        jobs.pop(0)[1](project_id)
        assert client.get(base).json()["tasks"][0]["variant"]["reframeAnalysis"]["clip"]["status"] == "cancelled"
        assert client.post(url + "/analyze", json={"revision": 1}).status_code == 202
        jobs.pop()[1](project_id)
        variant = client.get(base).json()["tasks"][0]["variant"]
        assert variant["revision"] == 2
        assert variant["tracks"][0]["clips"][0]["reframe"]["status"] == "review_required"
        forged = variant["tracks"]
        forged[0]["clips"][0]["reframe"]["status"] = "ready"
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 2, "tracks": forged}).status_code == 422
        assert client.post(url + "/analyze", json={"revision": 2}).status_code == 202
        source.unlink()
        jobs.pop()[1](project_id)
        deleted = client.get(base).json()["tasks"][0]["variant"]["reframeAnalysis"]["clip"]
        assert deleted["status"] == "failed"
        assert "素材" in deleted["error"]


def test_batch_reframe_proves_source_outside_lock_and_aborts_stale_submission(tmp_path, monkeypatch):
    """A slow batch source proof neither blocks the variant lock nor queues stale work."""
    from app import montage_reframe

    project_id = "reframe-lock-free-batch"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:size=64x48:rate=24:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True, text=True)
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "source", "name": "source", "kind": "video", "file": "source.mp4", "duration": 8}]}, ensure_ascii=False), encoding="utf-8")
    queue = DeferredComputeQueue()
    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), queue))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "x", "script": "x"}).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "clip", "assetId": "source", "start": 0, "inPoint": 0, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        variant_path = f"{base}/{task['id']}/variant"
        assert client.put(variant_path, json={"revision": 0, "tracks": tracks}).status_code == 200
        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        original = montage_reframe.source_fingerprint

        def blocked_fingerprint(path):
            entered.set()
            assert release.wait(5), "测试未释放批量主体分析来源证明"
            return original(path)

        monkeypatch.setattr(montage_reframe, "source_fingerprint", blocked_fingerprint)
        result = {}
        submitted = threading.Thread(target=lambda: result.setdefault(
            "response", client.post(variant_path + "/reframes/clip/analyze", json={"revision": 1}),
        ))
        submitted.start()
        assert entered.wait(5)
        reader = threading.Thread(target=lambda: (result.setdefault("read", client.get(base)), read_done.set()))
        reader.start()
        try:
            assert read_done.wait(1), "慢来源证明不应占用批量变体锁"
            current = result["read"].json()["tasks"][0]["variant"]
            assert client.post(variant_path + "/reframes/clip/cancel", json={"revision": 1}).status_code == 409
            changed = client.put(variant_path, json={
                "revision": current["revision"], "tracks": current["tracks"],
                "settings": {"width": 1280, "height": 720, "fps": 30}, "aspectMode": "16:9",
            })
            assert changed.status_code == 200, changed.text
        finally:
            release.set()
            submitted.join(5)
            reader.join(5)

        assert result["response"].status_code == 409
        assert result["response"].json()["detail"]["code"] == "batch_variant_conflict"
        variant = client.get(base).json()["tasks"][0]["variant"]
        assert variant.get("reframeAnalysis", {}) == {}
        assert queue.jobs == []


def test_montage_proposal_create_proof_allows_get_then_rejects_concurrent_save(tmp_path, monkeypatch):
    """The public create endpoint must not hold its project lock while proving clips."""
    from app import montage_reframe

    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, proposal = _create_montage_proposal(client, tmp_path)
        queue.jobs.clear()
        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        original = montage_reframe.source_fingerprint
        monkeypatch.setattr(montage_reframe, "source_fingerprint", lambda path: (
            entered.set(), release.wait(5), original(path))[2])
        body = {"revision": 0, "searchIds": [segment["searchId"] for segment in proposal["inputSnapshot"]["segments"]],
                "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": []}
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault("response", client.post(
            f"{base}/{task['id']}/montage-proposals", json=body, headers=INTENT)))
        thread.start()
        assert entered.wait(5)
        reader = threading.Thread(target=lambda: (result.setdefault("read", client.get(base)), read_done.set()))
        reader.start()
        assert read_done.wait(1)
        current = result["read"].json()["tasks"][0]["variant"]
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": current["revision"], "tracks": current["tracks"]}, headers=INTENT).status_code == 200
        release.set(); thread.join(5); reader.join(5)
        assert result["response"].status_code == 409
        assert queue.jobs == [] or all("batch-reframe:" not in job[0] for job in queue.jobs)


def test_montage_proposal_update_proof_preserves_concurrent_cancel(tmp_path, monkeypatch):
    from app import montage_reframe

    app, _queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, proposal = _create_montage_proposal(client, tmp_path)
        entered, release = threading.Event(), threading.Event()
        original = montage_reframe.source_fingerprint
        def blocked(path):
            entered.set(); assert release.wait(5); return original(path)
        monkeypatch.setattr(montage_reframe, "source_fingerprint", blocked)
        body = {"revision": 0, "segments": [{"segmentId": proposal["segments"][0]["id"],
                 "candidateId": proposal["segments"][0]["selectedCandidateId"],
                 "inPointSeconds": proposal["segments"][0]["selectedClip"]["inPoint"],
                 "durationSeconds": proposal["segments"][0]["selectedClip"]["duration"]}]}
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault("response", client.put(
            f"{base}/{task['id']}/montage-proposals/{proposal['id']}", json=body, headers=INTENT)))
        thread.start(); assert entered.wait(5)
        assert client.get(base).status_code == 200
        clip_id = proposal["clips"][0]["id"]
        assert client.post(f"{base}/{task['id']}/variant/reframes/{clip_id}/cancel", json={"revision": 0}, headers=INTENT).status_code == 200
        release.set(); thread.join(5)
        assert result["response"].status_code == 409
        assert client.get(base).json()["tasks"][0]["variant"]["reframeAnalysis"][clip_id]["status"] == "cancelled"


def test_montage_proposal_apply_proof_allows_get_then_leaves_variant_unchanged_on_save(tmp_path, monkeypatch):
    from app import montage_reframe

    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        project_id, base, task, proposal = _create_montage_proposal(client, tmp_path)
        queue.jobs.clear()
        state_path = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"][0]["variant"]["reframeAnalysis"] = {}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        entered, release = threading.Event(), threading.Event()
        original = montage_reframe.source_fingerprint
        def blocked(path):
            entered.set(); assert release.wait(5); return original(path)
        monkeypatch.setattr(montage_reframe, "source_fingerprint", blocked)
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault("response", client.post(
            f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply",
            json={"revision": 0, "proposalRevision": 0, "strategy": "overwrite"}, headers=INTENT)))
        thread.start(); assert entered.wait(5)
        current = client.get(base).json()["tasks"][0]["variant"]
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": current["revision"], "tracks": current["tracks"]}, headers=INTENT).status_code == 200
        release.set(); thread.join(5)
        assert result["response"].status_code == 409
        stored = client.get(base).json()["tasks"][0]
        assert stored["montageProposals"][0]["status"] == "draft"
        assert stored["variant"]["tracks"] == current["tracks"]
        assert queue.jobs == []


def test_batch_reframe_proof_deduplicates_shared_source_and_keeps_multi_clip_submit_atomic(tmp_path, monkeypatch):
    """Two adopted clips sharing one file receive one proof and one all-or-nothing queue job."""
    from app import montage_reframe, timeline_render

    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        project_id = client.post("/api/projects", json={"name": "同源证明回归"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        assets = tmp_path / "project-files" / project_id / "preproduction" / "assets"
        (assets / "commute.mp4").unlink()
        os.link(assets / "lid.mp4", assets / "commute.mp4")
        source_state_path = tmp_path / "project-files" / project_id / "source-segments" / "commute" / "state.json"
        source_state = json.loads(source_state_path.read_text(encoding="utf-8"))
        fingerprint = montage_reframe.source_fingerprint(assets / "lid.mp4")
        source_state["sourceFingerprint"] = fingerprint
        for candidate in source_state["candidates"]:
            candidate["sourceFingerprint"] = fingerprint
        source_state_path.write_text(json.dumps(source_state), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        searches = [client.post(f"/api/projects/{project_id}/segment-searches", json={"query": query, "limit": 2}, headers=INTENT).json()
                    for query in ("展示杯盖防泼溅", "展示通勤携带效果")]
        task = client.post(base, json={"sellingPoint": "防泼溅", "script": "展示杯盖防泼溅。展示通勤携带效果。"}, headers=INTENT).json()["task"]
        proposal_response = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 0, "searchIds": [item["id"] for item in searches],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}] * 2, "beatTimes": [],
        }, headers=INTENT)
        assert proposal_response.status_code == 201, proposal_response.text
        proposal = proposal_response.json()["proposal"]
        assert len(proposal["clips"]) == 2
        queue.jobs.clear()
        state_path = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"][0]["variant"]["reframeAnalysis"] = {}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        calls = {"hash": 0, "media": 0, "timing": 0}
        original_hash = montage_reframe.source_fingerprint
        original_media = timeline_render._probe_media
        original_timing = montage_reframe.probe_source_timing

        def counted_hash(path):
            calls["hash"] += 1
            return original_hash(path)

        def counted_media(path, probe):
            calls["media"] += 1
            return original_media(path, probe)

        def counted_timing(path, probe):
            calls["timing"] += 1
            return original_timing(path, probe)

        monkeypatch.setattr(montage_reframe, "source_fingerprint", counted_hash)
        monkeypatch.setattr(timeline_render, "_probe_media", counted_media)
        monkeypatch.setattr(montage_reframe, "probe_source_timing", counted_timing)
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)

        assert applied.status_code == 200, applied.text
        assert calls == {"hash": 1, "media": 1, "timing": 1}
        variant = client.get(base).json()["tasks"][0]["variant"]
        assert set(variant["reframeAnalysis"]) == {clip["id"] for clip in proposal["clips"]}
        assert {record["status"] for record in variant["reframeAnalysis"].values()} == {"queued"}
        assert len(queue.jobs) == 1


def test_batch_reframe_multi_clip_proof_failure_never_queues_a_subset(tmp_path, monkeypatch):
    """A failed proof in an adopted two-clip request leaves no partial analysis records."""
    from app import montage_reframe

    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        project_id, base, task, proposal = _create_montage_proposal(
            client, tmp_path, script="展示杯盖防泼溅。展示通勤携带效果。",
        )
        queue.jobs.clear()
        state_path = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"][0]["variant"]["reframeAnalysis"] = {}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        before = json.loads(state_path.read_text(encoding="utf-8"))
        original = montage_reframe.source_fingerprint

        def fail_second_source(path):
            if path.name == "commute.mp4":
                raise OSError("模拟第二段来源消失")
            return original(path)

        monkeypatch.setattr(montage_reframe, "source_fingerprint", fail_second_source)
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)

        assert applied.status_code == 409
        assert applied.json()["detail"]["code"] == "reframe_source_unavailable"
        assert json.loads(state_path.read_text(encoding="utf-8")) == before
        variant = client.get(base).json()["tasks"][0]["variant"]
        assert variant.get("reframeAnalysis", {}) == {}
        assert queue.jobs == []


def test_batch_image_reframe_rejects_invalid_manual_keyframes_and_stale_output_aspect(tmp_path):
    project_id = "reframe-image-batch"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "source.png"
    Image.new("RGB", (64, 48), "red").save(source)
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "source", "name": "source", "kind": "image", "file": "source.png", "width": 64, "height": 48}]}), encoding="utf-8")
    jobs = []
    class Queue:
        def submit(self, _kind, pid, handler):
            jobs.append((pid, handler)); return True
    def analyzer(path, _start, _end, aspect):
        return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(path), "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": aspect, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [{"x": .2, "y": .1, "width": .3, "height": .7,
                                                               "label": "person", "confidence": .9}],
                             "center": {"x": .35, "y": .45}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}
    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue(), reframe_analyzer=analyzer))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "x", "script": "x"}).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "clip", "assetId": "source", "start": 0, "inPoint": 0, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        variant_path = f"{base}/{task['id']}/variant"
        assert client.put(variant_path, json={"revision": 0, "tracks": tracks}).status_code == 200
        reframe_path = variant_path + "/reframes/clip"
        assert client.post(reframe_path + "/analyze", json={"revision": 1}).status_code == 202
        queued = client.get(base).json()["tasks"][0]["variant"]
        assert queued["reframeAnalysis"]["clip"]["identity"]["targetAspect"] == {"width": 720, "height": 1280}

        for keyframes in ([{"timeSeconds": .1, "x": .5, "y": .5}], [
            {"timeSeconds": 0, "x": .5, "y": .5}, {"timeSeconds": .1, "x": .6, "y": .5},
        ]):
            invalid_tracks = deepcopy(queued["tracks"])
            invalid_tracks[0]["clips"][0]["reframe"]["override"] = {"mode": "manual", "keyframes": keyframes}
            assert client.put(variant_path, json={"revision": queued["revision"], "tracks": invalid_tracks}).status_code == 422

        changed = client.put(variant_path, json={
            "revision": queued["revision"], "tracks": queued["tracks"],
            "settings": {"width": 1280, "height": 720, "fps": 30}, "aspectMode": "16:9",
        })
        assert changed.status_code == 200, changed.text
        jobs.pop()[1](project_id)
        record = client.get(base).json()["tasks"][0]["variant"]["reframeAnalysis"]["clip"]
        assert record["status"] == "failed"
        assert "输出比例" in record["error"]
        assert client.post(reframe_path + "/analyze", json={"revision": 2}).status_code == 202
        jobs.pop()[1](project_id)
        retried = client.get(base).json()["tasks"][0]["variant"]
        assert retried["reframeAnalysis"]["clip"]["status"] == "completed"
        assert retried["tracks"][0]["clips"][0]["reframe"]["targetAspect"] == {"width": 1280, "height": 720}


def test_batch_reframe_never_accepts_worker_dimensions_when_the_source_probe_has_none(tmp_path, monkeypatch):
    """A batch analysis cannot become ready from self-reported worker dimensions."""
    from app import timeline_render
    project_id = "reframe-size-batch"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "source.mp4"
    source.write_bytes(b"source")
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "source", "name": "source", "kind": "video", "file": "source.mp4", "duration": 8}]}), encoding="utf-8")
    monkeypatch.setattr(timeline_render, "_probe_media", lambda *_: SimpleNamespace(width=None, height=None))
    jobs = []

    class Queue:
        def submit(self, _kind, pid, handler):
            jobs.append((pid, handler)); return True

    def analyzer(path, _start, _end, aspect):
        return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(path), "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": aspect, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue(), reframe_analyzer=analyzer))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "x", "script": "x"}).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "clip", "assetId": "source", "start": 0, "inPoint": 0, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}).status_code == 200
        assert client.post(f"{base}/{task['id']}/variant/reframes/clip/analyze", json={"revision": 1}).status_code == 202
        jobs.pop()[1](project_id)
        record = client.get(base).json()["tasks"][0]["variant"]["reframeAnalysis"]["clip"]
        assert record["status"] == "failed"
        assert "可信源尺寸" in record["error"]


def test_completed_export_download_rechecks_real_reframe_sources_but_keeps_center_and_legacy_available(tmp_path, monkeypatch):
    """The output route must not trust a completed status when a new source probe downgrades auto framing."""
    from copy import deepcopy
    from app import montage_reframe
    from app.montage_reframe import normalize_reframe, probe_source_timing
    project_id = "reframe-delivery"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:size=64x48:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True, text=True)
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "source", "name": "source", "kind": "video", "file": "source.mp4", "duration": 2}]}), encoding="utf-8")
    timing = probe_source_timing(source, "ffprobe")
    assert timing.cfr
    raw = {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 64, "sourceHeight": 48,
           "targetAspect": {"width": 720, "height": 1280}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
           "samples": [{"timeSeconds": point, "boxes": [{"x": .4, "y": .1, "width": .2, "height": .7, "label": "person", "confidence": .9}], "center": {"x": .5, "y": .5}, "confidence": .9}
                       for point in timing.elapsed], "uncertaintyReasons": [], "status": "ready",
           "override": {"mode": "auto", "keyframes": []}}
    ready = normalize_reframe(raw, source_fingerprint=source_fingerprint(source), source_width=64, source_height=48,
                              source_timing=timing, required_start=0, required_end=2)
    assert ready["status"] == "ready"

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda value: SimpleNamespace(id=value) if value == project_id else None, DeferredComputeQueue()))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "x", "script": "x"}).json()["task"]
        state_path = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        stored = state["tasks"][0]
        clip = {"id": "clip", "assetId": "source", "start": 0, "inPoint": 0, "duration": 2,
                "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0, "reframe": ready}
        stored["variant"].update(revision=1, tracks=[{**stored["variant"]["tracks"][0], "clips": [clip]}, stored["variant"]["tracks"][1]])
        snapshot = {"settings": deepcopy(stored["variant"]["settings"]), "tracks": deepcopy(stored["variant"]["tracks"])}
        preview = {"id": "preview", "revision": 1, "contentRevision": 0, "subtitleRevision": 0, "subtitleCues": [],
                   "status": "completed", "error": None, "output": "preview.mp4", "snapshot": deepcopy(snapshot), "sources": {"source": "source.mp4"}}
        review = {"id": "review", "runId": "preview", "decision": "approved", "reason": "确认",
                  "contentRevision": 0, "variantRevision": 1, "subtitleRevision": 0}
        def delivery(run_id, snap):
            return {"id": run_id, "status": "completed", "error": None, "output": run_id + ".mp4",
                    "contentRevision": 0, "variantRevision": 1, "subtitleRevision": 0, "previewRunId": "preview",
                    "review": deepcopy(review), "sellingPoint": "x", "script": "x", "snapshot": snap,
                    "subtitles": [], "assets": [{"id": "source", "name": "source", "kind": "video"}], "sources": {"source": "source.mp4"}}
        stored["variant"]["runs"] = [preview]
        stored["reviews"] = [review]
        stored["variant"]["exports"] = [delivery("delivery", deepcopy(snapshot)), delivery("legacy", {**snapshot, "tracks": [{**snapshot["tracks"][0], "clips": [{key: value for key, value in clip.items() if key != "reframe"}]}, snapshot["tracks"][1]]})]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        for directory in ("runs/preview", "exports/delivery", "exports/legacy"):
            destination = tmp_path / "project-files" / project_id / "batch-edits" / directory / "sources"
            destination.mkdir(parents=True)
            shutil.copyfile(source, destination / "source.mp4")
        deliverables = tmp_path / "project-files" / project_id / "batch-edits" / "deliverables"
        deliverables.mkdir()
        (deliverables / "delivery.mp4").write_bytes(b"delivery")
        (deliverables / "legacy.mp4").write_bytes(b"legacy")
        outputs = tmp_path / "project-files" / project_id / "batch-edits" / "outputs"
        outputs.mkdir()
        (outputs / "preview.mp4").write_bytes(b"preview")
        assert client.get(f"{base}/{task['id']}/exports/delivery/output").content == b"delivery"

        # Download uses the same lock-free proof boundary: unrelated reads and
        # cancellation must remain available while its real PTS probe waits.
        state = json.loads(state_path.read_text(encoding="utf-8"))
        active = delivery("active", deepcopy(snapshot))
        active["status"] = "queued"
        state["tasks"][0]["variant"]["exports"].append(active)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        original_probe = montage_reframe.probe_source_timing
        download_entered, download_release, download_read = threading.Event(), threading.Event(), threading.Event()

        def delayed_download_probe(path, probe):
            download_entered.set()
            assert download_release.wait(5), "测试未释放下载探测"
            return original_probe(path, probe)

        monkeypatch.setattr(montage_reframe, "probe_source_timing", delayed_download_probe)
        download_result = {}
        downloading = threading.Thread(target=lambda: download_result.setdefault(
            "response", client.get(f"{base}/{task['id']}/exports/delivery/output")
        ))
        downloading.start()
        assert download_entered.wait(2)
        reading = threading.Thread(target=lambda: (client.get(base), download_read.set()))
        reading.start()
        try:
            assert download_read.wait(.5), "下载探测期间普通读取不应等待交付锁"
            assert client.post(f"{base}/{task['id']}/exports/active/cancel").status_code == 200
        finally:
            download_release.set()
        downloading.join(5)
        reading.join(5)
        assert download_result["response"].content == b"delivery"

        # Replacing the path with identical bytes during a proof is still a
        # CAS race, not a media-quality failure, and must be retryable.
        race_entered, race_release = threading.Event(), threading.Event()

        def delayed_race_probe(path, probe):
            race_entered.set()
            assert race_release.wait(5), "测试未释放竞态探测"
            return original_probe(path, probe)

        monkeypatch.setattr(montage_reframe, "probe_source_timing", delayed_race_probe)
        race_result = {}
        racing = threading.Thread(target=lambda: race_result.setdefault(
            "response", client.get(f"{base}/{task['id']}/exports/delivery/output")
        ))
        racing.start()
        assert race_entered.wait(2)
        replacement = source.with_name("source-replacement.mp4")
        replacement.write_bytes(source.read_bytes())
        os.replace(replacement, source)
        race_release.set()
        racing.join(5)
        assert race_result["response"].status_code == 409
        assert race_result["response"].json()["detail"]["code"] == "batch_export_reframe_conflict"

        # A delivery proof may take a long time to inspect real frame PTS, but
        # that work must not monopolize the shared project lock.  Submit uses
        # the same proof path and two variants sharing this source must reuse it.
        state = json.loads(state_path.read_text(encoding="utf-8"))
        children = []
        for child_id in ("child-one", "child-two"):
            child = deepcopy(state["tasks"][0])
            child.update(id=child_id, batchId=task["id"], generationId="generation",
                         generation={"status": "ready", "error": None})
            child["variant"]["exports"] = child["variant"]["exports"][:1]
            children.append(child)
        state["tasks"].extend(children)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        probes = []

        def delayed_probe(path, probe):
            probes.append(path)
            entered.set()
            assert release.wait(5), "测试未释放逐帧探测"
            return original_probe(path, probe)

        monkeypatch.setattr(montage_reframe, "probe_source_timing", delayed_probe)
        result = {}
        submitted = threading.Thread(target=lambda: result.setdefault(
            "response", client.post(f"{base}/{task['id']}/exports", json={"taskIds": [child["id"] for child in children]})
        ))
        submitted.start()
        if not entered.wait(2):
            submitted.join(1)
            response = result.get("response")
            pytest.fail(f"导出应在锁外进行真实逐帧探测；实际响应：{response.status_code if response else '未返回'} {response.text if response else ''}")
        reader = threading.Thread(target=lambda: (client.get(base), read_done.set()))
        reader.start()
        try:
            assert read_done.wait(.5), "逐帧探测期间普通读取不应等待交付锁"
        finally:
            release.set()
        submitted.join(5)
        reader.join(5)
        assert not submitted.is_alive() and not reader.is_alive()
        assert result["response"].status_code == 202, result["response"].text
        assert len(probes) == 2, "冻结副本与当前源各验证一次，但同请求同源变体不得重复探测"

        # The second lock must consume only the proof for the exact version it
        # inspected; a concurrent variant edit becomes a retryable conflict and
        # must not leave the lock-free source copy behind.
        state = json.loads(state_path.read_text(encoding="utf-8"))
        next(item for item in state["tasks"] if item["id"] == children[0]["id"])["variant"]["exports"] = []
        state_path.write_text(json.dumps(state), encoding="utf-8")
        exports_before = {item.name for item in (state_path.parent / "exports").iterdir()}
        entered.clear()
        release.clear()
        stale_result = {}
        stale = threading.Thread(target=lambda: stale_result.setdefault(
            "response", client.post(f"{base}/{task['id']}/exports", json={"taskIds": [children[0]["id"]]})
        ))
        stale.start()
        assert entered.wait(2)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        next(item for item in state["tasks"] if item["id"] == children[0]["id"])["variant"]["revision"] += 1
        state_path.write_text(json.dumps(state), encoding="utf-8")
        release.set()
        stale.join(5)
        assert stale_result["response"].status_code == 409
        assert stale_result["response"].json()["detail"]["code"] == "batch_export_reframe_conflict"
        assert {item.name for item in (state_path.parent / "exports").iterdir()} == exports_before

        monkeypatch.setattr(montage_reframe, "probe_source_timing", lambda path, probe:
                            montage_reframe.SourceTiming((), (), False, "新 probe 发现 VFR") if path == source else original_probe(path, probe))
        blocked = client.get(f"{base}/{task['id']}/exports/delivery/output")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "batch_export_reframe_review_required"
        assert client.get(f"{base}/{task['id']}/exports/legacy/output").status_code == 409

        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"][0]["variant"]["exports"][0]["snapshot"]["tracks"][0]["clips"][0]["reframe"]["override"] = {"mode": "center", "keyframes": []}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        assert client.get(f"{base}/{task['id']}/exports/delivery/output").status_code == 409


def test_montage_candidate_replacement_keeps_pending_reframe_and_queues_analysis(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, _task, proposal = _create_montage_proposal(client, tmp_path)
        segment = proposal["segments"][0]
        assert len(queue.jobs) == 1

        replaced = client.put(f"{base}/{_task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": proposal["revision"],
            "segments": [{"segmentId": segment["id"], "candidateId": "segment-commute",
                          "inPointSeconds": 1.0, "durationSeconds": 2.0}],
        }, headers=INTENT)

        assert replaced.status_code == 200, replaced.text
        selected = replaced.json()["proposal"]["segments"][0]["selectedClip"]
        assert selected["assetId"] == "commute"
        assert selected["reframe"]["status"] == "review_required"
        assert selected["reframe"]["samples"] == []
        assert "重新主体分析" in selected["reframe"]["uncertaintyReasons"][0]
        assert len(queue.jobs) == 2

        _kind, pid, handler = queue.jobs[-1]
        handler(pid)
        task = client.get(base).json()["tasks"][0]
        selected = task["montageProposals"][0]["segments"][0]["selectedClip"]
        assert selected["reframe"]["status"] == "review_required"
        assert task["variant"]["reframeAnalysis"][selected["id"]]["status"] == "completed"


def test_two_clip_proposal_adoption_publishes_one_aggregate_reframe_revision(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, proposal = _create_montage_proposal(
            client, tmp_path, script="展示杯盖防泼溅。展示通勤携带效果。",
        )
        assert len(proposal["clips"]) == 2
        assert len(queue.jobs) == 1

        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)

        assert applied.status_code == 200, applied.text
        assert applied.json()["variant"]["revision"] == 1
        assert len(queue.jobs) == 1
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        variant = client.get(base).json()["tasks"][0]["variant"]
        assert variant["revision"] == 2
        clips = variant["tracks"][0]["clips"]
        assert len(clips) == 2
        assert [clip["reframe"]["status"] for clip in clips] == ["review_required", "review_required"]
        assert [variant["reframeAnalysis"][clip["id"]]["status"] for clip in clips] == ["completed", "completed"]


def test_pending_proposal_apply_publishes_to_variant_without_overwriting_later_manual_reframe(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, proposal = _create_montage_proposal(client, tmp_path)
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert applied.status_code == 200, applied.text
        assert len(queue.jobs) == 1

        tracks = applied.json()["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        clip["reframe"]["override"] = {
            "mode": "manual", "keyframes": [{"timeSeconds": clip["inPoint"], "x": .2, "y": .7}],
        }
        manual = client.put(f"{base}/{task['id']}/variant", json={"revision": 1, "tracks": tracks}, headers=INTENT)
        assert manual.status_code == 200, manual.text

        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        variant = client.get(base).json()["tasks"][0]["variant"]
        stored = variant["tracks"][0]["clips"][0]["reframe"]
        assert variant["revision"] == 3
        assert stored["status"] == "review_required"
        assert stored["override"] == {"mode": "manual", "keyframes": [{"timeSeconds": 1.0, "x": .2, "y": .7}]}
        assert len(stored["samples"]) == 2


def test_reapplying_identical_source_preserves_manual_reframe_but_changed_range_or_source_does_not(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, first = _create_montage_proposal(client, tmp_path)
        first_applied = client.post(f"{base}/{task['id']}/montage-proposals/{first['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert first_applied.status_code == 200, first_applied.text
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        tracks = client.get(base).json()["tasks"][0]["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        valid_reframe = clip["reframe"]
        clip["reframe"]["override"] = {
            "mode": "manual", "keyframes": [{"timeSeconds": clip["inPoint"], "x": .25, "y": .65}],
        }
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 2, "tracks": tracks}, headers=INTENT).status_code == 200

        same = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 3, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT)
        assert same.status_code == 201, same.text
        same_proposal = same.json()["proposal"]
        same_applied = client.post(f"{base}/{task['id']}/montage-proposals/{same_proposal['id']}/apply", json={
            "revision": 3, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert same_applied.status_code == 200, same_applied.text
        assert same_applied.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "manual"

        changed_range = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 4, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        range_edit = client.put(f"{base}/{task['id']}/montage-proposals/{changed_range['id']}", json={
            "revision": 0,
            "segments": [{"segmentId": changed_range["segments"][0]["id"], "candidateId": "segment-lid",
                          "inPointSeconds": 1.5, "durationSeconds": 2.0}],
        }, headers=INTENT)
        assert range_edit.status_code == 200, range_edit.text
        range_applied = client.post(f"{base}/{task['id']}/montage-proposals/{changed_range['id']}/apply", json={
            "revision": 4, "proposalRevision": 1, "strategy": "overwrite",
        }, headers=INTENT)
        assert range_applied.status_code == 200, range_applied.text
        assert range_applied.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "auto"

        tracks = range_applied.json()["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        clip["reframe"]["override"] = {
            "mode": "manual", "keyframes": [{"timeSeconds": clip["inPoint"], "x": .3, "y": .6}],
        }
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 5, "tracks": tracks}, headers=INTENT).status_code == 200
        changed_source = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 6, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        source_edit = client.put(f"{base}/{task['id']}/montage-proposals/{changed_source['id']}", json={
            "revision": 0,
            "segments": [{"segmentId": changed_source["segments"][0]["id"], "candidateId": "segment-commute",
                          "inPointSeconds": 1.0, "durationSeconds": 2.0}],
        }, headers=INTENT)
        assert source_edit.status_code == 200, source_edit.text
        source_applied = client.post(f"{base}/{task['id']}/montage-proposals/{changed_source['id']}/apply", json={
            "revision": 6, "proposalRevision": 1, "strategy": "overwrite",
        }, headers=INTENT)
        assert source_applied.status_code == 200, source_applied.text
        stored = source_applied.json()["variant"]["tracks"][0]["clips"][0]
        assert stored["assetId"] == "commute"
        assert stored["reframe"]["override"]["mode"] == "auto"


def test_proposal_same_asset_bytes_change_or_speed_change_never_reuses_manual_reframe(tmp_path, monkeypatch):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        project_id, base, task, first = _create_montage_proposal(client, tmp_path)
        assert client.post(f"{base}/{task['id']}/montage-proposals/{first['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT).status_code == 200
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        tracks = client.get(base).json()["tasks"][0]["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        clip["reframe"]["override"] = {"mode": "manual", "keyframes": [{"timeSeconds": 1, "x": .2, "y": .7}]}
        clip["speed"] = 2
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 2, "tracks": tracks}, headers=INTENT).status_code == 200
        speed_changed = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 3, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{speed_changed['id']}/apply", json={
            "revision": 3, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert applied.status_code == 200, applied.text
        assert applied.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "auto"

        byte_proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 4, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        tracks = applied.json()["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        clip["speed"] = 1
        # A client may only change the current pending record's human override;
        # replaying `valid_reframe` facts would now correctly be rejected.
        clip["reframe"]["override"] = {"mode": "manual", "keyframes": [{"timeSeconds": 1, "x": .2, "y": .7}]}
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 4, "tracks": tracks}, headers=INTENT).status_code == 200
        from app import batch_editing_api
        frozen_search = batch_editing_api.load_planning_search(
            tmp_path, project_id, first["inputSnapshot"]["segments"][0]["searchId"],
        )
        source = tmp_path / "project-files" / project_id / "preproduction" / "assets" / "lid.mp4"
        source.write_bytes(b"replacement-bytes")
        # Keep proposal provenance readable solely to reach the independent
        # render identity gate under test; production normally blocks earlier
        # on this stale search too.
        monkeypatch.setattr(batch_editing_api, "load_planning_search", lambda *_args, **_kwargs: frozen_search)
        changed = client.post(f"{base}/{task['id']}/montage-proposals/{byte_proposal['id']}/apply", json={
            "revision": 5, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert changed.status_code == 200, changed.text
        assert changed.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "auto"


def test_reapplying_empty_hash_manual_pending_does_not_inherit_it(tmp_path):
    app, _queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, first = _create_montage_proposal(client, tmp_path)
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{first['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        tracks = applied.json()["variant"]["tracks"]
        tracks[0]["clips"][0]["reframe"]["override"] = {"mode": "manual", "keyframes": [{"timeSeconds": 1, "x": .2, "y": .7}]}
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 1, "tracks": tracks}, headers=INTENT).status_code == 200
        second = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 2, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        again = client.post(f"{base}/{task['id']}/montage-proposals/{second['id']}/apply", json={
            "revision": 2, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert again.status_code == 200, again.text
        assert again.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "auto"


@pytest.mark.parametrize("field,value", [("assetId", "commute"), ("inPoint", 1.5), ("speed", 2)])
def test_adopted_proposal_job_never_overwrites_variant_identity_edited_after_apply(tmp_path, field, value):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, proposal = _create_montage_proposal(client, tmp_path)
        assert client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT).status_code == 200
        tracks = client.get(base).json()["tasks"][0]["variant"]["tracks"]
        tracks[0]["clips"][0][field] = value
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 1, "tracks": tracks}, headers=INTENT).status_code == 200
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        variant = client.get(base).json()["tasks"][0]["variant"]
        assert variant["revision"] == 2
        assert variant["tracks"][0]["clips"][0]["reframe"]["status"] == "review_required"
        assert variant["reframeAnalysis"][proposal["clips"][0]["id"]]["status"] == "failed"


def test_adopted_multi_clip_job_increments_once_when_first_result_succeeds_and_last_source_is_lost(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        project_id, base, task, proposal = _create_montage_proposal(client, tmp_path, script="展示杯盖防泼溅。展示通勤携带效果。")
        assert client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT).status_code == 200
        (tmp_path / "project-files" / project_id / "preproduction" / "assets" / "commute.mp4").unlink()
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        variant = client.get(base).json()["tasks"][0]["variant"]
        clips = variant["tracks"][0]["clips"]
        assert variant["revision"] == 2
        assert clips[0]["reframe"]["status"] == "review_required"
        assert clips[1]["reframe"]["status"] == "review_required"


def test_batch_reframe_worker_rejects_asset_identity_change_with_same_file(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        project_id, base, task, proposal = _create_montage_proposal(client, tmp_path)
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert applied.status_code == 200, applied.text
        preproduction_path = tmp_path / "project-files" / project_id / "preproduction" / "state.json"
        preproduction = json.loads(preproduction_path.read_text(encoding="utf-8"))
        preproduction["assets"][0]["sourceReferenceId"] = "replacement-reference"
        preproduction_path.write_text(json.dumps(preproduction), encoding="utf-8")
        _kind, pid, handler = queue.jobs[0]
        handler(pid)

        variant = client.get(base).json()["tasks"][0]["variant"]
        assert variant["reframeAnalysis"][proposal["clips"][0]["id"]]["status"] == "failed"
