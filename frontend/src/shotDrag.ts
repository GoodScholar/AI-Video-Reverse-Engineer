export const SHOT_DRAG_TYPE = "application/x-aivre-shot-id";

export function writeShotDrag(dataTransfer: DataTransfer, shotId: string) {
  dataTransfer.effectAllowed = "move";
  dataTransfer.setData(SHOT_DRAG_TYPE, shotId);
}

export function readShotDrag(dataTransfer: DataTransfer) {
  return dataTransfer.getData(SHOT_DRAG_TYPE);
}

export function hasShotDrag(dataTransfer: DataTransfer) {
  return Array.from(dataTransfer.types).includes(SHOT_DRAG_TYPE);
}
