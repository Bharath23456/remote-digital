export type AnnotationBox = { x: number; y: number; width: number; height: number };

export function getSafeAnnotationPosition(
  newMark: AnnotationBox,
  existingMarks: AnnotationBox[],
  step = 15
): { x: number; y: number } {
  const overlaps = (a: AnnotationBox, b: AnnotationBox) =>
    a.x < b.x + b.width &&
    a.x + a.width > b.x &&
    a.y < b.y + b.height &&
    a.y + a.height > b.y;
  const candidate = { ...newMark };
  for (let i = 0; i < 50; i++) {
    if (!existingMarks.some((mark) => overlaps(candidate, mark))) break;
    candidate.y += step;
  }
  return { x: candidate.x, y: candidate.y };
}
