import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.batch_editing_api import create_batch_editing_router
from app.preproduction import PreproductionStore
from app.preproduction_api import create_preproduction_router
from app.project_assets import ProjectAssets
from app.video_sources import validate_source_status


PROJECT_ID = "project-local-source"
BASE = f"/api/projects/{PROJECT_ID}/preproduction"


class Queue:
    def submit(self, *_args):
        return True


def make_video(path: Path, *, filter_name: str = "testsrc2") -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"{filter_name}=size=64x48:rate=6:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def client_for(tmp_path: Path, *, with_batch: bool = False) -> TestClient:
    app = FastAPI()
    project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, Queue()))
    if with_batch:
        app.include_router(create_batch_editing_router(tmp_path, lambda _: project, Queue()))
    return TestClient(app)


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="本地视频来源合同需要 ffmpeg 和 ffprobe",
)


def upload(client: TestClient, name: str, content: bytes, *, rights_confirmed: bool):
    return client.post(
        BASE + "/assets?role=reference",
        data={"rightsConfirmed": str(rights_confirmed).lower()},
        files={"file": (name, content, "video/mp4")},
    )


def test_local_video_requires_prior_rights_confirmation_without_installing_a_partial_asset(tmp_path):
    """Removing the confirmation guard must make this test fail."""
    source = tmp_path / "licensed.mp4"
    make_video(source)
    client = client_for(tmp_path)

    rejected = upload(client, "/untrusted/path/licensed.mp4", source.read_bytes(), rights_confirmed=False)

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "local_video_rights_required"
    assert PreproductionStore(tmp_path).load(PROJECT_ID)["assets"] == []
    assert list(tmp_path.rglob(".preproduction-*")) == []


@pytest.mark.parametrize("status", ["available", "requires_login", "temporarily_unavailable", "unsupported_link"])
def test_shared_video_source_contract_accepts_each_platform_adapter_state(status):
    """Future adapters must be able to return every specified source state."""
    assert validate_source_status(status) == status


def test_local_video_installs_an_immutable_source_contract_and_deduplicates_by_content(tmp_path):
    """Dropping source provenance, content hash, or duplicate handling must fail this test."""
    source = tmp_path / "licensed.mp4"
    make_video(source)
    contents = source.read_bytes()
    client = client_for(tmp_path)

    first = upload(client, "/untrusted/path/licensed.mp4", contents, rights_confirmed=True)
    assert first.status_code == 200, first.text
    asset = first.json()["assets"][0]
    assert asset["assetVersionId"] == f"{asset['id']}-v1"
    assert asset["sha256"] == hashlib.sha256(contents).hexdigest()
    source_contract = asset["videoSource"]
    assert source_contract["schemaVersion"] == 1
    assert source_contract["sourceType"] == "local"
    assert source_contract["originalReference"] == "licensed.mp4"
    assert source_contract["adapter"] == {"id": "managed-local-upload", "version": "1"}
    assert source_contract["probe"] == {"durationSeconds": pytest.approx(1, abs=0.1), "width": 64, "height": 48}
    assert source_contract["rightsConfirmation"]["confirmedAt"].endswith("Z")
    assert source_contract["contentHash"] == hashlib.sha256(contents).hexdigest()
    assert source_contract["verifiedAt"].endswith("Z")
    assert source_contract["status"] == "available"
    assert "/untrusted/path" not in str(asset)
    assert client.get(BASE + f"/assets/{asset['id']}/file").status_code == 200

    duplicate = upload(client, "renamed.mp4", contents, rights_confirmed=True)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["uploadedAssetId"] == asset["id"]
    assert len(duplicate.json()["assets"]) == 1
    assert list(tmp_path.glob(".preproduction-*")) == []

    distinct = tmp_path / "different.mp4"
    make_video(distinct, filter_name="testsrc")
    same_name = upload(client, "licensed.mp4", distinct.read_bytes(), rights_confirmed=True)
    assert same_name.status_code == 200, same_name.text
    assert same_name.json()["uploadedAssetId"] != asset["id"]
    assert len(same_name.json()["assets"]) == 2


def test_local_video_rejects_corruption_and_rolls_back_a_failed_install(tmp_path, monkeypatch):
    """Broken probe or a failed state write must leave no registered or managed asset."""
    client = client_for(tmp_path)

    broken = upload(client, "broken.mp4", b"not-a-video", rights_confirmed=True)
    assert broken.status_code == 422
    assert PreproductionStore(tmp_path).load(PROJECT_ID)["assets"] == []

    source = tmp_path / "licensed.mp4"
    make_video(source)
    original_save = PreproductionStore.save

    def fail_save(self, project_id, state):
        if project_id == PROJECT_ID:
            raise OSError("disk unavailable")
        return original_save(self, project_id, state)

    monkeypatch.setattr(PreproductionStore, "save", fail_save)
    failed = upload(client, "licensed.mp4", source.read_bytes(), rights_confirmed=True)
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "preproduction_storage_failed"
    assert PreproductionStore(tmp_path).load(PROJECT_ID)["assets"] == []
    assert list((tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets").glob("*")) == []


def test_local_video_recovery_discards_an_orphan_left_between_move_and_state_save(tmp_path):
    """A restart must remove the target if an interrupted install never registered it."""
    assets = ProjectAssets(tmp_path)
    directory = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets"
    directory.mkdir(parents=True)
    asset_id, filename = "local-video-interrupted", "local-video-interrupted.mp4"
    target = directory / filename
    target.write_bytes(b"orphan")
    marker = directory / f"local-video-{asset_id}.pending.json"
    marker.write_text(json.dumps({"version": 1, "assetId": asset_id, "file": filename}))

    assets.recover_local_video_installations(PROJECT_ID)

    assert not target.exists()
    assert not marker.exists()


def test_local_video_rejects_unmanaged_or_symlink_source_and_keeps_legacy_assets_readable(tmp_path):
    """Only the API-owned staging file is installable; legacy assets remain readable."""
    staging = tmp_path / "staging"
    staging.mkdir()
    source = staging / ".preproduction-licensed.mp4"
    make_video(source)
    linked = staging / ".preproduction-linked.mp4"
    linked.symlink_to(source)
    outside = tmp_path / "outside.mp4"
    make_video(outside)
    assets = ProjectAssets(tmp_path)

    with pytest.raises(OSError):
        assets.install_local_video(
            PROJECT_ID, source=linked, original_name="linked.mp4", role="reference",
            staging_root=staging, metadata={"duration": 1.0, "width": 64, "height": 48},
            confirmed_at="2026-10-01T00:00:00.000Z",
        )
    with pytest.raises(OSError):
        assets.install_local_video(
            PROJECT_ID, source=outside, original_name="outside.mp4", role="reference",
            staging_root=staging, metadata={"duration": 1.0, "width": 64, "height": 48},
            confirmed_at="2026-10-01T00:00:00.000Z",
        )

    store = PreproductionStore(tmp_path)
    legacy = {"id": "legacy-video", "name": "历史素材", "kind": "video", "role": "reference", "file": "legacy.mp4"}
    state = store.load(PROJECT_ID)
    state["assets"].append(legacy)
    store.save(PROJECT_ID, state)
    target = store.path(PROJECT_ID, "assets", "legacy.mp4")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())

    restored = client_for(tmp_path).get(BASE).json()["assets"]
    assert restored == [{key: value for key, value in legacy.items() if key != "file"} | {
        "url": BASE + "/assets/legacy-video/file", "available": True,
    }]
    assert "videoSource" not in restored[0]


def test_two_authorized_local_videos_are_referenced_by_stable_ids_in_batch_recommendation(tmp_path):
    """The batch-remix path must consume project asset IDs, never an original local path."""
    first_file, second_file = tmp_path / "first.mp4", tmp_path / "second.mp4"
    make_video(first_file)
    make_video(second_file, filter_name="testsrc")
    client = client_for(tmp_path, with_batch=True)
    first = upload(client, "卖点-开场.mp4", first_file.read_bytes(), rights_confirmed=True).json()
    second = upload(client, "卖点-收尾.mp4", second_file.read_bytes(), rights_confirmed=True).json()
    asset_ids = [first["uploadedAssetId"], second["uploadedAssetId"]]
    task = client.post("/api/projects/project-local-source/batch-edits", json={"sellingPoint": "展示", "script": "开场展示，再收尾。"}).json()["task"]

    proposal = client.post(
        f"/api/projects/project-local-source/batch-edits/{task['id']}/recommendations",
        json={"revision": 0, "assetIds": asset_ids},
    )

    assert proposal.status_code == 200, proposal.text
    assert {clip["assetId"] for clip in proposal.json()["proposal"]["clips"]} <= set(asset_ids)
    assert "/" not in str(proposal.json()["proposal"])
