import { useEffect, useRef, useState } from "react";
import { CircleAlert, CircleCheck, Upload } from "lucide-react";

import type { Project, ReferenceImage, ReferenceMedia, ReferenceVideo } from "./models";
import { referenceMediaContentUrl, uploadReferenceMedia } from "./referenceMediaApi";

const DESKTOP_UPLOAD_QUERY = "(min-width: 1024px)";
const IMAGE_MAX_BYTES = 30_000_000;
const VIDEO_MAX_BYTES = 200_000_000;

type UploadOperation =
  | { kind: "idle" }
  | { kind: "uploading"; replacing: boolean }
  | { kind: "error"; message: string }
  | { kind: "confirmingReplacement"; file: File; type: ReferenceMedia["type"] }
  | { kind: "replacementError"; message: string };

type Props = {
  project: Project;
  onProjectUpdated: (project: Project) => void;
  upload?: typeof uploadReferenceMedia;
  preprocessingLocked?: boolean;
  hasPreprocessingResult?: boolean;
};

function useDesktopUpload() {
  const [isDesktop, setIsDesktop] = useState(() => (
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(DESKTOP_UPLOAD_QUERY).matches
      : false
  ));
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const mediaQuery = window.matchMedia(DESKTOP_UPLOAD_QUERY);
    const update = () => setIsDesktop(mediaQuery.matches);
    update();
    mediaQuery.addEventListener("change", update);
    return () => mediaQuery.removeEventListener("change", update);
  }, []);
  return isDesktop;
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(2)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
}

function mediaLabel(type: ReferenceMedia["type"]) {
  return type === "image" ? "参考图片" : "参考视频";
}

function Metadata({ media }: { media: ReferenceMedia }) {
  if (media.type === "image") return <ImageMetadata image={media} />;
  return <VideoMetadata video={media} />;
}

function ImageMetadata({ image }: { image: ReferenceImage }) {
  return (
    <dl className="reference-media-metadata">
      <div><dt>格式</dt><dd>{image.format.toUpperCase()}</dd></div>
      <div><dt>体积</dt><dd title={`${image.sizeBytes} B`}>{formatFileSize(image.sizeBytes)}</dd></div>
      <div><dt>分辨率</dt><dd>{image.width}×{image.height}</dd></div>
      <div><dt>透明区域</dt><dd>{image.hasTransparency ? "存在" : "无"}</dd></div>
    </dl>
  );
}

function VideoMetadata({ video }: { video: ReferenceVideo }) {
  return (
    <dl className="reference-media-metadata">
      <div><dt>格式</dt><dd>{video.format.toUpperCase()}</dd></div>
      <div><dt>体积</dt><dd title={`${video.sizeBytes} B`}>{formatFileSize(video.sizeBytes)}</dd></div>
      <div><dt>时长</dt><dd>{video.durationSeconds.toFixed(2)} 秒</dd></div>
      <div><dt>分辨率</dt><dd>{video.width}×{video.height}</dd></div>
      <div><dt>帧率</dt><dd>{video.frameRate.toFixed(2)} fps</dd></div>
    </dl>
  );
}

function candidateType(file: File): ReferenceMedia["type"] | null {
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (["jpg", "jpeg", "png", "webp"].includes(extension ?? "")) return "image";
  if (["mp4", "mov"].includes(extension ?? "")) return "video";
  return null;
}

function clientFileError(file: File) {
  const type = candidateType(file);
  if (!type) return "仅支持 JPG、JPEG、PNG、WebP、MP4 或 MOV 格式的参考素材。";
  if (type === "image" && file.size > IMAGE_MAX_BYTES) return "参考图片不能超过 30 MB。";
  if (type === "video" && file.size > VIDEO_MAX_BYTES) return "参考视频不能超过 200 MB。";
  return null;
}

export function ReferenceMediaPanel({
  project,
  onProjectUpdated,
  upload = uploadReferenceMedia,
  preprocessingLocked = false,
  hasPreprocessingResult = false,
}: Props) {
  const isDesktop = useDesktopUpload();
  const [displayedProject, setDisplayedProject] = useState(project);
  const [operation, setOperation] = useState<UploadOperation>({ kind: "idle" });
  const [successMessage, setSuccessMessage] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const selectButtonRef = useRef<HTMLButtonElement>(null);
  const changeButtonRef = useRef<HTMLButtonElement>(null);
  const summaryRef = useRef<HTMLHeadingElement>(null);
  const shouldRestoreFocus = useRef<"select" | "change" | "summary" | null>(null);
  const acceptedProjectRef = useRef<Project | null>(null);
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const currentProjectIdRef = useRef(project.id);
  currentProjectIdRef.current = project.id;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; requestGenerationRef.current += 1; };
  }, []);

  useEffect(() => {
    const accepted = acceptedProjectRef.current;
    if (accepted && accepted.id === project.id && accepted.updatedAt === project.updatedAt
      && accepted.referenceMedia?.id === project.referenceMedia?.id
      && accepted.referenceMedia?.type === project.referenceMedia?.type) {
      acceptedProjectRef.current = null;
      return;
    }
    acceptedProjectRef.current = null;
    requestGenerationRef.current += 1;
    setDisplayedProject(project);
    setOperation({ kind: "idle" });
    setSuccessMessage("");
  }, [project.id, project.referenceMedia?.id, project.referenceMedia?.type]);

  useEffect(() => {
    if (shouldRestoreFocus.current === "select") selectButtonRef.current?.focus();
    if (shouldRestoreFocus.current === "change") changeButtonRef.current?.focus();
    if (shouldRestoreFocus.current === "summary") summaryRef.current?.focus();
    shouldRestoreFocus.current = null;
  }, [operation, successMessage, displayedProject]);

  const referenceMedia = displayedProject.referenceMedia;
  const hasReferenceMedia = referenceMedia != null;
  const isUploading = operation.kind === "uploading";
  const isLocked = isUploading || preprocessingLocked;
  const error = operation.kind === "error" || operation.kind === "replacementError" ? operation.message : "";

  function showClientError(message: string) {
    shouldRestoreFocus.current = hasReferenceMedia ? "change" : "select";
    setOperation(hasReferenceMedia ? { kind: "replacementError", message } : { kind: "error", message });
  }

  async function performUpload(file: File, replacing: boolean) {
    if (isLocked) return;
    const requestGeneration = ++requestGenerationRef.current;
    const requestProjectId = project.id;
    const isCurrentRequest = () => mountedRef.current && requestGenerationRef.current === requestGeneration
      && currentProjectIdRef.current === requestProjectId;
    setSuccessMessage("");
    setOperation({ kind: "uploading", replacing });
    try {
      const updatedProject = await upload(requestProjectId, file);
      if (!isCurrentRequest()) return;
      acceptedProjectRef.current = updatedProject;
      onProjectUpdated(updatedProject);
      if (!isCurrentRequest()) return;
      setDisplayedProject(updatedProject);
      shouldRestoreFocus.current = "summary";
      setOperation({ kind: "idle" });
      setSuccessMessage("参考素材已通过校验");
    } catch (cause) {
      if (!isCurrentRequest()) return;
      const message = cause instanceof Error ? cause.message : "无法上传并校验参考素材，请检查文件后重试。";
      shouldRestoreFocus.current = replacing ? "change" : "select";
      setOperation(replacing ? { kind: "replacementError", message } : { kind: "error", message });
    }
  }

  function selectFile(file: File) {
    if (isLocked) return;
    const validationError = clientFileError(file);
    if (validationError) return showClientError(validationError);
    const type = candidateType(file)!;
    if (hasReferenceMedia) {
      setSuccessMessage("");
      setOperation({ kind: "confirmingReplacement", file, type });
      return;
    }
    void performUpload(file, false);
  }

  function dropZone(copy: string) {
    return (
      <div className="reference-media-dropzone" data-testid="reference-media-dropzone"
        onDragOver={(event) => { if (!isLocked) event.preventDefault(); }}
        onDrop={(event) => { event.preventDefault(); if (!isLocked && event.dataTransfer.files?.[0]) selectFile(event.dataTransfer.files[0]); }}>
        <p>{copy}</p>
        {!hasReferenceMedia && <p>支持 JPG、JPEG、PNG、WebP、MP4 或 MOV。图片最大 30 MB；视频最大 200 MB，时长 2～300 秒（5 分钟），最高 UHD 4K。低分辨率可能影响分析细节。</p>}
      </div>
    );
  }

  if (!isDesktop) return (
    <section className="reference-media-panel reference-media-panel--readonly" aria-labelledby="reference-media-title">
      <h2 id="reference-media-title">参考素材</h2>
      {hasReferenceMedia ? <MediaSummary media={referenceMedia} projectId={project.id} headingRef={summaryRef} reveal={false} />
        : <p>请在宽度至少 1024px 的桌面设备添加参考素材</p>}
    </section>
  );

  return (
    <section className="reference-media-panel" aria-labelledby="reference-media-title">
      <h2 id="reference-media-title">参考素材</h2>
      <input ref={fileInputRef} className="visually-hidden" id="reference-media-file" type="file" tabIndex={-1}
        accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,image/jpeg,image/png,image/webp,video/mp4,video/quicktime"
        aria-label="参考素材文件" disabled={isLocked}
        onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) selectFile(file); event.currentTarget.value = ""; }} />
      <label className="visually-hidden" htmlFor="reference-media-file">参考素材文件</label>
      {hasReferenceMedia && <MediaSummary media={referenceMedia} projectId={project.id} headingRef={summaryRef} reveal={Boolean(successMessage)} />}
      {preprocessingLocked && <p className="reference-media-lock-notice">本地预处理或深度捕捉运行时不能更换参考素材</p>}
      {operation.kind === "confirmingReplacement" ? (
        <div className="reference-media-confirmation" aria-labelledby="replacement-title">
          <p id="replacement-title">将以 {operation.file.name} 替换当前{mediaLabel(referenceMedia!.type)}。</p>
          {operation.type !== referenceMedia!.type
            ? <p>从{mediaLabel(referenceMedia!.type)}替换为{mediaLabel(operation.type)}后，本地预处理及后续分析、方案、工作流结果会失效。</p>
            : hasPreprocessingResult && <p>成功替换后，已有本地预处理结果将失效。</p>}
          <div className="reference-media-actions">
            <button className="secondary-action" type="button" onClick={() => { shouldRestoreFocus.current = "change"; setOperation({ kind: "idle" }); }}>取消</button>
            <button className="primary-action" type="button" disabled={isLocked} onClick={() => void performUpload(operation.file, true)}>替换参考素材</button>
          </div>
        </div>
      ) : <>
        {dropZone(hasReferenceMedia ? "拖放新文件以更换参考素材。" : "拖放参考图片或视频到这里，或使用下方选择按钮。")}
        <div className="reference-media-actions">
          <button ref={hasReferenceMedia ? changeButtonRef : selectButtonRef} className={hasReferenceMedia ? "secondary-action" : "primary-action"}
            type="button" disabled={isLocked} onClick={() => { if (!isLocked) fileInputRef.current?.click(); }}>
            {isUploading ? "正在上传并校验参考素材…" : hasReferenceMedia ? "更换参考素材" : "选择参考素材"}
          </button>
        </div>
      </>}
      {isUploading && <p className="reference-media-status reference-media-status--uploading" role="status" aria-live="polite"><Upload aria-hidden="true" size={17} />正在上传并校验参考素材…</p>}
      {successMessage && <p className="reference-media-status" role="status" aria-live="polite"><CircleCheck aria-hidden="true" size={17} />{successMessage}</p>}
      {error && <p className="reference-media-error" role="alert"><CircleAlert aria-hidden="true" size={17} /><span><strong>参考素材未通过校验</strong>{error}</span></p>}
    </section>
  );
}

function MediaSummary({ media, projectId, headingRef, reveal }: { media: ReferenceMedia; projectId: string; headingRef: React.RefObject<HTMLHeadingElement>; reveal: boolean }) {
  return <div className={`reference-media-summary${reveal ? " reference-media-summary--revealed" : ""}`}>
    <p className="reference-media-kind">{mediaLabel(media.type)}</p>
    <h3 ref={headingRef} tabIndex={-1}>{media.originalName}</h3>
    {media.type === "image" && <><img className="reference-media-image-preview" src={referenceMediaContentUrl(projectId, media.id)} alt={`参考图片预览：${media.originalName}`} />
      {media.hasTransparency && <p className="reference-media-transparency">透明区域将在本地预处理时以白色背景处理。</p>}</>}
    <Metadata media={media} />
  </div>;
}
