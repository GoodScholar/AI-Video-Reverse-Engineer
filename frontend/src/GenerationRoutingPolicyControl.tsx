import { useEffect, useMemo, useState } from "react";

import {
  listGenerationRoutingPolicies,
  saveGenerationRoutingPolicy,
  type GenerationRoute,
  type GenerationRoutingPolicyRecord,
  type GenerationRoutingPolicyRef,
} from "./generationRoutingApi";

type Modality = "text" | "image" | "video";

export function GenerationRoutingPolicyControl({
  modality, currentRoute, verifiedRoutes, value, onChange,
}: {
  modality: Modality;
  currentRoute: GenerationRoute;
  verifiedRoutes: GenerationRoute[];
  value?: GenerationRoutingPolicyRef;
  onChange: (next?: GenerationRoutingPolicyRef) => void;
}) {
  const [revision, setRevision] = useState(0);
  const [policies, setPolicies] = useState<GenerationRoutingPolicyRecord[]>([]);
  const [backup, setBackup] = useState("");
  const [notice, setNotice] = useState("");
  const [loaded, setLoaded] = useState(false);
  const routeKey = (route: GenerationRoute) => `${route.provider}\u0000${route.model}`;
  const routes = useMemo(() => [...new Map(verifiedRoutes.map((route) => [routeKey(route), route])).values()], [verifiedRoutes]);
  const eligible = (policy: GenerationRoutingPolicyRecord) => policy.routes.length > 0
    && routeKey(policy.routes[0]) === routeKey(currentRoute)
    && policy.routes.every((route) => routes.some((candidate) => routeKey(candidate) === routeKey(route)));
  const eligiblePolicies = useMemo(() => policies.filter(eligible), [policies, currentRoute.provider, currentRoute.model, routes]);

  useEffect(() => {
    setBackup((current) => current === routeKey(currentRoute) || !routes.some((route) => routeKey(route) === current) ? "" : current);
    if (value) {
      const policy = eligiblePolicies.find((item) => item.id === value.id);
      if (!policy || !eligible(policy)) onChange(undefined);
      else if (value.revision !== policy.revision) onChange({ id: policy.id, revision: policy.revision });
    }
  }, [currentRoute.provider, currentRoute.model, routes, eligiblePolicies, value?.id, value?.revision]);

  async function load() {
    try {
      const result = await listGenerationRoutingPolicies(modality);
      setRevision(typeof result.revision === "number" ? result.revision : 0);
      setPolicies((Array.isArray(result.policies) ? result.policies : []).filter(eligible)); setLoaded(true); setNotice("");
    } catch { setNotice("无法读取已保存路由策略。"); }
  }

  async function save() {
    if (!loaded) { setNotice("请先读取已保存策略，以取得最新版本后再保存。"); return; }
    const selected = [currentRoute, ...(backup ? [routes.find((route) => routeKey(route) === backup)!] : [])];
    if (!routes.some((route) => routeKey(route) === routeKey(currentRoute)) || new Set(selected.map(routeKey)).size !== selected.length) {
      setNotice("请选择不同且已完成能力验证的主路由与备用路由。"); return;
    }
    try {
      const id = `${modality}-routing-policy`;
      const result = await saveGenerationRoutingPolicy({ id, name: `${modality} 已验证路由`, modality, routes: selected }, revision);
      setRevision(result.policy.revision);
      setPolicies((current) => [...current.filter((policy) => policy.id !== id), result.policy]);
      onChange({ id, revision: result.policy.revision }); setNotice("已按当前顺序保存路由策略。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "保存路由策略失败。"); }
  }

  return <fieldset className="generation-routing-policy" aria-label="生成路由策略">
    <legend>已保存的路由策略</legend>
    <p>仅列出已完成精确能力验证的候选。连接成功本身不能作为生成或备用路由授权。</p>
    {!routes.length ? <p role="status">当前没有已完成能力验证的候选，不能保存或使用路由策略。</p> : <>
      <p>主路由固定为当前表单选择：{currentRoute.provider} · {currentRoute.model}</p>
      <label>备用路由（可选）<select value={backup} onChange={(event) => setBackup(event.target.value)}>
        <option value="">不设置备用</option>{routes.filter((route) => routeKey(route) !== routeKey(currentRoute)).map((route) => <option key={routeKey(route)} value={routeKey(route)}>{route.provider} · {route.model}</option>)}
      </select></label>
      <button type="button" onClick={() => void save()}>保存当前顺序</button>
    </>}
    <button type="button" onClick={() => void load()}>读取已保存策略</button>
    <label>提交时使用<select disabled={!loaded} value={value ? `${value.id}:${value.revision}` : ""} onChange={(event) => {
      const [id, savedRevision] = event.target.value.split(":"); onChange(id ? { id, revision: Number(savedRevision) } : undefined);
    }}><option value="">不使用备用路由</option>{eligiblePolicies.map((policy) => <option key={policy.id} value={`${policy.id}:${policy.revision}`}>{policy.name}：{policy.routes.map((route) => `${route.provider}/${route.model}`).join(" → ")}</option>)}</select></label>
    {notice && <p role="status">{notice}</p>}
  </fieldset>;
}
