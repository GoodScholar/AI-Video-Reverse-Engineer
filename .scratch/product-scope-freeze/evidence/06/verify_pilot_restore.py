#!/usr/bin/env python3
"""在临时数据副本中验证 P0 真实试点的备份、恢复与持久化边界。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


PILOT_PROJECT_ID = "697b8d71-b525-4011-ab90-b7f4563bc8f2"
EXPECTED_EXPORTS = {
    "a815aca7e9544592ada771e57b2c6085": {
        "exportId": "d4fdb668d417479e9d7d18597999a238",
        "previewRunId": "5517952a4a1f47c08c7006c3ce3204ef",
        "sha256": "1245a21b2d8a13069aaa3a0fe64ddb93ff7120b106083eaa80be9c2470f0ff46",
    },
    "1a2bde6e1556404a8d952e626b2c1e97": {
        "exportId": "e498624a8d12463994b5774019be2741",
        "previewRunId": "1da0f37c7f194e9a9404135310c42815",
        "sha256": "30c58212ad26f1ce1118cd4fa17606e9710cbafdc31e68c55f1deaf4b9691a4d",
    },
}
REFERENCED_ASSET_ID = "asset-e4af7b3f8a514a908590b200ed3e405e"


def require(response, expected: int = 200):
    assert response.status_code == expected, response.text
    return response


def normalize_project_urls(value, project_id: str):
    if isinstance(value, dict):
        return {key: normalize_project_urls(item, project_id) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_project_urls(item, project_id) for item in value]
    if isinstance(value, str):
        return value.replace(project_id, "{projectId}")
    return value


def first_difference(left, right, location: str = "root") -> str | None:
    if type(left) is not type(right):
        return f"{location}: {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return f"{location}: keys {sorted(left)} != {sorted(right)}"
        for key in left:
            difference = first_difference(left[key], right[key], f"{location}.{key}")
            if difference:
                return difference
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{location}: length {len(left)} != {len(right)}"
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            difference = first_difference(left_item, right_item, f"{location}[{index}]")
            if difference:
                return difference
        return None
    if left != right:
        return f"{location}: {left!r} != {right!r}"
    return None


def persistent_snapshot(client: TestClient, project_id: str) -> dict:
    """读取 P0 持久化公共投影；画布布局修订不属于本票业务恢复契约。"""
    preproduction = require(client.get(f"/api/projects/{project_id}/preproduction")).json()
    preproduction.pop("canvasLayout", None)
    return normalize_project_urls(
        {
            "preproduction": preproduction,
            "aigcContent": require(client.get(f"/api/projects/{project_id}/aigc-content")).json(),
            "batchEdits": require(client.get(f"/api/projects/{project_id}/batch-edits")).json(),
            "contentWorkflow": require(client.get(f"/api/projects/{project_id}/content-workflow")).json(),
        },
        project_id,
    )


def file_summary(client: TestClient, project_id: str) -> dict:
    preproduction = require(client.get(f"/api/projects/{project_id}/preproduction")).json()
    assets = {asset["id"]: asset for asset in preproduction["assets"]}
    asset_files = []
    for asset_id in (
        "asset-e4af7b3f8a514a908590b200ed3e405e",
        "asset-901128fa52d64661bf09bf53751043bb",
    ):
        content = require(client.get(assets[asset_id]["url"])).content
        asset_files.append(
            {"assetId": asset_id, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        )

    batch = require(client.get(f"/api/projects/{project_id}/batch-edits")).json()
    tasks = {task["id"]: task for task in batch["tasks"]}
    preview_files = []
    for task_id, expected in EXPECTED_EXPORTS.items():
        run = next(item for item in tasks[task_id]["variant"]["runs"] if item["id"] == expected["previewRunId"])
        assert run["status"] == "completed"
        content = require(client.get(run["url"])).content
        preview_files.append(
            {
                "taskId": task_id,
                "previewRunId": run["id"],
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return {"allowedAssets": asset_files, "currentPreviews": preview_files}


def batch_summary(client: TestClient, project_id: str) -> dict:
    payload = require(client.get(f"/api/projects/{project_id}/batch-edits")).json()
    tasks = {task["id"]: task for task in payload["tasks"]}
    exports = []
    for task_id, expected in EXPECTED_EXPORTS.items():
        task = tasks[task_id]
        export = next(item for item in task["variant"]["exports"] if item["id"] == expected["exportId"])
        output = require(
            client.get(
                f"/api/projects/{project_id}/batch-edits/{task_id}/exports/{export['id']}/output"
            )
        ).content
        digest = hashlib.sha256(output).hexdigest()
        assert digest == expected["sha256"]
        assert task["reviewStatus"] == "approved"
        assert export["status"] == "completed"
        assert export["previewRunId"] == expected["previewRunId"]
        assert export["review"]["decision"] == "approved"
        assert (export["contentRevision"], export["variantRevision"], export["subtitleRevision"]) == (2, 4, 2)
        exports.append(
            {
                "taskId": task_id,
                "variantId": task["variant"]["id"],
                "previewRunId": export["previewRunId"],
                "reviewId": export["review"]["id"],
                "exportId": export["id"],
                "versions": [2, 4, 2],
                "sha256": digest,
                "bytes": len(output),
            }
        )
    workflow = require(client.get(f"/api/projects/{project_id}/content-workflow")).json()
    assert workflow["counts"] == {
        "total": 5,
        "pending": 3,
        "approved": 0,
        "rejected": 0,
        "delivered": 2,
        "failed": 0,
    }
    assert {item["taskId"] for item in workflow["variants"] if item["stage"] == "delivered"} == set(
        EXPECTED_EXPORTS
    )
    content = require(client.get(f"/api/projects/{project_id}/aigc-content")).json()
    assert content["brief"]["revision"] == 4
    assert content["brief"]["assetIds"] == [
        "asset-e4af7b3f8a514a908590b200ed3e405e",
        "asset-901128fa52d64661bf09bf53751043bb",
    ]
    assert len(content["candidates"]) == 5
    assert all(candidate["confirmedRevision"] == candidate["revision"] for candidate in content["candidates"])
    handoff_tasks = [task for task in payload["tasks"] if task.get("generationId") == "a5c21efddf994128bc3e747c40e71c96"]
    assert len(handoff_tasks) == 5
    assert {task["aigcSource"]["candidateId"] for task in handoff_tasks} == {
        candidate["id"] for candidate in content["candidates"]
    }
    assert all(
        task["batchId"] == "571545fe92234cc999f52161ac650b18"
        and task["aigcSource"]["briefRevision"] == 4
        and task["aigcSource"]["generationId"] == "332a1aff24994a0d84b9805cfcb96dce"
        for task in handoff_tasks
    )
    return {
        "taskCount": len(payload["tasks"]),
        "workflowCounts": workflow["counts"],
        "briefRevision": content["brief"]["revision"],
        "briefAssetIds": content["brief"]["assetIds"],
        "confirmedCandidates": [
            {
                "id": candidate["id"],
                "revision": candidate["revision"],
                "confirmedRevision": candidate["confirmedRevision"],
            }
            for candidate in content["candidates"]
        ],
        "handoff": {
            "batchId": "571545fe92234cc999f52161ac650b18",
            "generationId": "a5c21efddf994128bc3e747c40e71c96",
            "sourceGenerationId": "332a1aff24994a0d84b9805cfcb96dce",
            "taskIds": [task["id"] for task in handoff_tasks],
        },
        "exports": exports,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("backend/data"))
    parser.add_argument("--project-id", default=PILOT_PROJECT_ID)
    args = parser.parse_args()

    source_data = args.data_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="aivre-p0-acceptance-") as temporary:
        copied_data = Path(temporary) / "data"
        shutil.copytree(source_data, copied_data)

        with TestClient(create_app(data_dir=copied_data)) as client:
            source_project = require(client.get(f"/api/projects/{args.project_id}")).json()
            source_snapshot = persistent_snapshot(client, args.project_id)
            source_files = file_summary(client, args.project_id)
            before = batch_summary(client, args.project_id)
            backup = require(client.post(f"/api/projects/{args.project_id}/backup", json={}))
            backup_digest = hashlib.sha256(backup.content).hexdigest()
            restored = require(
                client.post(
                    "/api/project-backups/restore",
                    content=backup.content,
                    headers={"Content-Type": "application/zip"},
                ),
                201,
            ).json()
            restored_id = restored["id"]
            assert restored_id != args.project_id
            assert restored["name"].startswith(source_project["name"])
            restored_snapshot = persistent_snapshot(client, restored_id)
            assert restored_snapshot == source_snapshot, first_difference(source_snapshot, restored_snapshot)
            assert file_summary(client, restored_id) == source_files
            after_restore = batch_summary(client, restored_id)
            assert after_restore == before

            preproduction = require(client.get(f"/api/projects/{restored_id}/preproduction")).json()
            references = require(
                client.get(
                    f"/api/projects/{restored_id}/preproduction/assets/{REFERENCED_ASSET_ID}/references"
                )
            ).json()["references"]
            assert {item["kind"] for item in references} >= {
                "content_brief",
                "script_candidate",
                "batch_variant",
            }
            blocked_delete = client.post(
                f"/api/projects/{restored_id}/preproduction/assets/{REFERENCED_ASSET_ID}/delete",
                json={"revision": preproduction["revision"]},
            )
            assert blocked_delete.status_code == 409, blocked_delete.text

            disabled_routes = []
            for module in ("reproduction", "character-motion"):
                for suffix in ("/runs", "/runs/previous/refresh", "/runs/previous/resolve"):
                    path = f"/api/projects/{restored_id}/{module}{suffix}"
                    response = client.post(path, json={})
                    assert response.status_code == 410, response.text
                    assert response.json()["detail"]["code"] == "final_generation_disabled"
                    disabled_routes.append(path)

        # 重新构造应用实例，验证完整恢复项目不依赖进程内状态。
        with TestClient(create_app(data_dir=copied_data)) as restarted:
            restarted_snapshot = persistent_snapshot(restarted, restored_id)
            assert restarted_snapshot == source_snapshot, first_difference(source_snapshot, restarted_snapshot)
            assert file_summary(restarted, restored_id) == source_files
            after_restart = batch_summary(restarted, restored_id)
            assert after_restart == before
            restored_project = require(restarted.get(f"/api/projects/{restored_id}")).json()

            # 在独立恢复项目中推进脚本版本，使旧批准失效，再重启检查阶段状态。
            batch_url = f"/api/projects/{restored_id}/batch-edits"
            tasks = {task["id"]: task for task in require(restarted.get(batch_url)).json()["tasks"]}
            first_id, second_id = EXPECTED_EXPORTS
            first = tasks[first_id]
            changed_payload = require(
                restarted.put(
                    f"{batch_url}/{first_id}/content",
                    json={
                        "revision": first["contentRevision"],
                        "sellingPoint": first["sellingPoint"],
                        "script": first["script"] + "（恢复阶段重启验证）",
                    },
                )
            ).json()
            changed = changed_payload.get("task", changed_payload)
            assert changed["contentRevision"] == 3
            changed_tasks = {task["id"]: task for task in require(restarted.get(batch_url)).json()["tasks"]}
            assert changed_tasks[first_id]["reviewStatus"] == "stale"
            assert changed_tasks[second_id]["reviewStatus"] == "approved"
            export_gate = restarted.post(
                f"{batch_url}/{first['batchId']}/exports",
                json={"taskIds": [first_id]},
            )
            assert export_gate.status_code == 409, export_gate.text

        with TestClient(create_app(data_dir=copied_data)) as stage_restarted:
            batch_url = f"/api/projects/{restored_id}/batch-edits"
            stage_tasks = {task["id"]: task for task in require(stage_restarted.get(batch_url)).json()["tasks"]}
            first_id, second_id = EXPECTED_EXPORTS
            assert stage_tasks[first_id]["reviewStatus"] == "stale"
            assert stage_tasks[second_id]["reviewStatus"] == "approved"
            assert stage_tasks[first_id]["contentRevision"] == 3
            assert len(stage_tasks[second_id]["variant"]["exports"]) == 1
            restarted_gate = stage_restarted.post(
                f"{batch_url}/{stage_tasks[first_id]['batchId']}/exports",
                json={"taskIds": [first_id]},
            )
            assert restarted_gate.status_code == 409, restarted_gate.text

        print(
            json.dumps(
                {
                    "result": "passed",
                    "sourceProjectId": args.project_id,
                    "sourceProjectName": source_project["name"],
                    "backup": {"sha256": backup_digest, "bytes": len(backup.content)},
                    "restoredProjectId": restored_id,
                    "restoredProjectName": restored_project["name"],
                    "p0PublicStateRoundTrip": {
                        "preproduction": True,
                        "aigcContent": True,
                        "batchEdits": True,
                        "contentWorkflow": True,
                    },
                    "restoredFiles": source_files,
                    "stableState": before,
                    "assetReferenceKinds": sorted({item["kind"] for item in references}),
                    "referencedAssetDeleteStatus": blocked_delete.status_code,
                    "disabledFinalGenerationRoutes": disabled_routes,
                    "restartVerified": True,
                    "stageRestart": {
                        "changedTaskId": first_id,
                        "changedContentRevision": 3,
                        "changedReviewStatus": "stale",
                        "siblingTaskId": second_id,
                        "siblingReviewStatus": "approved",
                        "staleExportStatus": restarted_gate.status_code,
                    },
                    "liveDataMutated": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
