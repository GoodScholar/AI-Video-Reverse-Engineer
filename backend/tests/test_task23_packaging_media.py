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
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.batch_editing_api import create_batch_editing_router
from app.montage_packaging import PackagingError, compile_ass, default_packaging, private_font_path
from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, pending_reframe, source_fingerprint
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


def _commands(directory: Path, text: str) -> None:
    """Keep replay instructions beside durable media instead of pytest's tmpdir."""
    (directory / "commands.txt").write_text(
        "PYTHONPATH='/Users/shen/.codex/worktrees/montage-transitions/AI Video Reverse Engineer/backend' "
        "'/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/.venv/bin/python' -m pytest "
        "backend/tests/test_task23_packaging_media.py -q\n"
        f"ffmpeg={FFMPEG} -hide_banner -nostdin -v error -y; ffprobe={FFPROBE}\n"
        f"stable-input-output-directory={directory}\n{text}\n", encoding="utf-8")


def _source(path: Path, *, color: str = "0x172554", duration: float = 5.0) -> Path:
    _run(FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c={color}:s=320x180:r=30:d={duration}", "-f", "lavfi", "-i",
         f"sine=frequency=440:sample_rate=48000:duration={duration}", "-shortest", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", path)
    return path


def _moving_subject_source(path: Path, *, colour: str, moving: bool, y: int) -> Path:
    x = "20+80*t" if moving else "160"
    _run(FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=0x101820:s=320x180:r=30:d=2.2", "-f", "lavfi", "-i",
         f"color={colour}:s=30x70:r=30:d=2.2", "-filter_complex",
         f"[0:v][1:v]overlay=x='{x}':y={y}:eval=frame", "-c:v", "libx264", "-pix_fmt", "yuv420p", path)
    return path


def _manual_subject_reframe(source: Path, *, moving: bool, colour: str, y: int) -> dict:
    def box(time: float) -> dict:
        x = (20 + 80 * time if moving else 160) / 320
        return {"x": x, "y": y / 180, "width": 30 / 320, "height": 70 / 180,
                "label": colour + "-subject", "confidence": .99}
    centres = ((.11, .55), (.44, .55)) if moving else ((.55, .55), (.55, .55))
    return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 320, "sourceHeight": 180,
            "targetAspect": {"width": 720, "height": 1280}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
            "samples": [{"timeSeconds": point, "boxes": [box(point)], "center": {"x": center[0], "y": center[1]}, "confidence": .99}
                        for point, center in zip((0.0, 1.3), centres)],
            "frameDurationSeconds": .04, "uncertaintyReasons": ["实际色块主体已人工确认轨迹"], "status": "review_required",
            "override": {"mode": "manual", "keyframes": [{"timeSeconds": point, "x": center[0], "y": center[1]}
                                                          for point, center in zip((0.0, 1.3), centres)]}}


def _colour_bbox(frame: Path, colour: str) -> list[int] | None:
    with Image.open(frame).convert("RGB") as image:
        points = [(x, y) for y in range(image.height) for x in range(image.width)
                  if (image.getpixel((x, y))[0] > image.getpixel((x, y))[2] + 35 if colour == "red"
                      else image.getpixel((x, y))[2] > image.getpixel((x, y))[0] + 35)]
        if not points:
            return None
        return [min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points) + 1, max(y for _, y in points) + 1]


def _literal_text_bbox(rendered: Path, control: Path) -> list[int] | None:
    """White ASS glyphs are separable from red/blue real subjects and encoder noise."""
    with Image.open(rendered).convert("RGB") as actual, Image.open(control).convert("RGB") as baseline:
        points = [(x, y) for y in range(actual.height) for x in range(actual.width)
                  if min(actual.getpixel((x, y))) > 210 and min(baseline.getpixel((x, y))) <= 210]
        if not points:
            return None
        return [min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points) + 1, max(y for _, y in points) + 1]


def _disjoint(left: list[int], right: list[int]) -> bool:
    return left[2] <= right[0] or right[2] <= left[0] or left[3] <= right[1] or right[3] <= left[1]


def _tracks(duration: float = 5.0) -> list[dict]:
    reframe = pending_reframe(target_width=720, target_height=1280, reason="真实包装证据显式采用中心构图")
    reframe["override"] = {"mode": "center", "keyframes": []}
    return [{"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": [
        {"id": "clip", "assetId": "shot", "start": 0, "inPoint": 0, "duration": duration,
         "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0, "reframe": reframe},
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
    # Decode-to-seek keeps the packaged/control frame index aligned for a
    # moving reframe; input seeking can choose different preceding GOP frames.
    _run(FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", video, "-ss", str(second),
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


def _diff_metrics(rendered: Path, control: Path) -> dict[str, object]:
    """Actual ASS-vs-control pixels, normalized for preview/export codec size."""
    with Image.open(rendered).convert("RGB") as actual, Image.open(control).convert("RGB") as baseline:
        changed, bins, channels = 0, set(), [0, 0, 0]
        histogram = [0] * 24
        dark = 0
        for y in range(actual.height):
            for x in range(actual.width):
                pixel, reference = actual.getpixel((x, y)), baseline.getpixel((x, y))
                if sum(abs(left - right) for left, right in zip(pixel, reference)) <= 12:
                    continue
                changed += 1
                bins.add((x * 64 // actual.width, y * 64 // actual.height))
                for index, value in enumerate(pixel):
                    channels[index] += value
                    histogram[index * 8 + value * 8 // 256] += 1
                if max(pixel) < 105:
                    dark += 1
        assert changed
        return {"changedPixels": changed, "normalizedArea": changed / (actual.width * actual.height),
                "maskBins": sorted([list(item) for item in bins]),
                "meanRgb": [value / changed for value in channels],
                "histogram": [value / (changed * 3) for value in histogram], "darkChangedPixels": dark}


def _iou(left: list[list[int]], right: list[list[int]]) -> float:
    first, second = {tuple(item) for item in left}, {tuple(item) for item in right}
    return len(first & second) / len(first | second) if first or second else 1.0


def _normal(box: list[int], size: tuple[int, int]) -> list[float]:
    return [box[0] / size[0], box[1] / size[1], box[2] / size[0], box[3] / size[1]]


def test_real_five_element_pixel_timing_safe_area_and_preview_export_matrix():
    directory = _evidence_dir("five-elements")
    _commands(directory, "render_timeline: ffmpeg-full/libass, packaged and same-source unpackaged preview/mp4 controls; extract PNG frames with ffmpeg-full")
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


def test_real_moving_subject_reframe_and_transition_keep_actual_text_pixels_clear(monkeypatch):
    """The actual coloured subjects, not only compiler occupancy, stay clear of ASS pixels."""
    directory = _evidence_dir("moving-subject-transition")
    _commands(directory, "render_timeline: two real 320x180 moving-colour sources + manual retained reframe trajectories + dissolve overlap; packaged/unpackaged pixel controls")
    red, blue = _moving_subject_source(directory / "red-moving.mp4", colour="red", moving=True, y=95), _moving_subject_source(directory / "blue-subject.mp4", colour="blue", moving=False, y=105)
    left = {"id": "red-clip", "assetId": "red", "start": 0, "inPoint": 0, "duration": 1.3, "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0,
            "reframe": _manual_subject_reframe(red, moving=True, colour="red", y=95)}
    right = {"id": "blue-clip", "assetId": "blue", "start": .9, "inPoint": 0, "duration": 1.3, "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0,
             "reframe": _manual_subject_reframe(blue, moving=False, colour="blue", y=105)}
    tracks = [{"id": "video", "name": "真实主体", "kind": "video", "muted": False, "hidden": False, "clips": [left, right]}]
    timeline = {"settings": {"width": 720, "height": 1280, "fps": 30}, "tracks": tracks,
                "transitions": [{"id": "dissolve", "leftClipId": "red-clip", "rightClipId": "blue-clip", "template": "dissolve", "enabled": True,
                                 "duration": .4, "overlap": .4, "audioCrossfade": False, "reason": "真实主体转场"}]}
    package = default_packaging()
    package["elements"] = [{"id": "title", "kind": "title", "enabled": True, "start": .95, "end": 1.28,
                            "text": "转场不遮主体", "position": "auto", "style": "banner"}]
    packaged, control = directory / "packaged.mp4", directory / "control.mp4"
    render_timeline(timeline, {"red": red, "blue": blue}, packaged, format="mp4", packaging=package,
                    ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))
    render_timeline(timeline, {"red": red, "blue": blue}, control, format="mp4", ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))
    ass = compile_ass(package, [], tracks, 720, 1280, private_font_path())
    (directory / "packaging.ass").write_text(ass, encoding="utf-8")
    matrix = {"times": {}, "rule": "actual colour subject bbox ∩ ASS pixel-diff bbox = empty"}
    for second in (.99, 1.10, 1.23):
        actual, baseline = _frame(packaged, second, directory / f"packaged-{second}.png"), _frame(control, second, directory / f"control-{second}.png")
        text = _literal_text_bbox(actual, baseline)
        assert text, second
        subjects = {colour: box for colour in ("red", "blue") if (box := _colour_bbox(baseline, colour)) is not None}
        assert subjects, second
        assert all(_disjoint(text, box) for box in subjects.values()), (second, text, subjects)
        if 1.02 <= second <= 1.25:
            assert set(subjects) == {"red", "blue"}, subjects
        matrix["times"][str(second)] = {"text": text, "subjects": subjects}
    # The same production entry rejects full-frame occupancy before it can run ffmpeg.
    blocked_tracks = json.loads(json.dumps(tracks))
    blocked_tracks[0]["clips"] = [blocked_tracks[0]["clips"][0]]
    observed = deepcopy(blocked_tracks[0]["clips"][0]["reframe"]["samples"][0])
    observed["timeSeconds"] = 1.1
    observed["boxes"][0].update({"x": 0, "y": 0, "width": 1, "height": 1})
    blocked_tracks[0]["clips"][0]["reframe"]["samples"] = [observed]
    blocked = {"settings": timeline["settings"], "tracks": blocked_tracks, "transitions": []}
    calls = []
    monkeypatch.setattr("app.timeline_render._run_ffmpeg", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(TimelineRenderError, match="无可用安全区"):
        render_timeline(blocked, {"red": red}, directory / "blocked.mp4", format="mp4", packaging=package,
                        ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))
    assert not calls and not (directory / "blocked.mp4").exists()
    matrix["noSpace"] = {"stage": "pre-ffmpeg", "ffmpegCalls": len(calls), "output": False}
    (directory / "subject-transition-matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")


def test_real_api_lifecycle_renders_downloads_stales_and_regenerates():
    directory = _evidence_dir("api-lifecycle")
    _commands(directory, "HTTP: save packaging+cues -> queued real preview -> download -> approve -> queued real export -> download -> mutate -> stale download -> regenerate")
    project_id, jobs = "task23", []
    root = directory / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True, exist_ok=True)
    source = _source(root / "assets" / "shot.mp4", duration=5)
    shutil.copy2(source, root / "assets" / "shot-two.mp4")
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "shot", "name": "真实镜头", "kind": "video", "file": "shot.mp4", "duration": 5},
                   {"id": "shot-two", "name": "真实镜头二", "kind": "video", "file": "shot-two.mp4", "duration": 5}]}), encoding="utf-8")

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
        tracks[0]["clips"] = [{"id": "clip", "assetId": "shot", "start": 0, "inPoint": 0, "duration": 5,
                                "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        assert api.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}).status_code == 200
        analysis = api.post(f"{base}/{task['id']}/variant/reframes/clip/analyze", json={"revision": 1})
        assert analysis.status_code == 202, analysis.text
        cancelled = api.post(f"{base}/{task['id']}/variant/reframes/clip/cancel", json={"revision": 1})
        assert cancelled.status_code == 200, cancelled.text
        kind, pid, handler = jobs.pop(0)
        assert kind.startswith("batch-reframe:")
        handler(pid)
        tracks = cancelled.json()["variant"]["tracks"]
        tracks[0]["clips"][0]["reframe"]["override"] = {"mode": "center", "keyframes": []}
        centered = api.put(f"{base}/{task['id']}/variant", json={"revision": 1, "tracks": tracks})
        assert centered.status_code == 200, centered.text
        tracks = centered.json()["variant"]["tracks"]
        cues = [{"id": "cue-1", "start": 3.8, "end": 4.45, "text": "字幕正本"}]
        assert api.put(f"{base}/{task['id']}/subtitles", json={"revision": 0, "timelineRevision": 2, "cues": cues}).status_code == 200
        package = _package()
        package["elements"][-1] = {"id": "subtitle", "kind": "subtitle", "enabled": True,
                                    "cueId": "cue-1", "position": "bottom", "style": "plain"}
        saved = api.put(f"{base}/{task['id']}/variant", json={"revision": 2, "tracks": tracks, "packaging": package})
        assert saved.status_code == 200, saved.text

        def drain(expected_kind: str):
            kind, pid, handler = jobs.pop(0)
            assert kind == expected_kind
            handler(pid)

        preview = api.post(f"{base}/{task['id']}/variant/previews", json={"revision": 3})
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
        api_preview, api_export = directory / "first-preview.mp4", directory / "first-export.mp4"
        # The response is a public frozen API snapshot.  These two controls are
        # rendered only after the HTTP runs have completed, solely to measure
        # their pixels; they never stand in for preview/export lifecycle work.
        frozen = saved.json()["variant"]
        control_timeline = {"settings": frozen["settings"], "tracks": tracks, "transitions": frozen.get("transitions", [])}
        preview_control, export_control = directory / "frozen-preview-control.mp4", directory / "frozen-export-control.mp4"
        render_timeline(control_timeline, {"shot": source}, preview_control, format="preview", ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))
        render_timeline(control_timeline, {"shot": source}, export_control, format="mp4", ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))
        api_matrix = {"thresholds": {"eventOutside": "null", "previewExportBBoxDelta": .02, "pixelDifference": 12,
                                      "normalizedAreaDelta": .012, "maskGridIoU": .45, "meanRgbDelta": 35, "histogramL1": .50}, "elements": {}}
        for kind, second in {"title": .5, "selling-point": 1.4, "keyword": 2.3, "cta": 3.2, "subtitle": 4.1}.items():
            pframe, eframe = _frame(api_preview, second, directory / f"api-preview-{kind}.png"), _frame(api_export, second, directory / f"api-export-{kind}.png")
            pcontrol, econtrol = _frame(preview_control, second, directory / f"api-preview-control-{kind}.png"), _frame(export_control, second, directory / f"api-export-control-{kind}.png")
            pbox, psize = _difference_bbox(pframe, pcontrol)
            ebox, esize = _difference_bbox(eframe, econtrol)
            assert pbox and ebox, kind
            pn, en = _normal(pbox, psize), _normal(ebox, esize)
            assert max(abs(a - b) for a, b in zip(pn, en)) < .02
            preview_metrics, export_metrics = _diff_metrics(pframe, pcontrol), _diff_metrics(eframe, econtrol)
            area_delta = abs(preview_metrics["normalizedArea"] - export_metrics["normalizedArea"])
            mask_iou = _iou(preview_metrics["maskBins"], export_metrics["maskBins"])
            mean_delta = max(abs(left - right) for left, right in zip(preview_metrics["meanRgb"], export_metrics["meanRgb"]))
            histogram_l1 = sum(abs(left - right) for left, right in zip(preview_metrics["histogram"], export_metrics["histogram"]))
            assert area_delta < .012 and mask_iou > .45 and mean_delta < 35 and histogram_l1 < .50
            # These are measured pixel groups (not input style labels): dark
            # changed regions expose libass outline and banner background.
            if kind in {"title", "keyword"}:
                assert preview_metrics["darkChangedPixels"] > 100 and export_metrics["darkChangedPixels"] > 100
            if kind in {"selling-point", "cta"}:
                assert preview_metrics["darkChangedPixels"] > 1_000 and export_metrics["darkChangedPixels"] > 1_000
            api_matrix["elements"][kind] = {"sample": second, "preview": pn, "export": en,
                "maxDelta": max(abs(a - b) for a, b in zip(pn, en)), "previewPixels": preview_metrics,
                "exportPixels": export_metrics, "comparison": {"normalizedAreaDelta": area_delta, "maskGridIoU": mask_iou,
                "meanRgbDelta": mean_delta, "histogramL1": histogram_l1}}
            for phase, moment in {"before": second - .35, "after": second + .35}.items():
                for label, video, control in (("preview", api_preview, preview_control), ("export", api_export, export_control)):
                    assert _difference_bbox(_frame(video, moment, directory / f"api-{label}-{kind}-{phase}.png"),
                                            _frame(control, moment, directory / f"api-{label}-control-{kind}-{phase}.png"))[0] is None
        (directory / "frozen-five-element-matrix.json").write_text(json.dumps(api_matrix, ensure_ascii=False, indent=2), encoding="utf-8")

        changed = api.put(f"{base}/{task['id']}/variant", json={"revision": 3, "tracks": tracks,
                          "packaging": {**package, "elements": [{**item, "text": "立即领取"} if item["id"] == "cta" else item for item in package["elements"]]}})
        assert changed.status_code == 200, changed.text
        assert api.get(f"{base}/{task['id']}/variant/previews/{preview_id}/output").status_code == 409
        assert api.get(f"{base}/{task['id']}/exports/{export_id}/output").status_code == 409

        refreshed = api.post(f"{base}/{task['id']}/variant/previews", json={"revision": 4})
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
    _commands(directory, "compile_ass validation rejection matrix; render_timeline cancellation before process launch")
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
    monkeypatch.setattr(packaging_module, "_FONT", directory / "definitely-missing-controlled-font.otf")
    monkeypatch.setattr(packaging_module, "_FONT_INFO", None)
    with pytest.raises(PackagingError, match="字体") as missing_font:
        private_font_path()
    monkeypatch.undo()
    monkeypatch.setattr(packaging_module, "FONT_SHA256", "00" * 32)
    with pytest.raises(PackagingError, match="字体"):
        private_font_path()
    monkeypatch.undo()
    output = directory / "cancelled.mp4"
    with pytest.raises(TimelineRenderError, match="取消"):
        render_timeline(timeline, {"shot": source}, output, format="mp4", packaging=package,
                        ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE), cancelled=lambda: True)
    assert not output.exists() and not (directory / ".packaging.ass").exists()
    (directory / "failure-matrix.json").write_text(json.dumps({"missingFont": {"path": "definitely-missing-controlled-font.otf", "error": str(missing_font.value)}, "missingGlyph": "rejected",
        "unknownTemplate": "rejected", "literalInjection": "escaped", "cancel": "pre-ffmpeg-cleaned"}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_font_cache_closes_faces_is_path_bound_and_initializes_concurrently(monkeypatch, tmp_path):
    """A valid SHA is not enough to reuse metadata from a different controlled path."""
    import app.montage_packaging as packaging
    calls, closed, barrier = [], [], threading.Barrier(6)

    class Face:
        def __getitem__(self, key):
            if key == "name":
                return type("Names", (), {"names": [type("Name", (), {"nameID": item, "toUnicode": lambda self, value=value: value})()
                    for item, value in ((1, packaging.FONT_FAMILY), (4, packaging.FONT_FAMILY), (6, packaging.FONT_POSTSCRIPT))]})()
            if key == "OS/2":
                return type("OS2", (), {"usWinAscent": 1, "usWinDescent": 1})()
            if key == "hmtx":
                return type("Hmtx", (), {"metrics": {"A": (1, 0)}})()
            raise KeyError(key)
        def getBestCmap(self): return {ord("A"): "A"}
        def close(self): closed.append(True)

    font = tmp_path / "controlled.otf"
    font.write_bytes(b"font")
    digest = packaging.hashlib.sha256(b"font").hexdigest()
    monkeypatch.setattr(packaging, "_FONT", font)
    monkeypatch.setattr(packaging, "FONT_SHA256", digest)
    monkeypatch.setattr(packaging, "_FONT_INFO", None)
    monkeypatch.setattr("fontTools.ttLib.TTFont", lambda *_args, **_kwargs: calls.append(True) or Face())
    def call():
        barrier.wait(); assert packaging.private_font_path() == font
    workers = [threading.Thread(target=call) for _ in range(5)]
    for worker in workers: worker.start()
    barrier.wait()
    for worker in workers: worker.join()
    assert len(calls) == len(closed) == 1
    font.write_bytes(b"replacement")
    monkeypatch.setattr(packaging, "FONT_SHA256", packaging.hashlib.sha256(b"replacement").hexdigest())
    assert packaging.private_font_path() == font
    font.write_bytes(b"font")
    monkeypatch.setattr(packaging, "FONT_SHA256", digest)
    assert packaging.private_font_path() == font
    assert len(calls) == len(closed) == 3
    replacement = tmp_path / "other.otf"
    replacement.write_bytes(b"font")
    monkeypatch.setattr(packaging, "_FONT", replacement)
    assert packaging.private_font_path() == replacement
    assert len(calls) == len(closed) == 4


def test_started_ffmpeg_cancel_and_no_libass_are_explicit_and_leave_no_packaging_files(tmp_path):
    """Cancellation must occur after process start, and missing libass is not a generic encode error."""
    directory = _evidence_dir("failure-runtime")
    _commands(directory, "render_timeline with executable FFmpeg shims: each fresh marker records pid/time/temporary partial output before polling cancellation; no-ass shim returns exact filter error")
    source = _source(directory / "source.mp4", duration=1)
    timeline, package = _timeline(_tracks(1)), _package()
    package["elements"] = [{"id": "title", "kind": "title", "enabled": True, "start": 0, "end": .8,
                            "text": "取消清理", "position": "top", "style": "plain"}]
    rounds = []
    for number in (1, 2):
        run_id = uuid.uuid4().hex
        started, output = directory / f"started-{run_id}.json", directory / f"cancelled-{run_id}.mp4"
        test_started_ns = time.time_ns()
        # Each run has a new marker and output name.  These unlinks are bounded
        # to that fresh id, never delete a tracked font or shared evidence.
        for path in (started, output):
            path.unlink(missing_ok=True)
        shim = directory / f"ffmpeg-shim-{run_id}.py"
        shim.write_text("#!/usr/bin/env python3\nimport json,os,pathlib,sys,time\npartial=pathlib.Path(sys.argv[-1])\npartial.write_bytes(b'partial-' + sys.argv[-1].encode())\npathlib.Path(" + repr(str(started)) + ").write_text(json.dumps({'pid': os.getpid(), 'startedNs': time.time_ns(), 'partial': str(partial), 'partialSize': partial.stat().st_size}))\ntime.sleep(10)\n", encoding="utf-8")
        shim.chmod(0o755)
        observed = {"started": False, "partial": False, "startedNs": None, "partialSize": 0, "partialMtimeNs": None}
        def cancelled_after_actual_start() -> bool:
            if not started.is_file():
                return False
            record = json.loads(started.read_text(encoding="utf-8"))
            partial = Path(record["partial"])
            if record["startedNs"] <= test_started_ns or not partial.is_file() or partial.stat().st_size <= 0:
                return False
            observed.update(started=True, partial=True, startedNs=record["startedNs"], partialSize=partial.stat().st_size,
                            partialMtimeNs=partial.stat().st_mtime_ns)
            return True
        with pytest.raises(TimelineRenderError, match="取消") as cancelled:
            render_timeline(timeline, {"shot": source}, output, format="mp4", packaging=package,
                            ffmpeg_path=str(shim), ffprobe_path=str(FFPROBE), cancelled=cancelled_after_actual_start)
        leftovers = sorted(path.name for path in directory.glob(".cancelled-*.mp4"))
        assert observed["started"] and observed["partial"] and observed["startedNs"] > test_started_ns
        assert not output.exists() and not leftovers and not (directory / ".packaging.ass").exists()
        rounds.append({"runId": run_id, "testStartedNs": test_started_ns, "observed": observed,
                       "publishedOutput": output.exists(), "temporaryOutputs": leftovers, "ass": (directory / ".packaging.ass").exists(),
                       "cancelError": str(cancelled.value)})
    no_ass = directory / "ffmpeg-no-ass.py"
    no_ass.write_text("#!/usr/bin/env python3\nimport sys\nsys.stderr.write(\"No such filter: 'ass'\\n\")\nsys.exit(1)\n", encoding="utf-8")
    no_ass.chmod(0o755)
    with pytest.raises(TimelineRenderError, match="libass") as no_libass:
        render_timeline(timeline, {"shot": source}, directory / "no-ass.mp4", format="mp4", packaging=package,
                        ffmpeg_path=str(no_ass), ffprobe_path=str(FFPROBE))
    (directory / "failure-runtime.json").write_text(json.dumps({"phase": "ffmpeg-started", "cancelRounds": rounds,
        "noLibass": str(no_libass.value)}, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "failure-runtime.log").write_text("\n".join("run %(runId)s: started=%(startedNs)s partial=%(partialSize)sB mtime=%(partialMtimeNs)s cleanup=%(temporaryOutputs)s" % {**item, **item["observed"]}
        for item in rounds) + "\nno-libass: " + str(no_libass.value) + "\n", encoding="utf-8")
