"""受控的 worker 替身；由 depth capture runner 集成测试作为真实子进程执行。"""

import argparse
import json
import os
import struct
import sys
import time
import zipfile
from pathlib import Path


MODEL = {
    "modelId": "video-depth-anything-small-relative",
    "upstreamCommit": "4f5ae23172ba60fd7bc11ef671cca678842c7072",
    "checkpointSha256": "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--upstream-root", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--target-fps", required=True, type=int)
    parser.add_argument("--input-size", required=True, type=int)
    parser.add_argument("--max-res", required=True, type=int)
    parser.add_argument("--output-short-side", required=True, type=int)
    parser.add_argument("--backbone-microbatch", required=True, type=int)
    args = parser.parse_args()

    audit = os.environ.get("FAKE_WORKER_AUDIT")
    if audit:
        Path(audit).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
    mode = os.environ.get("FAKE_WORKER_MODE", "success")
    if mode == "timeout":
        time.sleep(10)
        return 0
    if mode == "oom":
        print("MPS backend out of memory", file=sys.stderr)
        return 71
    if mode == "unavailable":
        print("requested device unsupported", file=sys.stderr)
        return 70
    if mode == "failure":
        print("internal worker detail must not reach users", file=sys.stderr)
        return 72

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if mode == "missing":
        (output / "worker-metadata.json").write_text("{}", encoding="utf-8")
        return 0
    if mode == "bad-zip":
        (output / "depths.gray").write_bytes(b"bad")
        (output / "worker-metadata.json").write_text("{}", encoding="utf-8")
        return 0
    if mode in ("bad-header-zero", "bad-header-one"):
        header_length = 0 if mode == "bad-header-zero" else 1
        npy = b"\x93NUMPY\x01\x00" + struct.pack("<H", header_length) + b"x" * header_length
        with zipfile.ZipFile(output / "depths.npz", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("depths.npy", npy)
        (output / "worker-metadata.json").write_text("{}", encoding="utf-8")
        return 0
    shape = (2, args.output_short_side, (int(round(args.output_short_side * 16 / 9)) + 1) // 2 * 2)
    depths = [index % 256 for index in range(shape[0] * shape[1] * shape[2])]
    if mode == "constant":
        depths = [128] * len(depths)
    if mode == "odd":
        shape = (2, 2, 3)
        depths = [index % 256 for index in range(12)]
    if mode == "too-large":
        shape = (2, 2, 642)
        depths = [index % 256 for index in range(2568)]
    if mode == "long":
        frames = int(os.environ["FAKE_WORKER_FRAMES"])
        shape = (frames, args.output_short_side, (int(round(args.output_short_side * 16 / 9)) + 1) // 2 * 2)
        with (output / "depths.gray").open("wb") as raw:
            raw.truncate(shape[0] * shape[1] * shape[2])
    else:
        (output / "depths.gray").write_bytes(b"" if mode == "malformed" else bytes(depths))
    metadata = {
        "schemaVersion": 1,
        "modelIdentity": MODEL,
        "device": args.device,
        "targetFps": args.target_fps,
        "inputSize": args.input_size,
        "maxRes": args.max_res,
        "outputShortSide": args.output_short_side,
        "backboneMicrobatch": args.backbone_microbatch,
        "frameCount": shape[0],
        "width": shape[2],
        "height": shape[1],
        "frameRate": 99.0 if mode == "too-large-fps" else float(args.target_fps),
        "depthMin": 0.0,
        "depthMax": 1.0,
        "finite": True,
        "normalizationDirection": "near_white_far_black",
        "normalizationPercentilePolicy": {
            "scope": "clip",
            "lowerPercentile": 2,
            "upperPercentile": 98,
        },
        "sourceMotionSamples": [
            {"timestampSeconds": 1.0 / float(args.target_fps), "magnitude": 0.25},
        ],
    }
    if mode == "bad-metadata":
        metadata["device"] = "cuda"
    if mode == "bad-motion":
        metadata["sourceMotionSamples"] = [{"timestampSeconds": 0.125, "magnitude": 2.0}]
    if mode == "missing-motion":
        del metadata["sourceMotionSamples"]
    (output / "worker-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
