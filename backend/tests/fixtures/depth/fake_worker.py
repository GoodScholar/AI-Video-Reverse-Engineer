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
        (output / "depths.npz").write_bytes(b"not a zip")
        (output / "worker-metadata.json").write_text("{}", encoding="utf-8")
        return 0
    if mode in ("bad-header-zero", "bad-header-one"):
        header_length = 0 if mode == "bad-header-zero" else 1
        npy = b"\x93NUMPY\x01\x00" + struct.pack("<H", header_length) + b"x" * header_length
        with zipfile.ZipFile(output / "depths.npz", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("depths.npy", npy)
        (output / "worker-metadata.json").write_text("{}", encoding="utf-8")
        return 0
    depths = [0.1, 0.2, 0.2, 0.3, 0.2, 0.3, 0.3, 0.4]
    if mode == "malformed":
        depths[0] = float("nan")
    if mode == "out-of-range":
        depths = [-1.0, 2.0, -0.5, 1.5, 0.0, 1.0, 0.25, 0.75]
    if mode == "constant":
        depths = [0.5] * 8
    shape = (2, 2, 2)
    if mode == "odd":
        shape = (2, 2, 3)
        depths = [index / 11 for index in range(12)]
    if mode == "too-large":
        shape = (2, 2, 642)
        depths = [index / 2567 for index in range(2568)]
    header = (
        "{'descr': '<f4', 'fortran_order': False, 'shape': " + repr(shape) + ", }\n"
    ).encode("ascii")
    padding = b" " * ((16 - ((10 + len(header)) % 16)) % 16)
    npy = b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header) + len(padding)) + header[:-1] + padding + b"\n"
    with zipfile.ZipFile(output / "depths.npz", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("depths.npy", npy + struct.pack(f"<{len(depths)}f", *depths))
    if mode == "bad-compression":
        payload = bytearray((output / "depths.npz").read_bytes())
        for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
            index = payload.index(signature)
            payload[index + offset:index + offset + 2] = struct.pack("<H", 99)
        (output / "depths.npz").write_bytes(payload)
    if mode == "corrupt-deflate":
        payload = bytearray((output / "depths.npz").read_bytes())
        local = payload.index(b"PK\x03\x04")
        compressed_size = struct.unpack("<I", payload[local + 18:local + 22])[0]
        name_length = struct.unpack("<H", payload[local + 26:local + 28])[0]
        extra_length = struct.unpack("<H", payload[local + 28:local + 30])[0]
        data_start = local + 30 + name_length + extra_length
        payload[data_start:data_start + compressed_size] = b"\xff" * compressed_size
        (output / "depths.npz").write_bytes(payload)
    metadata = {
        "schemaVersion": 1,
        "modelIdentity": MODEL,
        "device": args.device,
        "targetFps": args.target_fps,
        "inputSize": args.input_size,
        "maxRes": args.max_res,
        "frameCount": shape[0],
        "width": shape[2],
        "height": shape[1],
        "frameRate": 99.0 if mode == "too-large-fps" else float(args.target_fps),
        "depthMin": min(depths),
        "depthMax": max(depths),
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
