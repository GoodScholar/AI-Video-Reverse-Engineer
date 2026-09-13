export type ReferenceVideo = {
  type: "video";
  id: string;
  originalName: string;
  format: "mp4" | "mov";
  sizeBytes: number;
  durationSeconds: number;
  width: number;
  height: number;
  frameRate: number;
};

export type ReferenceImage = {
  type: "image";
  id: string;
  originalName: string;
  format: "jpeg" | "png" | "webp";
  sizeBytes: number;
  width: number;
  height: number;
  hasTransparency: boolean;
};

export type ReferenceMedia = ReferenceImage | ReferenceVideo;

export type PreprocessingStageName =
  | "decoding"
  | "sceneDetection"
  | "keyframeExtraction"
  | "motionAnalysis"
  | "reproducibilityAssessment";

export type PreprocessingStageState = {
  name: PreprocessingStageName;
  status: "pending" | "running" | "completed" | "failed";
  startedAt: string | null;
  completedAt: string | null;
};

export type LocalPreprocessingError = {
  code: string;
  message: string;
  stage: PreprocessingStageName;
  retryable: true;
};

export type ReproducibilityCheck = {
  criterion:
    | "single_shot"
    | "motion_range"
    | "primary_subject_count"
    | "complex_interaction";
  status: "passed" | "failed" | "pending" | "not_assessed";
  message: string;
  evidence: string;
};

export type LocalPreprocessing = {
  id: string;
  sourceReferenceMediaId: string;
  mediaType: "image" | "video";
  algorithmVersion: 1;
  status: "queued" | "running" | "completed" | "failed";
  currentStage: PreprocessingStageName | null;
  stages: PreprocessingStageState[];
  queuedAt: string;
  startedAt: string | null;
  updatedAt: string;
  completedAt: string | null;
  proxySummary: {
    keyframeCount: number;
    contactSheetCount: 1;
    sceneChangeCount: number;
    motionP50: number | null;
    motionP90: number | null;
    motionPeak: number | null;
    motionLevel: "light" | "moderate" | "high" | "unavailable";
  } | null;
  reproducibilityAssessment: {
    status: "out_of_scope" | "pending_semantic_confirmation";
    checks: ReproducibilityCheck[];
  } | null;
  error: LocalPreprocessingError | null;
};

export type DepthCaptureStageName =
  | "preparing"
  | "estimatingDepth"
  | "encoding"
  | "qualityAssessment";

export type DepthCaptureStageState<Name extends DepthCaptureStageName = DepthCaptureStageName> = {
  name: Name;
  status: "pending" | "running" | "completed" | "failed";
  startedAt: string | null;
  completedAt: string | null;
};

export type DepthCaptureStages = [
  DepthCaptureStageState<"preparing">,
  DepthCaptureStageState<"estimatingDepth">,
  DepthCaptureStageState<"encoding">,
  DepthCaptureStageState<"qualityAssessment">,
];

export type DepthDevicePreference = "auto" | "cuda" | "mps" | "cpu";
export type DepthExecutionDevice = "cuda" | "mps" | "cpu";

export type DepthCaptureError = {
  code: string;
  message: string;
  stage: DepthCaptureStageName;
  retryable: true;
};

export type DepthModelIdentity = {
  modelId: "video-depth-anything-small-relative";
  upstreamCommit: "4f5ae23172ba60fd7bc11ef671cca678842c7072";
  checkpointSha256: "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609";
};

export type DepthOutputSummary = {
  width: number;
  height: number;
  frameRate: number;
  frameCount: number;
  durationSeconds: number;
};

export type DepthQualityCriterion =
  | "completeness"
  | "dynamicRange"
  | "temporalFlicker"
  | "directionStability"
  | "edgeContinuity"
  | "timelineAlignment";

export type DepthQualityCheck<Criterion extends DepthQualityCriterion = DepthQualityCriterion> = {
  criterion: Criterion;
  status: "passed" | "review_required" | "failed";
  message: string;
  evidence: string;
  metric: number;
  threshold: number;
  sampleTimestamps: number[];
};

export type DepthQualityChecks = [
  DepthQualityCheck<"completeness">,
  DepthQualityCheck<"dynamicRange">,
  DepthQualityCheck<"temporalFlicker">,
  DepthQualityCheck<"directionStability">,
  DepthQualityCheck<"edgeContinuity">,
  DepthQualityCheck<"timelineAlignment">,
];

export type DepthQualityAssessment = {
  status: "passed" | "review_required" | "failed";
  checks: DepthQualityChecks;
  thresholdVersion: 1;
};

export type DepthCapture = {
  id: string;
  sourceReferenceVideoId: string;
  algorithmVersion: 1;
  status: "queued" | "running" | "completed" | "failed";
  devicePreference: DepthDevicePreference;
  executionDevice: DepthExecutionDevice | null;
  modelIdentity: DepthModelIdentity;
  normalizationDirection: "near_white_far_black";
  currentStage: DepthCaptureStageName | null;
  stages: DepthCaptureStages;
  outputSummary: DepthOutputSummary | null;
  qualityAssessment: DepthQualityAssessment | null;
  reviewConfirmedAt: string | null;
  error: DepthCaptureError | null;
  queuedAt: string;
  startedAt: string | null;
  updatedAt: string;
  completedAt: string | null;
};

export type Project = {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  referenceMedia: ReferenceMedia | null;
  localPreprocessing: LocalPreprocessing | null;
  depthCaptures?: DepthCapture[];
  activeDepthCaptureId?: string | null;
};

export type Capability = {
  state: "checking" | "unavailable" | "unconfigured" | "disconnected";
  label: string;
};

export type Capabilities = {
  analysisService: Capability;
  localComfyui: Capability;
};
