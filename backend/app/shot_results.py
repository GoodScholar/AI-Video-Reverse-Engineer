"""Server-owned candidate history and content-based review freshness."""
import hashlib
import json
from copy import deepcopy

MAX_RESULT_VERSIONS = 100


def result_association(brief, shot, assets, revision):
    """Record the saved plan at attachment time, not an external model run."""
    referenced = set(shot.get("assetIds", []))
    referenced.update(node.get("input", "").removeprefix("asset:") for node in shot.get("nodes", [])
                      if node.get("input", "").startswith("asset:"))
    return deepcopy({
        "revision": revision,
        "brief": {"inputKind": "reference_video", **brief},
        "shot": {key: shot.get(key) for key in ("id", "title", "duration", "prompt", "negativePrompt")},
        "assets": [{key: asset.get(key) for key in ("id", "name", "kind", "role")}
                   for asset in assets if asset["id"] in referenced],
        "nodes": [{"id": node["id"], "kind": node["kind"], "input": node["input"],
                   "params": node["params"], "outputs": [item["name"] for item in node.get("artifacts", [])]}
                  for node in shot.get("nodes", [])],
    })


def _canonical(value):
    if type(value) in (int, float):
        return float(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def result_signature(brief, shot):
    plan = {
        "brief": {"inputKind": "reference_video", **brief},
        "duration": shot.get("duration", 0), "prompt": shot.get("prompt", ""),
        "negativePrompt": shot.get("negativePrompt", ""), "assetIds": sorted(shot.get("assetIds", [])),
        "nodes": [{key: node.get(key) for key in ("id", "kind", "input", "params")} for node in shot.get("nodes", [])],
    }
    return hashlib.sha256(json.dumps(_canonical(plan), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def result_history(shot):
    versions = [dict(item) for item in shot.get("_resultVersions", [])]
    current = shot.get("resultAssetId")
    if current and not any(item["assetId"] == current for item in versions):
        # Old projects have no evidence of which plan produced this result.
        versions.append({"assetId": current, "signature": None, "reviewed": False})
    return versions


def public_result_versions(brief, shot):
    signature = result_signature(brief, shot)
    return [{"assetId": version["assetId"], "planChanged": version.get("signature") != signature,
             "reviewed": bool(version.get("reviewed")) and version.get("signature") == signature,
             **({"association": version["association"]} if "association" in version else {}),
             **({"externalNote": version["externalNote"]} if "externalNote" in version else {}),
             **({"adoptionReason": version["adoptionReason"]} if "adoptionReason" in version else {})}
            for version in result_history(shot)]
