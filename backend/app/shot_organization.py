"""Transactional commands for shot management order and scene ownership."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


class ShotOrganizationError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ShotOrganization:
    """Mutate only shot organization fields after validating a whole command."""

    def __init__(self, state: dict[str, Any]):
        self.state = state

    def reorder_shots(self, ordered_shot_ids: list[str]) -> None:
        def apply(next_state: dict[str, Any]) -> None:
            shots = next_state["shots"]
            existing = [shot["id"] for shot in shots]
            if len(ordered_shot_ids) != len(set(ordered_shot_ids)) or set(ordered_shot_ids) != set(existing):
                self._fail("preproduction_shot_order_invalid", "镜头排序必须且只能包含当前全部镜头。", 422)
            ranks = {shot_id: f"{index:08d}" for index, shot_id in enumerate(ordered_shot_ids, start=1)}
            for shot in shots:
                shot["rank"] = ranks[shot["id"]]

        self._commit(apply)

    def move_shots_to_scene(self, shot_ids: list[str], scene_id: str) -> None:
        def apply(next_state: dict[str, Any]) -> None:
            self._scene(next_state, scene_id)
            requested = self._requested_shots(next_state, shot_ids)
            for shot in requested:
                shot["sceneId"] = scene_id

        self._commit(apply)

    def create_shot(self, shot: dict[str, Any]) -> None:
        def apply(next_state: dict[str, Any]) -> None:
            if not isinstance(shot, dict) or not isinstance(shot.get("id"), str):
                self._fail("preproduction_shot_invalid", "新镜头数据无效。", 422)
            if any(item["id"] == shot["id"] for item in next_state["shots"]):
                self._fail("preproduction_shot_exists", "镜头已存在。")
            self._scene(next_state, shot.get("sceneId"))
            rank = shot.get("rank")
            if not isinstance(rank, str) or not rank or any(item.get("rank") == rank for item in next_state["shots"]):
                self._fail("preproduction_shot_order_invalid", "新镜头顺序无效。", 422)
            next_state["shots"].append(deepcopy(shot))

        self._commit(apply)

    def replace_snapshot(
        self,
        scenes: list[dict[str, Any]],
        shot_organization: list[dict[str, Any]],
        *,
        increment_revision: bool = True,
    ) -> None:
        """Apply a full editable organization snapshot after content derivation."""
        def apply(next_state: dict[str, Any]) -> None:
            scene_ids = [scene.get("id") for scene in scenes]
            scene_ranks = [scene.get("rank") for scene in scenes]
            if not scenes or len(scene_ids) != len(set(scene_ids)) or len(scene_ranks) != len(set(scene_ranks)):
                self._fail("preproduction_scene_invalid", "场景标识或顺序无效。", 422)
            if any(not isinstance(value, str) or not value for value in (*scene_ids, *scene_ranks)):
                self._fail("preproduction_scene_invalid", "场景标识或顺序无效。", 422)
            current_ids = {shot["id"] for shot in next_state["shots"]}
            requested_ids = [shot.get("id") for shot in shot_organization]
            ranks = [shot.get("rank") for shot in shot_organization]
            if set(requested_ids) != current_ids or len(requested_ids) != len(set(requested_ids)):
                self._fail("preproduction_shot_invalid", "镜头组织快照与当前镜头不一致。", 422)
            if len(ranks) != len(set(ranks)) or any(not isinstance(rank, str) or not rank for rank in ranks):
                self._fail("preproduction_shot_order_invalid", "镜头顺序无效。", 422)
            scene_id_set = set(scene_ids)
            by_id = {item["id"]: item for item in shot_organization}
            if any(item.get("sceneId") not in scene_id_set for item in shot_organization):
                self._fail("preproduction_scene_missing", "镜头所属场景不存在。", 422)
            next_state["scenes"] = deepcopy(scenes)
            for shot in next_state["shots"]:
                organization = by_id[shot["id"]]
                shot["sceneId"] = organization["sceneId"]
                shot["rank"] = organization["rank"]

        self._commit(apply, increment_revision=increment_revision)

    def delete_scene(
        self,
        scene_id: str,
        *,
        migrate_to_scene_id: str | None = None,
        delete_shots: bool = False,
    ) -> None:
        def apply(next_state: dict[str, Any]) -> None:
            self._scene(next_state, scene_id)
            if len(next_state["scenes"]) <= 1:
                self._fail("preproduction_scene_required", "工作区必须保留至少一个场景。")
            affected = [shot for shot in next_state["shots"] if shot.get("sceneId") == scene_id]
            if affected and migrate_to_scene_id is None and not delete_shots:
                self._fail("preproduction_scene_impact_required", "删除场景前请选择迁移镜头或确认同时删除镜头。")
            if migrate_to_scene_id is not None:
                if migrate_to_scene_id == scene_id:
                    self._fail("preproduction_scene_invalid", "镜头不能迁移到正在删除的场景。", 422)
                self._scene(next_state, migrate_to_scene_id)
                for shot in affected:
                    shot["sceneId"] = migrate_to_scene_id
            elif delete_shots:
                if any(node.get("status") in ("queued", "running") for shot in affected for node in shot.get("nodes", [])):
                    self._fail("preproduction_node_running", "包含运行中节点的镜头不能随场景删除。")
                affected_ids = {shot["id"] for shot in affected}
                next_state["shots"] = [shot for shot in next_state["shots"] if shot["id"] not in affected_ids]
            next_state["scenes"] = [scene for scene in next_state["scenes"] if scene["id"] != scene_id]

        self._commit(apply)

    def _commit(self, change: Callable[[dict[str, Any]], None], *, increment_revision: bool = True) -> None:
        next_state = deepcopy(self.state)
        change(next_state)
        if increment_revision:
            next_state["revision"] = self.state["revision"] + 1
        self.state.clear()
        self.state.update(next_state)

    def _requested_shots(self, state: dict[str, Any], shot_ids: list[str]) -> list[dict[str, Any]]:
        if not shot_ids or len(shot_ids) != len(set(shot_ids)):
            self._fail("preproduction_shot_invalid", "请选择有效且不重复的镜头。", 422)
        by_id = {shot["id"]: shot for shot in state["shots"]}
        if any(shot_id not in by_id for shot_id in shot_ids):
            self._fail("preproduction_shot_missing", "镜头不存在。", 404)
        return [by_id[shot_id] for shot_id in shot_ids]

    def _scene(self, state: dict[str, Any], scene_id: Any) -> dict[str, Any]:
        scene = next((item for item in state["scenes"] if item["id"] == scene_id), None)
        if scene is None:
            self._fail("preproduction_scene_missing", "场景不存在。", 404)
        return scene

    @staticmethod
    def _fail(code: str, message: str, status: int = 409) -> None:
        raise ShotOrganizationError(code, message, status)
