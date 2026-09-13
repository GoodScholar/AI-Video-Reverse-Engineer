import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from threading import Event

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile

from app import main, reference_media_storage
from app.local_preprocessing import new_local_preprocessing
from app.main import create_app
from app.reference_media import ReferenceMediaError
from app.reference_video import ProbedReferenceVideo, ReferenceVideo, ReferenceVideoError
from app.reference_media_storage import managed_reference_media_path, stage_reference_media


def valid_probe(path, original_name, size_bytes, *, ffprobe_path, timeout_seconds):
    assert path.exists()
    detected_format = "mov" if original_name.lower().endswith(".mov") else "mp4"
    return ProbedReferenceVideo(
        format=detected_format,
        duration_seconds=2.5,
        width=854,
        height=480,
        frame_rate=24.0,
    )


def assert_video_error(response, status, code, message_fragment):
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert message_fragment in response.json()["detail"]["message"]


def create_project(client, name="上传测试"):
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()


def upload(client, project_id, name, content, *, headers=None):
    return client.put(
        f"/api/projects/{project_id}/reference-video",
        files={"file": (name, content, "video/mp4")},
        headers=headers,
    )


def managed_path(data_dir, project_id, reference):
    return (
        data_dir
        / "project-files"
        / project_id
        / "reference-media"
        / f"{reference['id']}.{reference['format']}"
    )


def test_staging_accepts_exact_byte_limit(tmp_path):
    upload_file = UploadFile(filename="edge.mp4", file=BytesIO(b"12345678"))

    staged = asyncio.run(
        stage_reference_media(upload_file, tmp_path, max_bytes=8, chunk_bytes=3)
    )

    assert staged.size_bytes == 8
    assert staged.path.read_bytes() == b"12345678"


def test_staging_rejects_one_byte_over_and_removes_partial_file(tmp_path):
    upload_file = UploadFile(filename="large.mp4", file=BytesIO(b"123456789"))

    with pytest.raises(ReferenceMediaError) as captured:
        asyncio.run(
            stage_reference_media(upload_file, tmp_path, max_bytes=8, chunk_bytes=3)
        )

    assert captured.value.code == "video_too_large"
    assert "9" in captured.value.message
    assert list(tmp_path.glob(".reference-*.part")) == []


def test_staging_io_failure_removes_partial_file(tmp_path, monkeypatch):
    upload_file = UploadFile(filename="write-failure.mp4", file=BytesIO(b"1234"))

    def fail_sync(_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(reference_media_storage.os, "fsync", fail_sync)

    with pytest.raises(OSError, match="disk unavailable"):
        asyncio.run(stage_reference_media(upload_file, tmp_path, max_bytes=8))

    assert list(tmp_path.glob(".reference-*.part")) == []


def test_user_uploads_video_and_reopens_project_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    first = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=16))
    project = create_project(first)

    uploaded = upload(first, project["id"], "clip.mp4", b"video-bytes")

    assert uploaded.status_code == 200
    reference = uploaded.json()["referenceMedia"]
    assert reference["originalName"] == "clip.mp4"
    assert reference["sizeBytes"] == 11
    assert reference["durationSeconds"] == 2.5
    assert uploaded.json()["updatedAt"] != project["updatedAt"]
    persisted = (tmp_path / "projects.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in persisted
    assert "project-files" not in persisted

    restarted = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=16))
    reopened = restarted.get(f"/api/projects/{project['id']}")
    assert reopened.json()["referenceMedia"] == reference
    assert managed_path(tmp_path, project["id"], reference).read_bytes() == b"video-bytes"


def test_upload_rejects_actual_bytes_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            max_reference_video_bytes=8,
            max_multipart_body_bytes=1_000_000,
        )
    )
    project = create_project(client, "体积边界")

    response = upload(client, project["id"], "clip.mp4", b"123456789")

    assert_video_error(response, 413, "video_too_large", "9")
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] is None
    assert list(tmp_path.rglob(".reference-*.part")) == []


def test_existing_project_rejects_unsupported_extension_without_file_work(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    project = create_project(client)

    response = upload(client, project["id"], "unsupported.avi", b"bytes")

    assert_video_error(response, 415, "unsupported_video_format", ".avi")
    assert not (tmp_path / "project-files").exists()


def test_missing_project_is_rejected_before_format_validation_or_file_work(tmp_path, monkeypatch):
    def probe_must_not_run(*args, **kwargs):
        raise AssertionError("不存在项目不应探测素材")

    monkeypatch.setattr(main, "probe_reference_video", probe_must_not_run)
    client = TestClient(create_app(data_dir=tmp_path))

    response = upload(client, "unknown-project", "unsupported.avi", b"bytes")

    assert_video_error(response, 404, "project_not_found", "不存在")
    assert not (tmp_path / "project-files").exists()


def test_upload_requires_named_file(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    project = create_project(client)

    response = client.put(f"/api/projects/{project['id']}/reference-video")

    assert_video_error(response, 400, "invalid_multipart", "请选择")


@pytest.mark.parametrize(
    ("content", "headers"),
    [
        (
            b"--actual\r\nContent-Disposition: form-data; name=\"file\"; filename=\"clip.mp4\"\r\n\r\nvideo\r\n--actual--\r\n",
            {"Content-Type": "multipart/form-data"},
        ),
        (
            b"--actual\r\nContent-Disposition: form-data; name=\"file\"; filename=\"clip.mp4\"\r\n\r\nvideo\r\n--actual--\r\n",
            {"Content-Type": "multipart/form-data; boundary=wrong"},
        ),
    ],
    ids=["missing-boundary", "wrong-boundary"],
)
def test_multipart_parse_errors_are_stable_video_errors(tmp_path, content, headers):
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    project = create_project(client)

    response = client.put(
        f"/api/projects/{project['id']}/reference-video",
        content=content,
        headers=headers,
    )

    assert_video_error(response, 400, "invalid_multipart", "请选择一个视频文件")


def test_text_file_field_is_a_stable_video_error(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    project = create_project(client)

    response = client.put(
        f"/api/projects/{project['id']}/reference-video",
        content=b"file=not-a-file",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert_video_error(response, 400, "invalid_multipart", "请选择一个视频文件")


def test_repeated_file_parts_are_rejected_and_all_uploads_are_closed(tmp_path, monkeypatch):
    closed_names = []
    original_close = StarletteUploadFile.close

    async def record_close(upload_file):
        closed_names.append(upload_file.filename)
        await original_close(upload_file)

    monkeypatch.setattr(StarletteUploadFile, "close", record_close)
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    project = create_project(client)

    response = client.put(
        f"/api/projects/{project['id']}/reference-video",
        files=[
            ("file", ("first.mp4", b"first", "video/mp4")),
            ("file", ("second.mp4", b"second", "video/mp4")),
        ],
    )

    assert_video_error(response, 400, "invalid_multipart", "请选择一个视频文件")
    assert set(closed_names) == {"first.mp4", "second.mp4"}


def test_successful_upload_closes_its_upload_file(tmp_path, monkeypatch):
    closed_names = []
    original_close = StarletteUploadFile.close

    async def record_close(upload_file):
        closed_names.append(upload_file.filename)
        await original_close(upload_file)

    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    monkeypatch.setattr(StarletteUploadFile, "close", record_close)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32))
    project = create_project(client)

    response = upload(client, project["id"], "clip.mp4", b"video")

    assert response.status_code == 200
    assert set(closed_names) == {"clip.mp4"}


def test_content_length_obviously_over_limit_is_rejected_before_route(tmp_path):
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            max_reference_video_bytes=8,
            max_multipart_body_bytes=8,
        )
    )
    project = create_project(client)

    response = upload(client, project["id"], "clip.mp4", b"tiny")

    assert_video_error(response, 413, "video_too_large", "200,000,000")


@pytest.mark.parametrize("content_length", ["not-a-number", "-1"])
def test_invalid_content_length_is_structured_invalid_multipart(tmp_path, content_length):
    client = TestClient(create_app(data_dir=tmp_path))
    project = create_project(client)

    response = upload(
        client,
        project["id"],
        "clip.mp4",
        b"tiny",
        headers={"Content-Length": content_length},
    )

    assert_video_error(response, 400, "invalid_multipart", "Content-Length")


def test_forged_smaller_content_length_still_uses_actual_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            max_reference_video_bytes=8,
            max_multipart_body_bytes=1_000_000,
        )
    )
    project = create_project(client)

    response = upload(
        client,
        project["id"],
        "clip.mp4",
        b"123456789",
        headers={"Content-Length": "1"},
    )

    assert_video_error(response, 413, "video_too_large", "9")


def test_probe_error_is_returned_without_replacing_existing_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32))
    project = create_project(client, "替换测试")
    first = upload(client, project["id"], "clip.mp4", b"old-video").json()
    old_reference = first["referenceMedia"]
    old_path = managed_path(tmp_path, project["id"], old_reference)

    def invalid_probe(path, original_name, size_bytes, *, ffprobe_path, timeout_seconds):
        raise ReferenceVideoError(422, "video_unreadable", "参考视频无法读取有效视频帧。")

    monkeypatch.setattr(main, "probe_reference_video", invalid_probe)
    failed = upload(client, project["id"], "broken.mp4", b"broken")

    assert_video_error(failed, 422, "video_unreadable", "有效视频帧")
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] == old_reference
    assert old_path.read_bytes() == b"old-video"
    assert list(tmp_path.rglob(".reference-*.part")) == []


def test_missing_ffprobe_path_is_returned_as_service_unavailable(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path, ffprobe_path="missing-ffprobe"))
    project = create_project(client)

    response = upload(client, project["id"], "clip.mp4", b"not-real-video")

    assert_video_error(response, 503, "ffprobe_unavailable", "ffprobe")


def test_staging_write_failure_is_structured_and_keeps_project_unchanged(tmp_path, monkeypatch):
    async def fail_stage(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(main, "stage_reference_media", fail_stage)
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    project = create_project(client)

    response = upload(client, project["id"], "clip.mp4", b"video")

    assert_video_error(response, 503, "storage_unavailable", "存储")
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] is None


def test_successful_replacement_removes_old_managed_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32))
    project = create_project(client)
    old_reference = upload(client, project["id"], "old.mp4", b"old-video").json()["referenceMedia"]
    old_path = managed_path(tmp_path, project["id"], old_reference)

    replacement = upload(client, project["id"], "new.mov", b"new-video")

    assert replacement.status_code == 200
    new_reference = replacement.json()["referenceMedia"]
    assert managed_path(tmp_path, project["id"], new_reference).read_bytes() == b"new-video"
    assert not old_path.exists()


def test_successful_replacement_clears_and_removes_old_preprocessing(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32))
    project = create_project(client)
    old_reference = upload(client, project["id"], "old.mp4", b"old-video").json()["referenceMedia"]
    preprocessing = new_local_preprocessing("preprocessing-001", old_reference["id"], "video", datetime.now(timezone.utc))
    preprocessing.status = "failed"
    current = client.get(f"/api/projects/{project['id']}").json()
    (tmp_path / "projects.json").write_text(json.dumps([{
        **current, "localPreprocessing": preprocessing.model_dump(),
    }]), encoding="utf-8")
    artifact = tmp_path / "project-files" / project["id"] / "local-preprocessing" / preprocessing.id / "sentinel.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("old-result", encoding="utf-8")

    replacement = upload(client, project["id"], "new.mp4", b"new-video")

    assert replacement.status_code == 200
    assert replacement.json()["localPreprocessing"] is None
    assert not artifact.parent.exists()


def test_replacement_metadata_failure_preserves_old_preprocessing_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32), raise_server_exceptions=False)
    project = create_project(client)
    old_reference = upload(client, project["id"], "old.mp4", b"old-video").json()["referenceMedia"]
    preprocessing = new_local_preprocessing("preprocessing-001", old_reference["id"], "video", datetime.now(timezone.utc))
    preprocessing.status = "failed"
    current = client.get(f"/api/projects/{project['id']}").json()
    (tmp_path / "projects.json").write_text(json.dumps([{
        **current, "localPreprocessing": preprocessing.model_dump(),
    }]), encoding="utf-8")
    artifact = tmp_path / "project-files" / project["id"] / "local-preprocessing" / preprocessing.id / "sentinel.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("old-result", encoding="utf-8")

    monkeypatch.setattr(main, "_write_projects", lambda *_: (_ for _ in ()).throw(OSError("write failed")))
    response = upload(client, project["id"], "new.mp4", b"new-video")

    assert_video_error(response, 503, "storage_unavailable", "存储")
    persisted = client.get(f"/api/projects/{project['id']}").json()
    assert persisted["referenceMedia"] == old_reference
    assert persisted["localPreprocessing"] == preprocessing.model_dump(mode="json")
    assert artifact.read_text(encoding="utf-8") == "old-result"


def test_preprocessing_cleanup_failure_does_not_rollback_new_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32))
    project = create_project(client)
    old_reference = upload(client, project["id"], "old.mp4", b"old-video").json()["referenceMedia"]
    preprocessing = new_local_preprocessing("preprocessing-001", old_reference["id"], "video", datetime.now(timezone.utc))
    preprocessing.status = "failed"
    current = client.get(f"/api/projects/{project['id']}").json()
    (tmp_path / "projects.json").write_text(json.dumps([{
        **current, "localPreprocessing": preprocessing.model_dump(),
    }]), encoding="utf-8")
    monkeypatch.setattr(main, "discard_preprocessing", lambda *_: (_ for _ in ()).throw(OSError("cleanup failed")))

    replacement = upload(client, project["id"], "new.mp4", b"new-video")

    assert replacement.status_code == 200
    assert replacement.json()["referenceMedia"] != old_reference
    assert replacement.json()["localPreprocessing"] is None


def test_replacement_commit_rechecks_preprocessing_started_during_probe(tmp_path, monkeypatch):
    class HoldingQueue:
        def __init__(self, handler):
            self.handler = handler
            self.pending = []

        def submit(self, project_id):
            self.pending.append(project_id)
            return True

        def is_active(self, project_id):
            return project_id in self.pending

        def shutdown(self):
            return None

    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    app = create_app(
        data_dir=tmp_path, max_reference_video_bytes=32, preprocessing_queue_factory=HoldingQueue,
    )
    client = TestClient(app)
    project = create_project(client)
    old_reference = upload(client, project["id"], "old.mp4", b"old-video").json()["referenceMedia"]
    old_path = managed_path(tmp_path, project["id"], old_reference)
    probe_started = Event()
    release_probe = Event()

    def blocked_probe(path, original_name, size_bytes, *, ffprobe_path, timeout_seconds):
        probe_started.set()
        assert release_probe.wait(timeout=2)
        return valid_probe(path, original_name, size_bytes, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(main, "probe_reference_video", blocked_probe)
    with ThreadPoolExecutor(max_workers=1) as executor:
        replacement = executor.submit(upload, TestClient(app), project["id"], "new.mp4", b"new-video")
        assert probe_started.wait(timeout=2)
        started = client.post(f"/api/projects/{project['id']}/local-preprocessing")
        task = started.json()["localPreprocessing"]
        artifact = tmp_path / "project-files" / project["id"] / "local-preprocessing" / task["id"] / "sentinel.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("keep", encoding="utf-8")
        release_probe.set()
        response = replacement.result(timeout=2)

    assert_video_error(response, 409, "preprocessing_in_progress", "完成后再更换")
    current = client.get(f"/api/projects/{project['id']}").json()
    assert current["referenceMedia"] == old_reference
    assert current["localPreprocessing"] == task
    assert old_path.read_bytes() == b"old-video"
    assert artifact.read_text(encoding="utf-8") == "keep"


def test_metadata_write_failure_removes_new_file_and_keeps_old_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32), raise_server_exceptions=False)
    project = create_project(client)
    old_reference = upload(client, project["id"], "old.mp4", b"old-video").json()["referenceMedia"]
    old_path = managed_path(tmp_path, project["id"], old_reference)
    original_write = main._write_projects
    calls = 0

    def fail_replacement_write(data_dir, projects):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("metadata unavailable")
        return original_write(data_dir, projects)

    monkeypatch.setattr(main, "_write_projects", fail_replacement_write)
    failed = upload(client, project["id"], "new.mp4", b"new-video")

    assert_video_error(failed, 503, "storage_unavailable", "存储")
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] == old_reference
    assert old_path.read_bytes() == b"old-video"
    assert sorted(path.name for path in old_path.parent.iterdir()) == [old_path.name]


def test_partial_promote_failure_removes_new_file_and_keeps_old_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32), raise_server_exceptions=False)
    project = create_project(client)
    old_reference = upload(client, project["id"], "old.mp4", b"old-video").json()["referenceMedia"]
    old_path = managed_path(tmp_path, project["id"], old_reference)

    def promote_then_fail(staged_path, final_path):
        final_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.replace(final_path)
        raise OSError("promotion failed after move")

    monkeypatch.setattr(main, "promote_staged_reference_media", promote_then_fail)
    failed = upload(client, project["id"], "new.mp4", b"new-video")

    assert_video_error(failed, 503, "storage_unavailable", "存储")
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] == old_reference
    assert old_path.read_bytes() == b"old-video"
    assert sorted(path.name for path in old_path.parent.iterdir()) == [old_path.name]
    assert list(tmp_path.rglob(".reference-*.part")) == []


def test_concurrent_replacements_leave_only_json_referenced_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    app = create_app(data_dir=tmp_path, max_reference_video_bytes=32)
    client = TestClient(app)
    project = create_project(client)

    def replace(contents):
        return upload(TestClient(app), project["id"], "clip.mp4", contents)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(replace, [b"one-video", b"two-video"]))

    assert [response.status_code for response in responses] == [200, 200]
    reference = TestClient(app).get(f"/api/projects/{project['id']}").json()["referenceMedia"]
    current_path = managed_path(tmp_path, project["id"], reference)
    assert current_path.read_bytes() in {b"one-video", b"two-video"}
    assert {path.name for path in current_path.parent.iterdir()} == {current_path.name}


def test_uploads_to_different_projects_remain_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    app = create_app(data_dir=tmp_path, max_reference_video_bytes=32)
    client = TestClient(app)
    first = create_project(client, "项目一")
    second = create_project(client, "项目二")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda args: upload(TestClient(app), *args),
                [(first["id"], "one.mp4", b"one-video"), (second["id"], "two.mp4", b"two-video")],
            )
        )

    assert [response.status_code for response in responses] == [200, 200]
    for project, contents in [(first, b"one-video"), (second, b"two-video")]:
        reference = TestClient(app).get(f"/api/projects/{project['id']}").json()["referenceMedia"]
        assert managed_path(tmp_path, project["id"], reference).read_bytes() == contents


def test_original_name_is_basename_and_managed_path_uses_server_id(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32))
    project = create_project(client)

    response = upload(client, project["id"], "../../private.mp4", b"video")

    assert response.status_code == 200
    reference = response.json()["referenceMedia"]
    assert reference["originalName"] == "private.mp4"
    assert managed_path(tmp_path, project["id"], reference).read_bytes() == b"video"


def test_windows_style_upload_name_is_normalized_and_never_persisted_as_a_source_path(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32))
    project = create_project(client)

    response = upload(client, project["id"], r"C:\\recordings\\clip.mp4", b"video")

    assert response.status_code == 200
    assert response.json()["referenceMedia"]["originalName"] == "clip.mp4"
    assert r"C:\\recordings" not in (tmp_path / "projects.json").read_text(encoding="utf-8")


def test_managed_path_rejects_a_parent_symlink_that_escapes_data_directory(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "project-files").symlink_to(outside, target_is_directory=True)
    video = ReferenceVideo(
        id="video-001",
        originalName="clip.mp4",
        format="mp4",
        sizeBytes=11,
        durationSeconds=2.5,
        width=854,
        height=480,
        frameRate=24,
    )

    with pytest.raises(OSError):
        managed_reference_media_path(tmp_path, "project-001", video)


def test_unsafe_persisted_id_cannot_delete_an_external_sentinel(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside" / "reference-videos"
    outside.mkdir(parents=True)
    sentinel = outside / "old.mp4"
    sentinel.write_bytes(b"do-not-delete")
    unsafe_video = ReferenceVideo.model_construct(
        id="old", originalName="old.mp4", format="mp4", sizeBytes=11,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )

    with pytest.raises(ValueError):
        managed_reference_media_path(data_dir, "../../outside", unsafe_video)

    assert sentinel.read_bytes() == b"do-not-delete"


def test_old_file_delete_failure_keeps_the_new_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32))
    project = create_project(client)
    old_reference = upload(client, project["id"], "old.mp4", b"old-video").json()["referenceMedia"]
    old_path = managed_path(tmp_path, project["id"], old_reference)
    original_discard = main.discard_managed_file

    def fail_only_old(path):
        if path == old_path:
            raise OSError("old file busy")
        original_discard(path)

    monkeypatch.setattr(main, "discard_managed_file", fail_only_old)
    replacement = upload(client, project["id"], "new.mp4", b"new-video")

    assert replacement.status_code == 200
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] == replacement.json()["referenceMedia"]
    assert managed_path(tmp_path, project["id"], replacement.json()["referenceMedia"]).read_bytes() == b"new-video"
    assert old_path.read_bytes() == b"old-video"
