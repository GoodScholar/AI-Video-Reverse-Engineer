"""Business rules for production shots, steps, candidates, and delivery readiness."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

NODE_KINDS = ("reference", "trim", "first_frame", "last_frame", "crop", "resize", "prompt")
MAX_RESULT_VERSIONS = 100


class ShotProductionError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class StepSource:
    kind: str
    asset_id: str | None = None
    step_id: str | None = None
    artifact_name: str | None = None


@dataclass(frozen=True)
class PreparedStepRun:
    kind: str
    params: dict[str, Any]
    source: StepSource | None


class ShotProduction:
    """Apply all persisted business decisions for production shots."""

    def __init__(
        self,
        state: dict[str, Any],
        *,
        asset_available: Callable[[dict[str, Any]], bool] | None = None,
        artifact_available: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], bool] | None = None,
    ):
        self.state = state
        self._asset_available = asset_available or (lambda asset: True)
        self._artifact_available = artifact_available or (lambda shot, step, artifact: True)

    def edit(self, brief: dict[str, Any], incoming: list[dict[str, Any]]) -> None:
        if self.has_inflight():
            self._fail("preproduction_node_running", "有节点正在执行，完成后再修改工作台。")
        self._validate_shots(incoming)
        previous_brief = self.state["brief"]
        revision = self.state["revision"] + 1
        shots = self._derive_shots(
            self.state["shots"], incoming, previous_brief != brief, brief,
            self.state["assets"], revision,
        )
        self.state["brief"] = brief
        self.state["shots"] = shots
        self.state["revision"] = revision

    def attach_result(self, shot_id: str, asset_id: str) -> None:
        self._ensure_idle("有节点正在执行，完成后再关联镜头结果。")
        shot = self._shot(shot_id)
        asset = self._asset(asset_id)
        if asset.get("kind") != "video":
            self._fail("preproduction_result_invalid", "镜头结果必须是本项目的视频素材。", 422)
        history = result_history(shot)
        if not any(version["assetId"] == asset_id for version in history):
            if len(history) >= MAX_RESULT_VERSIONS:
                self._fail("preproduction_result_limit", "每个镜头最多保留 100 个候选结果。", 422)
            revision = self.state["revision"] + 1
            history.append({
                "assetId": asset_id,
                "signature": result_signature(self.state["brief"], shot),
                "reviewed": False,
                "association": result_association(
                    self.state["brief"], shot, self.state["assets"], revision,
                ),
            })
        shot["_resultVersions"] = history
        shot["resultAssetId"] = asset_id
        self.state["revision"] += 1

    def prepare_result_attachment(self, shot_id: str, revision: int) -> None:
        self._ensure_idle("有节点正在执行，完成后再关联镜头结果。")
        if revision != self.state["revision"]:
            self._fail("preproduction_conflict", "镜头方案已更新，请重新读取后上传结果。")
        shot = self._shot(shot_id)
        if len(result_history(shot)) >= MAX_RESULT_VERSIONS:
            self._fail("preproduction_result_limit", "每个镜头最多保留 100 个候选结果。", 422)

    def review_result(self, shot_id: str, asset_id: str) -> None:
        self._ensure_idle("有节点正在执行，完成后再记录检查结果。")
        shot, version = self._candidate(shot_id, asset_id)
        asset = self._asset(asset_id)
        if asset.get("kind") != "video" or not self._asset_available(asset):
            self._fail("preproduction_result_missing", "候选视频文件不可用。")
        version.update(signature=result_signature(self.state["brief"], shot), reviewed=True)
        self._save_history(shot, asset_id, version)

    def set_result_note(self, shot_id: str, asset_id: str, note: str) -> None:
        self._ensure_idle("有节点正在执行，完成后再修改素材。")
        shot, version = self._candidate(shot_id, asset_id)
        version["externalNote"] = note
        self._save_history(shot, asset_id, version)

    def set_adoption_reason(self, shot_id: str, asset_id: str, reason: str) -> None:
        self._ensure_idle("有节点正在执行，完成后再修改素材。")
        shot, version = self._candidate(shot_id, asset_id)
        version["adoptionReason"] = reason
        self._save_history(shot, asset_id, version)

    def remove_result(self, shot_id: str, asset_id: str) -> None:
        self._ensure_idle("有节点正在执行，完成后再修改素材。")
        shot = self.result_for_removal(shot_id, asset_id)
        shot["_resultVersions"] = [item for item in result_history(shot) if item["assetId"] != asset_id]
        self.state["revision"] += 1

    def result_for_removal(self, shot_id: str, asset_id: str) -> dict[str, Any]:
        shot, _ = self._candidate(shot_id, asset_id)
        if shot.get("resultAssetId") == asset_id:
            self._fail("preproduction_result_adopted", "当前采用的结果不能移出历史，请先采用其他结果并保存。")
        return shot

    def public_result_versions(self, shot_id: str) -> list[dict[str, Any]]:
        return public_result_versions(self.state["brief"], self._shot(shot_id))

    def invalidate_descendants(self, shot_id: str, source_id: str) -> None:
        shot = self._shot(shot_id)
        changed = {source_id}
        self._invalidate_dependants(shot["nodes"], changed)

    def prepare_run(self, shot_id: str, step_id: str, run_id: str) -> PreparedStepRun:
        if self.has_inflight():
            self._fail("preproduction_node_running", "有节点正在执行，完成后再启动其他节点。")
        shot, step = self._step(shot_id, step_id)
        if step["status"] in ("queued", "running"):
            self._fail("preproduction_node_running", "节点正在执行。")
        source = self._step_source(shot, step)
        step.update(status="queued", error=None, artifacts=[], runId=run_id)
        self.invalidate_descendants(shot_id, step_id)
        self.state["revision"] += 1
        return PreparedStepRun(kind=step["kind"], params=self._run_params(shot, step), source=source)

    def start_run(self, shot_id: str, step_id: str, run_id: str) -> dict[str, Any] | None:
        _, step = self._step(shot_id, step_id)
        if step.get("runId") != run_id or step["status"] != "queued":
            return None
        step["status"] = "running"
        return step

    def complete_run(self, shot_id: str, step_id: str, run_id: str, artifacts: list[dict[str, Any]]) -> bool:
        _, step = self._step(shot_id, step_id)
        if step.get("runId") != run_id or step["status"] != "running":
            return False
        step.update(status="completed", error=None, artifacts=artifacts)
        self.state["revision"] += 1
        return True

    def fail_run(self, shot_id: str, step_id: str, run_id: str, message: str) -> bool:
        _, step = self._step(shot_id, step_id)
        if step.get("runId") != run_id:
            return False
        step.update(status="failed", error=message, artifacts=[])
        self.state["revision"] += 1
        return True

    def delivery_checks(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        assets = {asset["id"]: asset for asset in self.state["assets"]}
        brief = self.state["brief"]
        missing = [label for key, label in (("theme", "主题"), ("aspect", "画幅")) if not brief.get(key, "").strip()]
        if not brief.get("duration"):
            missing.append("目标时长")
        if missing:
            result.append({"level": "warning", "code": "brief_incomplete", "message": "创作需求尚未填写：" + "、".join(missing) + "。"})
        total_duration = sum(shot["duration"] for shot in self.state["shots"])
        if self.state["shots"] and brief.get("duration", 0) > 0 and not math.isclose(total_duration, brief["duration"], rel_tol=0, abs_tol=0.01):
            result.append({"level": "warning", "code": "duration_mismatch", "message": f"镜头计划总时长 {total_duration:.2f} 秒，与目标 {brief['duration']:.2f} 秒不一致，请核对镜头或需求。"})
        if not self.state["shots"]:
            result.append({"level": "error", "code": "shots_missing", "message": "尚未添加镜头，无法交付。"})
        for shot in self.state["shots"]:
            self._shot_checks(result, assets, brief, shot)
        return result

    def has_inflight(self) -> bool:
        return any(
            step["status"] in ("queued", "running")
            for shot in self.state["shots"] for step in shot["nodes"]
        )

    def referenced_assets(self) -> list[dict[str, Any]]:
        ids = {asset_id for shot in self.state["shots"] for asset_id in shot["assetIds"]}
        ids.update(version["assetId"] for shot in self.state["shots"] for version in result_history(shot))
        ids.update(
            step["input"].split(":", 1)[1]
            for shot in self.state["shots"] for step in shot["nodes"]
            if step["input"].startswith("asset:")
        )
        return [asset for asset in self.state["assets"] if asset["id"] in ids]

    def _validate_shots(self, incoming: list[dict[str, Any]]) -> None:
        assets = self.state["assets"]
        asset_ids = {asset["id"] for asset in assets}
        scene_ids = {scene["id"] for scene in self.state.get("scenes", [])}
        if len({shot["id"] for shot in incoming}) != len(incoming):
            self._fail("preproduction_shot_invalid", "镜头标识符不能重复。", 422)
        for shot in incoming:
            if shot.get("sceneId") is not None and shot["sceneId"] not in scene_ids:
                self._fail("preproduction_scene_missing", "镜头所属场景不存在。", 422)
            result_asset_id = shot.get("resultAssetId")
            if result_asset_id is not None and not any(asset["id"] == result_asset_id and asset["kind"] == "video" for asset in assets):
                self._fail("preproduction_result_invalid", "镜头结果必须是本项目的视频素材。", 422)
            shot_asset_ids = shot.get("assetIds", [])
            if len(set(shot_asset_ids)) != len(shot_asset_ids) or any(value not in asset_ids for value in shot_asset_ids):
                self._fail("preproduction_asset_missing", "镜头绑定了不存在的素材。", 422)
            seen: set[str] = set()
            for index, step in enumerate(shot.get("nodes", [])):
                if step["id"] in seen:
                    self._fail("preproduction_node_invalid", "同一镜头中的节点标识符不能重复。", 422)
                self._validate_step(step, index, seen, set(shot_asset_ids))
                seen.add(step["id"])

    def _derive_shots(self, previous, incoming, brief_changed, brief, assets, revision):
        old_shots = {shot["id"]: shot for shot in previous}
        result = []
        for item in incoming:
            old_shot = old_shots.get(item["id"], {})
            old_nodes = {step["id"]: step for step in old_shot.get("nodes", [])}
            changed: set[str] = set()
            nodes = []
            for incoming_step in item.get("nodes", []):
                value = {key: incoming_step[key] for key in ("id", "kind", "input", "params")}
                old = old_nodes.get(incoming_step["id"])
                same = old is not None and all(old.get(key) == value[key] for key in ("kind", "input", "params"))
                if same:
                    value.update(status=old["status"], error=old.get("error"), artifacts=old.get("artifacts", []))
                else:
                    if old and old.get("status") == "running":
                        self._fail("preproduction_node_running", "运行中的节点不能修改。")
                    value.update(status="stale" if old else "pending", error=None, artifacts=[])
                    changed.add(incoming_step["id"])
                nodes.append(value)
            if brief_changed or old_shot.get("prompt") != item.get("prompt", "") or old_shot.get("negativePrompt") != item.get("negativePrompt", ""):
                for step in nodes:
                    if step["kind"] == "prompt":
                        self._mark_stale(step, "运行中的节点依赖不能修改。")
                        changed.add(step["id"])
            self._invalidate_dependants(nodes, changed)
            record = {
                "id": item["id"], "title": item.get("title", ""), "duration": item["duration"],
                "prompt": item.get("prompt", ""), "negativePrompt": item.get("negativePrompt", ""),
                "assetIds": item.get("assetIds", []), "nodes": nodes,
                "resultAssetId": item.get("resultAssetId"),
                "sceneId": old_shot.get("sceneId", item.get("sceneId") or "scene-default"),
                "rank": f"{len(result) + 1:08d}",
            }
            history = result_history(old_shot)
            result_asset_id = item.get("resultAssetId")
            if result_asset_id and not any(version["assetId"] == result_asset_id for version in history):
                if len(history) >= MAX_RESULT_VERSIONS:
                    self._fail("preproduction_result_limit", "每个镜头最多保留 100 个候选结果。", 422)
                history.append({
                    "assetId": result_asset_id,
                    "signature": result_signature(brief, record),
                    "reviewed": False,
                    "association": result_association(brief, record, assets, revision),
                })
            record["_resultVersions"] = history
            if "_importKey" in old_shot:
                record["_importKey"] = old_shot["_importKey"]
            result.append(record)
        return result

    def _validate_step(self, step, index, seen, asset_ids):
        kind = step.get("kind")
        if kind not in NODE_KINDS:
            self._fail("preproduction_node_invalid", "节点类型无效。", 422)
        value = step.get("input", "")
        params = step.get("params", {})
        if kind == "prompt":
            if value and not self._valid_input(value, index, seen, asset_ids):
                self._fail("preproduction_input_invalid", "提示词节点输入无效。", 422)
            if not isinstance(params.get("text", ""), str) or len(params.get("text", "")) > 12000:
                self._fail("preproduction_node_invalid", "提示词节点参数无效。", 422)
            return
        if not self._valid_input(value, index, seen, asset_ids):
            self._fail("preproduction_input_invalid", "节点输入必须是本镜头的素材或更早节点。", 422)
        if kind == "trim":
            self._numbers(params, ("start", "end"))
            if not params["start"] < params["end"]:
                self._fail("preproduction_node_invalid", "截取起止时间无效。", 422)
        elif kind == "crop":
            self._numbers(params, ("x", "y", "width", "height"))
            if any(not 0 <= params[key] <= 1 for key in ("x", "y", "width", "height")) or params["x"] + params["width"] > 1 or params["y"] + params["height"] > 1 or not params["width"] or not params["height"]:
                self._fail("preproduction_node_invalid", "裁切参数必须在 0 到 1 的范围内。", 422)
        elif kind == "resize":
            self._numbers(params, ("width", "height"))
            if any(int(params[key]) != params[key] or not 1 <= params[key] <= 8192 for key in ("width", "height")):
                self._fail("preproduction_node_invalid", "缩放尺寸无效。", 422)
        elif params:
            self._fail("preproduction_node_invalid", "该节点不接受参数。", 422)

    def _invalidate_dependants(self, nodes, changed):
        for step in nodes:
            upstream = step["input"].removeprefix("node:") if step["input"].startswith("node:") else None
            if upstream in changed:
                self._mark_stale(step, "运行中的节点依赖不能修改。")
                changed.add(step["id"])

    def _mark_stale(self, step, running_message):
        if step["status"] == "running":
            self._fail("preproduction_node_running", running_message)
        step.update(status="stale", error=None, artifacts=[])

    def _shot_checks(self, result, assets, brief, shot):
        if not shot["assetIds"]:
            result.append({"level": "warning", "code": "shot_assets_missing", "shotId": shot["id"], "message": "镜头未绑定素材，请确认是否仅使用文字制作。"})
        if shot["duration"] <= 0:
            result.append({"level": "warning", "code": "shot_duration_missing", "shotId": shot["id"], "message": "镜头尚未设置有效计划时长。"})
        if not shot["prompt"].strip():
            result.append({"level": "warning", "code": "shot_prompt_missing", "shotId": shot["id"], "message": "镜头尚未填写提示词。"})
        adopted = next((version for version in public_result_versions(brief, shot) if version["assetId"] == shot.get("resultAssetId")), None)
        if adopted and (adopted["planChanged"] or not adopted["reviewed"]):
            result.append({"level": "warning", "code": "result_review_required", "shotId": shot["id"], "message": "采用结果的方案已变化或缺少关联记录，请重新检查。" if adopted["planChanged"] else "采用结果尚未按当前方案人工检查。"})
        referenced = set(shot["assetIds"])
        referenced.update(version["assetId"] for version in result_history(shot))
        if shot.get("resultAssetId"):
            referenced.add(shot["resultAssetId"])
        referenced.update(step["input"].split(":", 1)[1] for step in shot["nodes"] if step["input"].startswith("asset:"))
        for asset_id in referenced:
            asset = assets.get(asset_id)
            if asset is None or not self._asset_available(asset):
                result.append({"level": "error", "code": "shot_asset_unavailable", "shotId": shot["id"], "message": "镜头引用的素材已不存在。"})
        for step in shot["nodes"]:
            if step["status"] == "failed":
                result.append({"level": "error", "code": "node_failed", "shotId": shot["id"], "nodeId": step["id"], "message": "节点执行失败，请重试或修改输入。"})
            elif step["status"] in ("pending", "queued", "running", "stale"):
                result.append({"level": "error", "code": "node_not_ready", "shotId": shot["id"], "nodeId": step["id"], "message": "节点尚未生成当前产物。"})
            elif step["status"] == "completed" and (not step["artifacts"] or any(
                not self._artifact_available(shot, step, artifact) for artifact in step["artifacts"]
            )):
                result.append({"level": "error", "code": "node_output_unavailable", "shotId": shot["id"], "nodeId": step["id"], "message": "节点当前产物不可用。"})

    def _shot(self, shot_id):
        shot = next((item for item in self.state["shots"] if item["id"] == shot_id), None)
        if shot is None:
            self._fail("preproduction_shot_missing", "镜头不存在。", 404)
        return shot

    def _step(self, shot_id, step_id):
        shot = self._shot(shot_id)
        step = next((item for item in shot["nodes"] if item["id"] == step_id), None)
        if step is None:
            self._fail("preproduction_node_missing", "节点不存在。", 404)
        return shot, step

    def _step_source(self, shot, step):
        input_value = step["input"]
        if not input_value:
            return None
        prefix, _, identifier = input_value.partition(":")
        if prefix == "asset":
            if not any(asset["id"] == identifier for asset in self.state["assets"]):
                self._fail("preproduction_input_missing", "节点输入素材不存在。")
            return StepSource(kind="asset", asset_id=identifier)
        if prefix == "node":
            upstream = next((item for item in shot["nodes"] if item["id"] == identifier), None)
            if upstream is None or upstream["status"] != "completed" or not upstream["artifacts"]:
                self._fail("preproduction_input_not_ready", "上游节点尚未生成当前产物。")
            return StepSource(kind="artifact", step_id=identifier, artifact_name=upstream["artifacts"][0]["name"])
        self._fail("preproduction_input_invalid", "节点输入无效。", 422)

    def _run_params(self, shot, step):
        params = dict(step["params"])
        if step["kind"] != "prompt":
            return params
        brief = self.state["brief"]
        sections = (
            ("主题", brief.get("theme", "")), ("用途", brief.get("purpose", "")),
            ("风格", brief.get("style", "")), ("时长", brief.get("duration", "")),
            ("画幅", brief.get("aspect", "")), ("必须保留", brief.get("mustPreserve", "")),
            ("镜头提示词", shot.get("prompt", "")), ("镜头负面提示词", shot.get("negativePrompt", "")),
            ("节点文本", params.get("text", "")),
        )
        params["text"] = "\n".join(f"{label}: {value}" for label, value in sections if str(value).strip())
        return params

    def _asset(self, asset_id):
        asset = next((item for item in self.state["assets"] if item["id"] == asset_id), None)
        if asset is None:
            self._fail("preproduction_result_missing", "候选视频文件不可用。")
        return asset

    def _candidate(self, shot_id, asset_id):
        shot = next((item for item in self.state["shots"] if item["id"] == shot_id), None)
        if shot is None:
            self._fail("preproduction_result_missing", "当前镜头不存在该候选结果。", 404)
        version = next((item for item in result_history(shot) if item["assetId"] == asset_id), None)
        if version is None:
            self._fail("preproduction_result_missing", "当前镜头不存在该候选结果。", 404)
        return shot, version

    def _save_history(self, shot, asset_id, version):
        shot["_resultVersions"] = [
            version if item["assetId"] == asset_id else item for item in result_history(shot)
        ]
        self.state["revision"] += 1

    @staticmethod
    def _valid_input(value, index, seen, asset_ids):
        prefix, separator, identifier = value.partition(":")
        return bool(separator and identifier and ((prefix == "asset" and identifier in asset_ids) or (prefix == "node" and identifier in seen)))

    def _numbers(self, params, names):
        if set(params) != set(names):
            self._fail("preproduction_node_invalid", "节点参数无效。", 422)
        for name in names:
            value = params.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                self._fail("preproduction_node_invalid", "节点参数必须是有限数值。", 422)

    def _ensure_idle(self, message):
        if self.has_inflight():
            self._fail("preproduction_node_running", message)

    @staticmethod
    def _fail(code, message, status=409):
        raise ShotProductionError(code, message, status)


def result_association(brief, shot, assets, revision):
    referenced = set(shot.get("assetIds", []))
    referenced.update(step.get("input", "").removeprefix("asset:") for step in shot.get("nodes", []) if step.get("input", "").startswith("asset:"))
    return deepcopy({
        "revision": revision,
        "brief": {"inputKind": "reference_video", **brief},
        "shot": {key: shot.get(key) for key in ("id", "title", "duration", "prompt", "negativePrompt")},
        "assets": [{key: asset.get(key) for key in ("id", "name", "kind", "role")} for asset in assets if asset["id"] in referenced],
        "nodes": [{
            "id": step["id"], "kind": step["kind"], "input": step["input"], "params": step["params"],
            "outputs": [item["name"] for item in step.get("artifacts", [])],
        } for step in shot.get("nodes", [])],
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
        "nodes": [{key: step.get(key) for key in ("id", "kind", "input", "params")} for step in shot.get("nodes", [])],
    }
    return hashlib.sha256(json.dumps(_canonical(plan), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def result_history(shot):
    versions = [dict(item) for item in shot.get("_resultVersions", [])]
    current = shot.get("resultAssetId")
    if current and not any(item["assetId"] == current for item in versions):
        versions.append({"assetId": current, "signature": None, "reviewed": False})
    return versions


def public_result_versions(brief, shot):
    signature = result_signature(brief, shot)
    return [{
        "assetId": version["assetId"],
        "planChanged": version.get("signature") != signature,
        "reviewed": bool(version.get("reviewed")) and version.get("signature") == signature,
        **({"association": version["association"]} if "association" in version else {}),
        **({"externalNote": version["externalNote"]} if "externalNote" in version else {}),
        **({"adoptionReason": version["adoptionReason"]} if "adoptionReason" in version else {}),
    } for version in result_history(shot)]
