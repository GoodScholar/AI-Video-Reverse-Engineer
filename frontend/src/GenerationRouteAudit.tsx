import type { GenerationRouteAttempt } from "./generationRoutingApi";

const decisionText: Record<string, string> = {
  selected: "已选择该路由", completed: "该路由已完成", fallback: "因可确认且未计费的失败切换到下一候选",
  fallback_not_allowed: "未保存路由策略，未切换", fallback_exhausted: "已耗尽保存的候选，未切换",
  fallback_not_requested_cancelled: "用户已确认取消，未请求回退",
  fallback_blocked_unknown_outcome: "结果或计费状态未知，未切换", fallback_blocked_partial_result: "已有部分结果，未切换",
  fallback_blocked_remote_identity: "已取得远端任务标识，未切换", fallback_blocked_capability_unavailable: "冻结版本不可执行，未切换",
};

function failureText(code: string | null) {
  if (!code) return "无";
  if (code.includes("version_unavailable")) return "冻结的适配器或能力版本已不可用，请以新请求重新提交";
  if (code.includes("unknown")) return "结果未知，请先查询或人工确认";
  if (code.includes("capability")) return "该候选未完成所需能力验证";
  return "该次调用失败，请检查服务配置后重试";
}

export function GenerationRouteAudit({ attempts }: { attempts?: GenerationRouteAttempt[] }) {
  if (!attempts?.length) return null;
  return <details className="generation-route-audit"><summary>实际路由与回退审计</summary>{attempts.map((item, index) => <p key={`${item.provider}-${item.model}-${index}`}>
    第 {index + 1} 次：{item.provider} · {item.model}；适配器 {item.adapterId} / {item.adapterVersion}；配置 {item.configurationVersion}；能力 {item.capabilityVersion}。{decisionText[item.fallbackDecision] ?? "未自动切换"}；{failureText(item.failureCode)}。
  </p>)}</details>;
}
