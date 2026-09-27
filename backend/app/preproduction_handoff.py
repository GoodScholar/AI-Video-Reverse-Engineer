"""Human-readable handoff using only the package's portable public snapshot."""
import html
import json
from urllib.parse import quote


def _text(value: object) -> str:
    text = html.escape(str(value), quote=False)
    for character in ("\\", "`", "*", "_", "[", "]", "#", "|"):
        text = text.replace(character, "\\" + character)
    return text.replace("\r", "").replace("\n", " ")


def _link(label: str, path: str) -> str:
    return f"[{_text(label)}]({quote(path, safe='/')})"


def render_handoff(workspace: dict) -> str:
    """Describe the actual exported order, inputs, outputs and unresolved checks."""
    brief = workspace["brief"]
    assets = {asset["id"]: asset for asset in workspace["assets"]}
    labels = {node["kind"]: node["label"] for node in workspace["nodeCatalog"]}
    roles = {"character": "角色", "scene": "场景", "motion": "动作", "audio": "音频", "reference": "参考"}
    lines = [
        "# 视频制作交接说明", "",
        f"方案版本：{workspace['revision']}", "",
        "本包是素材与方案准备成果，不是成片，也不是通用可执行工作流。",
        "最终生成在外部工具完成；模型、节点、输入格式和效果需在目标环境验证。",
        "深度素材表示相对空间层次，不等于骨骼动作；姿态或遮罩也需确认目标模型的输入兼容性。", "",
        "## 创作需求", "",
    ]
    for key, label in (("theme", "主题"), ("purpose", "用途"), ("style", "风格"),
                       ("duration", "目标时长（秒）"), ("aspect", "画面比例"), ("mustPreserve", "必须保留")):
        lines.append(f"- {label}：{_text(brief.get(key) or '未填写')}")
    lines.append("- 输入类型：" + {"reference_video": "参考视频", "depth_video": "灰度深度视频", "white_model_video": "三维白模渲染视频"}.get(brief.get("inputKind", "reference_video"), "参考视频"))
    lines += ["", "## 按镜头接续制作", "",
              "解压整个目录后使用下列相对链接。各镜头时长为计划值；处理参数和实际媒体时长需分别核对。", ""]
    for index, shot in enumerate(workspace["shots"], 1):
        lines += [f"### {index}. {_text(shot['title'] or shot['id'])}", "",
                  f"镜头 ID：{_text(shot['id'])}；计划时长：{shot['duration']} 秒", "",
                  f"正向提示词：{_text(shot['prompt'] or '未填写')}", "",
                  f"负向提示词：{_text(shot['negativePrompt'] or '未填写')}", "", "素材：", ""]
        for asset_id in shot["assetIds"]:
            asset = assets[asset_id]
            lines.append(f"- {roles.get(asset['role'], asset['role'])}：{_link(asset['name'], asset['url'])}")
        if not shot["assetIds"]:
            lines.append("- 未绑定素材，请在外部制作前确认输入。")
        result = assets.get(shot.get("resultAssetId"))
        if result:
            lines.append("- 已关联生成结果（效果待验收）：" + _link(result["name"], result["url"]))
        for index, version in enumerate(shot.get("resultVersions", []), 1):
            asset = assets[version["assetId"]]
            status = "方案已变化或关联记录缺失，待复查" if version["planChanged"] else "已按当前方案人工检查" if version["reviewed"] else "尚未人工检查"
            adopted = " · 当前采用" if version["assetId"] == shot.get("resultAssetId") else ""
            lines.append(f"- 候选 {index}{adopted}：" + _link(asset["name"], asset["url"]) + "；" + status)
            if version.get("adoptionReason"):
                lines.append("  - 采用理由：" + _text(version["adoptionReason"]))
        for node in shot["nodes"]:
            lines += ["", f"步骤 {_text(node['id'])}：{_text(labels[node['kind']])}", ""]
            source = node["input"]
            if source.startswith("asset:"):
                asset = assets[source[6:]]
                lines.append("输入：" + _link(asset["name"], asset["url"]))
            else:
                lines.append("输入：" + (_text(source[5:]) + " 的产物" if source.startswith("node:") else "无需媒体输入"))
            lines += ["", "参数：" + _text(json.dumps(node["params"], ensure_ascii=False)), ""]
            lines.extend("- 产物：" + _link(item["name"], item["url"]) for item in node["artifacts"])
        lines.append("")
    lines += ["## 交付检查与后续验收", ""]
    for check in workspace["checks"]:
        location = " / ".join(check[key] for key in ("shotId", "nodeId") if check.get(key))
        lines.append(f"- {_text(location or '项目')}：{_text(check['message'])}")
    if not workspace["checks"]:
        lines.append("当前未发现准备阶段的交付阻断项；这不代表模型兼容性或生成质量已验收。")
    lines += ["", "1. 按镜头导入角色、场景、动作及声音素材，确认采用原素材还是对应步骤产物。",
              "2. 根据目标工具绑定输入和提示词；需要 ComfyUI 专用工作流时，从准备工具另行导出并验证候选模板。",
              "3. 先验证一个短镜头的外观、动作、背景和时长，再继续其他镜头。",
              "4. 修改项目后重新运行过期步骤并重新导出，避免混用不同版本；将生成视频关联到对应镜头，在本地剪辑页按镜头导入结果，再完成音频编辑和成片检查。",
              "", "结构化数据：[完整方案](workspace.json) · [镜头](shots.json) · [检查报告](delivery-checks.json)", ""]
    return "\n".join(lines)
