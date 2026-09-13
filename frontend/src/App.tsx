import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  CircleAlert,
  Clapperboard,
  FolderOpen,
  HardDrive,
  MonitorCog,
  Plus,
  RadioTower,
  RefreshCw,
} from "lucide-react";
import type { Capabilities, Capability, Project } from "./models";
import { DepthCapturePanel } from "./DepthCapturePanel";
import { LocalPreprocessingPanel } from "./LocalPreprocessingPanel";
import { ReferenceMediaPanel } from "./ReferenceMediaPanel";
import { uploadReferenceMedia } from "./referenceMediaApi";

const defaultCapabilities: Capabilities = {
  analysisService: { state: "checking", label: "检查中" },
  localComfyui: { state: "checking", label: "检查中" },
};

const unavailableCapabilities: Capabilities = {
  analysisService: { state: "unavailable", label: "状态不可用" },
  localComfyui: { state: "unavailable", label: "状态不可用" },
};

const api = {
  async listProjects(): Promise<Project[]> {
    const response = await fetch("/api/projects");
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(body?.detail ?? "无法读取本地复刻项目");
    }
    return response.json() as Promise<Project[]>;
  },
  async getCapabilities(): Promise<Capabilities> {
    const response = await fetch("/api/capabilities");
    if (!response.ok) throw new Error("无法读取环境状态");
    return response.json() as Promise<Capabilities>;
  },
  async createProject(name: string): Promise<Project> {
    const response = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(body?.detail ?? "无法保存复刻项目，请重试。");
    }
    return response.json() as Promise<Project>;
  },
};

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "时间数据损坏";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function StatusLine({ icon: Icon, label, capability }: { icon: typeof RadioTower; label: string; capability: Capability }) {
  return (
    <div className="status-line">
      <Icon aria-hidden="true" size={17} strokeWidth={1.8} />
      <span className="status-label">{label}</span>
      <span className={`status-value ${capability.state}`}>
        <span aria-hidden="true" className="status-mark" />
        {capability.label}
      </span>
    </div>
  );
}

function ProjectMark() {
  return (
    <div className="project-mark" aria-hidden="true">
      <span>00:00</span>
      <Clapperboard size={21} strokeWidth={1.6} />
    </div>
  );
}

export function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities>(defaultCapabilities);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [depthMutationPending, setDepthMutationPending] = useState(false);
  const [shouldRestoreCreateFocus, setShouldRestoreCreateFocus] = useState(false);
  const newProjectButtonRef = useRef<HTMLButtonElement>(null);

  async function loadHome() {
    setIsLoading(true);
    setLoadError("");
    setCapabilities(defaultCapabilities);
    const [projectResult, capabilityResult] = await Promise.allSettled([
      api.listProjects(),
      api.getCapabilities(),
    ]);
    if (projectResult.status === "fulfilled") {
      setProjects(projectResult.value);
    } else {
      setLoadError(projectResult.reason instanceof Error ? projectResult.reason.message : "无法读取本地复刻项目。请检查数据目录后重试。");
    }
    if (capabilityResult.status === "fulfilled") {
      setCapabilities(capabilityResult.value);
    } else {
      setCapabilities(unavailableCapabilities);
    }
    setIsLoading(false);
  }

  async function refreshCapabilities() {
    setCapabilities(defaultCapabilities);
    try {
      setCapabilities(await api.getCapabilities());
    } catch {
      setCapabilities(unavailableCapabilities);
    }
  }

  function cancelCreating() {
    setShouldRestoreCreateFocus(true);
    setIsCreating(false);
  }

  function updateProject(updated: Project) {
    setSelectedProject((current) => current?.id === updated.id ? updated : current);
    setProjects((current) => current
      .map((project) => project.id === updated.id ? updated : project)
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt)));
  }

  async function uploadAndMergeProject(projectId: string, file: File): Promise<Project> {
    const updated = await uploadReferenceMedia(projectId, file);
    updateProject(updated);
    return updated;
  }

  useEffect(() => {
    void loadHome();
  }, []);

  useEffect(() => {
    if (!isCreating && shouldRestoreCreateFocus) {
      newProjectButtonRef.current?.focus();
      setShouldRestoreCreateFocus(false);
    }
  }, [isCreating, shouldRestoreCreateFocus]);

  async function submitProject() {
    const name = projectName.trim();
    if (!name || isSaving) return;
    setIsSaving(true);
    setSaveError("");
    try {
      const project = await api.createProject(name);
      setProjects((current) => [project, ...current]);
      setSelectedProject(project);
      setIsCreating(false);
      setProjectName("");
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "无法保存复刻项目，请重试。");
    } finally {
      setIsSaving(false);
    }
  }

  if (selectedProject) {
    const depthCaptureLocked = (selectedProject.depthCaptures ?? []).some((capture) => (
      capture.status === "queued" || capture.status === "running"
    ));
    return (
      <main className="app-shell project-page">
        <header className="topbar">
          <button className="brand-button" type="button" onClick={() => setSelectedProject(null)}>
            <span className="brand-mark"><Clapperboard aria-hidden="true" size={15} strokeWidth={1.8} /></span>
            <span>AI Video Reverse Engineer</span>
          </button>
          <div className="topbar-status" aria-label="环境状态">
            <StatusLine icon={RadioTower} label="分析服务" capability={capabilities.analysisService} />
            <StatusLine icon={MonitorCog} label="本地 ComfyUI" capability={capabilities.localComfyui} />
            {capabilities.analysisService.state === "unavailable" && (
              <button className="capability-retry" type="button" onClick={() => void refreshCapabilities()}><RefreshCw size={15} aria-hidden="true" />重新检测</button>
            )}
          </div>
        </header>

        <section className="project-intro" aria-labelledby="project-title">
          <button className="back-link" type="button" onClick={() => setSelectedProject(null)}>
            <ArrowLeft size={16} aria-hidden="true" /> 返回项目首页
          </button>
          <div className="project-intro-grid">
            <ProjectMark />
            <div>
              <h1 id="project-title">{selectedProject.name}</h1>
              <p className="muted">创建于 {formatTime(selectedProject.createdAt)} · 仅保存在本地</p>
            </div>
          </div>
          <ReferenceMediaPanel
            project={selectedProject}
            onProjectUpdated={updateProject}
            upload={uploadAndMergeProject}
            preprocessingLocked={
              selectedProject.localPreprocessing?.status === "queued"
              || selectedProject.localPreprocessing?.status === "running"
              || depthCaptureLocked
              || depthMutationPending
            }
            hasPreprocessingResult={selectedProject.localPreprocessing !== null}
          />
          <LocalPreprocessingPanel project={selectedProject} onProjectUpdated={updateProject} />
          <DepthCapturePanel project={selectedProject} onProjectUpdated={updateProject} onMutationPendingChange={setDepthMutationPending} />
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand" aria-label="AI Video Reverse Engineer">
          <span className="brand-mark"><Clapperboard aria-hidden="true" size={15} strokeWidth={1.8} /></span>
          <span>AI Video Reverse Engineer</span>
          <span className="brand-divider" aria-hidden="true" />
          <strong>AI 视频复刻分析器</strong>
        </div>
        <div className="topbar-status" aria-label="环境状态">
          <StatusLine icon={RadioTower} label="分析服务" capability={capabilities.analysisService} />
          <StatusLine icon={MonitorCog} label="本地 ComfyUI" capability={capabilities.localComfyui} />
          {capabilities.analysisService.state === "unavailable" && (
            <button className="capability-retry" type="button" onClick={() => void refreshCapabilities()}><RefreshCw size={15} aria-hidden="true" />重新检测</button>
          )}
        </div>
      </header>

      <section className="home-hero" aria-labelledby="home-title">
        <div>
          <h1 id="home-title">从一份参考素材开始一项可继续的复刻工作。</h1>
          <p className="lede">创建项目不会上传文件，也不要求账户、登录或已连接的本地 ComfyUI。</p>
        </div>
        <button className="primary-action" ref={newProjectButtonRef} type="button" onClick={() => { setIsCreating(true); setSaveError(""); }}>
          <Plus size={18} aria-hidden="true" /> 新建复刻项目
        </button>
      </section>

      {isCreating && (
        <section className="create-panel" aria-labelledby="create-project-title">
          <h2 id="create-project-title">为这次复刻起一个名称</h2>
          <p className="dialog-copy">项目与后续分析、修改和输出都将只保存在本地。</p>
          <form
            onSubmit={(event: FormEvent<HTMLFormElement>) => {
              event.preventDefault();
              void submitProject();
            }}
          >
            <label htmlFor="project-name">项目名称</label>
            <input
              autoFocus
              id="project-name"
              maxLength={100}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="例如：雨夜人像复刻"
              value={projectName}
            />
            {saveError && <p className="save-error" role="alert"><CircleAlert size={16} aria-hidden="true" />{saveError}</p>}
            <div className="form-actions">
              <button className="secondary-action" type="button" onClick={cancelCreating}>取消</button>
              <button className="primary-action" disabled={!projectName.trim() || isSaving} type="submit">
                <Clapperboard size={17} aria-hidden="true" />{isSaving ? "正在保存…" : "创建并进入项目"}
              </button>
            </div>
          </form>
        </section>
      )}

      <section className="project-area film-rail" aria-labelledby="recent-projects-title">
        <div className="section-heading">
          <div>
            <h2 id="recent-projects-title">最近复刻项目</h2>
          </div>
          {!isLoading && !loadError && <span className="project-count">{projects.length} 项</span>}
        </div>

        {isLoading && <p className="loading-line">正在读取本地复刻项目…</p>}

        {loadError && (
          <div className="inline-error" role="alert">
            <CircleAlert aria-hidden="true" size={18} />
            <span>{loadError}</span>
            <button type="button" onClick={() => void loadHome()}><RefreshCw size={15} aria-hidden="true" />重新读取</button>
          </div>
        )}

        {!isLoading && !loadError && projects.length === 0 && (
          <div className="empty-state">
            <HardDrive aria-hidden="true" size={28} strokeWidth={1.3} />
            <div>
              <h3>尚无复刻项目</h3>
              <p>从一个命名项目开始。参考图片或视频将在下一步添加。</p>
            </div>
          </div>
        )}

        {!isLoading && !loadError && projects.length > 0 && (
          <ul className="project-list" aria-label="最近复刻项目">
            {projects.map((project, index) => (
              <li key={project.id}>
                <button className="project-row" type="button" onClick={() => setSelectedProject(project)}>
                  <ProjectMark />
                  <span className="project-copy">
                    <strong>{project.name}</strong>
                    <span>最近更新 {formatTime(project.updatedAt)}</span>
                  </span>
                  <span className="row-time">{String(index + 1).padStart(2, "0")}</span>
                  <FolderOpen aria-hidden="true" size={19} strokeWidth={1.5} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

    </main>
  );
}
