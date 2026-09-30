"""Task 2.3: real libass pixels and durable packaging API evidence.

The evidence directory is deliberately stable: it is a review artifact, not a
pytest temporary directory.  Every assertion below observes rendered pixels or
the durable HTTP lifecycle; an ASS string alone is never accepted as proof.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.batch_editing_api import create_batch_editing_router
from app.montage_packaging import PackagingError, compile_ass, default_packaging, private_font_path
from app.timeline_render import TimelineRenderError, render_timeline


FFMPEG = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
EVIDENCE = Path("/tmp/aivre-ticket05-evidence/task23")
pytestmark = pytest.mark.skipif(not (FFMPEG.is_file() and FFPROBE.is_file()), reason="Task2.3 需要 ffmpeg-full/libass")


def _run(*command: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(value) for value in command], check=True, capture_output=True, text=True)


def _evidence_dir(name: str) -> Path:
    directory = EVIDENCE / name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _source(path: Path, *, color: str = "0x172554", duration: float = 5.0) -> Path:
    _run(FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c={color}:s=320x180:r=30:d={duration}", "-f", "lavfi", "-i",
         f"sine=frequency=440:sample_rate=48000:duration={duration}", "-shortest", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", path)
    return path


def _tracks(duration: float = 5.0) -> list[dict]:
    return [{"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": [
        {"id": "clip", "assetId": "shot", "start": 0, "inPoint": 0, "duration": duration,
         "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0},
    ]}]


def _timeline(tracks: list[dict], *, duration: float = 5.0) -> dict:
    return {"settings": {"width": 720, "height": 1280, "fps": 30}, "tracks": tracks, "transitions": []}


def _package() -> dict:
    package = default_packaging()
    # Each event has a distinct sample time, style and placement.  The title
    # carries real newline plus literal ASS controls; the compiler must show
    # them as text, never execute them.
    package["elements"] = [
        {"id": "title", "kind": "title", "enabled": True, "start": .20, "end": .85,
         "text": "新品\\N{安全}\n真换行", "position": "top", "style": "emphasis"},
        {"id": "selling", "kind": "selling-point", "enabled": True, "start": 1.10, "end": 1.75,
         "text": "中文卖点", "position": "middle", "style": "banner"},
        {"id": "keyword", "kind": "keyword", "enabled": True, "start": 2.00, "end": 2.65,
         "text": "关键词", "position": "top", "style": "emphasis"},
        {"id": "cta", "kind": "cta", "enabled": True, "start": 2.90, "end": 3.55,
         "text": "立即咨询", "position": "bottom", "style": "banner"},
        {"id": "subtitle", "kind": "subtitle", "enabled": True, "start": 3.80, "end": 4.45,
         "text": "字幕正本", "position": "bottom", "style": "plain"},
    ]
    return package


def _frame(video: Path, second: float, destination: Path) -> Path:
    _run(FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", str(second), "-i", video,
         "-frames:v", "1", destination)
    return destination


def _difference_bbox(rendered: Path, control: Path) -> tuple[list[int] | None, tuple[int, int]]:
    with Image.open(rendered).convert("RGB") as actual, Image.open(control).convert("RGB") as baseline:
        points = [(x, y) for y in range(actual.height) for x in range(actual.width)
                  if sum(abs(left - right) for left, right in zip(actual.getpixel((x, y)), baseline.getpixel((x, y)))) > 12]
        if not points:
            return None, actual.size
        return [min(x for x, _y in points), min(y for _x, y in points),
                max(x for x, _y in points) + 1, max(y for _x, y in points) + 1], actual.size


def _normal(box: list[int], size: tuple[int, int]) -> list[float]:
    return [box[0] / size[0], box[1] / size[1], box[2] / size[0], box[3] / size[1]]


def test_real_five_element_pixel_timing_safe_area_and_preview_export_matrix():
    directory = _evidence_dir("five-elements")
    source = _source(directory / "source.mp4")
    timeline, package = _timeline(_tracks()), _package()
    ass = compile_ass(package, [], timeline["tracks"], 720, 1280, private_font_path())
    (directory / "packaging.ass").write_text(ass, encoding="utf-8")
    assert "\\\u2060N" in ass and "\\{" in ass and "\\}" in ass and "\\N真换行" in ass

    preview, exported = directory / "preview.mp4", directory / "export.mp4"
    preview_control, export_control = directory / "preview-control.mp4", directory / "export-control.mp4"
    render_timeline(timeline, {"shot": source}, preview, format="preview", packaging=package, packaging_cues=[],
                    ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))
    render_timeline(timeline, {"shot": source}, exported, format="mp4", packaging=package, packaging_cues=[],
                    ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))
    render_timeline(timeline, {"shot": source}, preview_control, format="preview", ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))
    render_timeline(timeline, {"shot": source}, export_control, format="mp4", ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))

    samples = {"title": (.50, "top"), "selling-point": (1.40, "middle"), "keyword": (2.30, "top"),
               "cta": (3.20, "bottom"), "subtitle": (4.10, "bottom")}
    matrix: dict[str, object] = {"thresholds": {"bboxNormalizedDelta": .015, "safeArea": [.05, .95], "pixelDifference": 12}, "elements": {}}
    for kind, (second, position) in samples.items():
        preview_frame, export_frame, control_frame = (_frame(preview, second, directory / f"preview-{kind}.png"),
            _frame(exported, second, directory / f"export-{kind}.png"), _frame(preview_control, second, directory / f"preview-control-{kind}.png"))
        export_control_frame = _frame(export_control, second, directory / f"export-control-{kind}.png")
        preview_box, preview_size = _difference_bbox(preview_frame, control_frame)
        export_box, export_size = _difference_bbox(export_frame, export_control_frame)
        assert preview_box and export_box, kind
        preview_normal, export_normal = _normal(preview_box, preview_size), _normal(export_box, export_size)
        assert max(abs(left - right) for left, right in zip(preview_normal, export_normal)) < .015
        assert .05 <= export_normal[0] < export_normal[2] <= .95 and .05 <= export_normal[1] < export_normal[3] <= .95
        center_y = (export_normal[1] + export_normal[3]) / 2
        assert (center_y < .30 if position == "top" else .30 < center_y < .70 if position == "middle" else center_y > .70)
        matrix["elements"][kind] = {"sample": second, "preview": preview_normal, "export": export_normal,
                                     "maxDelta": max(abs(left - right) for left, right in zip(preview_normal, export_normal))}
    for second in (.05, .95, 1.90, 2.80, 3.70, 4.65):
        packaged, baseline = _frame(exported, second, directory / f"outside-{second}.png"), _frame(export_control, second, directory / f"control-outside-{second}.png")
        assert _difference_bbox(packaged, baseline)[0] is None
    (directory / "pixel-matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    probe = _run(FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", exported)
    (directory / "export-ffprobe.json").write_text(probe.stdout, encoding="utf-8")
    # `fontsdir=.` is evaluated from the controlled directory, avoiding an
    # FFmpeg filter-parser path quoting test rather than changing font scope.
    fontselect = subprocess.run([str(FFMPEG), "-hide_banner", "-nostdin", "-v", "verbose", "-i", str(source), "-vf",
                                 f"ass=filename={directory / 'packaging.ass'}:fontsdir=.", "-ss", "0.5", "-frames:v", "1", "-f", "null", "-"],
                                cwd=private_font_path().parent, check=True, capture_output=True, text=True)
    (directory / "fontselect.log").write_text(fontselect.stderr, encoding="utf-8")
    assert re.search(r"fontselect: \(NotoSansCJKsc-Regular, \d+, 0\) -> NotoSansCJKsc-Regular, 0, NotoSansCJKsc-Regular", fontselect.stderr)


def test_real_api_lifecycle_renders_downloads_stales_and_regenerates():
    directory = _evidence_dir("api-lifecycle")
    project_id, jobs = "task23", []
    root = directory / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True, exist_ok=True)
    source = _source(root / "assets" / "shot.mp4", duration=2)
    shutil.copy2(source, root / "assets" / "shot-two.mp4")
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "shot", "name": "真实镜头", "kind": "video", "file": "shot.mp4", "duration": 2},
                   {"id": "shot-two", "name": "真实镜头二", "kind": "video", "file": "shot-two.mp4", "duration": 2}]}), encoding="utf-8")

    class Queue:
        def submit(self, kind, pid, handler):
            jobs.append((kind, pid, handler)); return True
    app = FastAPI()
    app.include_router(create_batch_editing_router(directory, lambda _: SimpleNamespace(id=project_id), Queue(),
                       ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE)))
    base = f"/api/projects/{project_id}/batch-edits"
    with TestClient(app) as api:
        parent = api.post(base, json={"sellingPoint": "真实卖点", "script": "真实包装"}).json()["task"]
        bulk = api.post(f"{base}/{parent['id']}/variants/bulk", json={
            "items": [{"sellingPoint": f"真实卖点{index}", "script": f"真实包装{index}"} for index in range(5)],
            "assetIds": ["shot", "shot-two"],
        })
        assert bulk.status_code == 201, bulk.text
        task = bulk.json()["tasks"][0]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "clip", "assetId": "shot", "start": 0, "inPoint": 0, "duration": 2,
                                "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        assert api.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}).status_code == 200
        cues = [{"id": "cue-1", "start": 1.1, "end": 1.55, "text": "字幕正本"}]
        assert api.put(f"{base}/{task['id']}/subtitles", json={"revision": 0, "timelineRevision": 1, "cues": cues}).status_code == 200
        package = default_packaging()
        package["elements"] = [
            {"id": "title", "kind": "title", "enabled": True, "start": .15, "end": .75,
             "text": "真实包装", "position": "top", "style": "emphasis"},
            {"id": "cta", "kind": "cta", "enabled": True, "start": .8, "end": 1.0,
             "text": "立即咨询", "position": "middle", "style": "banner"},
            {"id": "subtitle", "kind": "subtitle", "enabled": True, "cueId": "cue-1", "position": "bottom", "style": "plain"},
        ]
        saved = api.put(f"{base}/{task['id']}/variant", json={"revision": 1, "tracks": tracks, "packaging": package})
        assert saved.status_code == 200, saved.text

        def drain(expected_kind: str):
            kind, pid, handler = jobs.pop(0)
            assert kind == expected_kind
            handler(pid)

        preview = api.post(f"{base}/{task['id']}/variant/previews", json={"revision": 2})
        assert preview.status_code == 202, preview.text
        preview_id = preview.json()["variant"]["runs"][-1]["id"]
        drain("batch-preview:" + preview_id)
        preview_download = api.get(f"{base}/{task['id']}/variant/previews/{preview_id}/output")
        assert preview_download.status_code == 200 and preview_download.content[:8]
        (directory / "first-preview.mp4").write_bytes(preview_download.content)
        assert api.post(f"{base}/{task['id']}/reviews", json={"runId": preview_id, "decision": "approved", "reason": "真实审核"}).status_code == 200
        delivery = api.post(f"{base}/{parent['id']}/exports", json={"taskIds": [task["id"]]})
        assert delivery.status_code == 202, delivery.text
        export_id = delivery.json()["results"][0]["runId"]
        drain("batch-export:" + export_id)
        delivered = api.get(f"{base}/{task['id']}/exports/{export_id}/output")
        assert delivered.status_code == 200 and delivered.content[:8]
        (directory / "first-export.mp4").write_bytes(delivered.content)

        changed = api.put(f"{base}/{task['id']}/variant", json={"revision": 2, "tracks": tracks,
                          "packaging": {**package, "elements": [{**item, "text": "立即领取"} if item["id"] == "cta" else item for item in package["elements"]]}})
        assert changed.status_code == 200, changed.text
        assert api.get(f"{base}/{task['id']}/variant/previews/{preview_id}/output").status_code == 409
        assert api.get(f"{base}/{task['id']}/exports/{export_id}/output").status_code == 409

        refreshed = api.post(f"{base}/{task['id']}/variant/previews", json={"revision": 3})
        assert refreshed.status_code == 202, refreshed.text
        refreshed_id = refreshed.json()["variant"]["runs"][-1]["id"]
        drain("batch-preview:" + refreshed_id)
        assert api.post(f"{base}/{task['id']}/reviews", json={"runId": refreshed_id, "decision": "approved", "reason": "重新审核"}).status_code == 200
        regenerated = api.post(f"{base}/{parent['id']}/exports", json={"taskIds": [task["id"]]})
        assert regenerated.status_code == 202, regenerated.text
        regenerated_id = regenerated.json()["results"][0]["runId"]
        drain("batch-export:" + regenerated_id)
        assert api.get(f"{base}/{task['id']}/exports/{regenerated_id}/output").status_code == 200
    (directory / "lifecycle.json").write_text(json.dumps({"firstPreview": preview_id, "firstExport": export_id,
        "regeneratedPreview": refreshed_id, "regeneratedExport": regenerated_id}, indent=2), encoding="utf-8")


def test_packaging_failure_matrix_rejects_before_fallback_and_cleans_temporary_files(monkeypatch):
    directory = _evidence_dir("failure-matrix")
    source = _source(directory / "source.mp4", duration=1)
    timeline, package = _timeline(_tracks(1), duration=1), _package()
    package["elements"] = [{"id": "title", "kind": "title", "enabled": True, "start": 0, "end": .8,
                            "text": "注入{\\pos(1,1)}\\N", "position": "top", "style": "plain"}]
    ass = compile_ass(package, [], [], 720, 1280, private_font_path())
    (directory / "literal-injection.ass").write_text(ass, encoding="utf-8")
    assert "\\{\\\u2060pos(1,1)\\}" in ass and "\\\u2060N" in ass
    with pytest.raises(PackagingError, match="模板"):
        compile_ass({**package, "templateId": "unknown"}, [], [], 720, 1280, private_font_path())
    with pytest.raises(PackagingError, match="缺少"):
        compile_ass({**package, "elements": [{**package["elements"][0], "text": "𠀀"}]}, [], [], 720, 1280, private_font_path())
    import app.montage_packaging as packaging_module
    monkeypatch.setattr(packaging_module, "FONT_SHA256", "00" * 32)
    with pytest.raises(PackagingError, match="字体"):
        private_font_path()
    monkeypatch.undo()
    output = directory / "cancelled.mp4"
    with pytest.raises(TimelineRenderError, match="取消"):
        render_timeline(timeline, {"shot": source}, output, format="mp4", packaging=package,
                        ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE), cancelled=lambda: True)
    assert not output.exists() and not (directory / ".packaging.ass").exists()
    (directory / "failure-matrix.json").write_text(json.dumps({"missingFont": "rejected", "missingGlyph": "rejected",
        "unknownTemplate": "rejected", "literalInjection": "escaped", "cancel": "cleaned"}, indent=2), encoding="utf-8")
