// When the list of messages should follow new text. Pure; tested in scroll.test.ts. Erasable TypeScript only.
export const FOLLOW_MARGIN = 48          // pixels: about two lines of text of slack

/** Is the list at (or within a couple of lines of) its end? Numbers that cannot be real count as yes: the newest text is never hidden by a glitch. */
export function isNearBottom(box: { scrollTop: number, clientHeight: number, scrollHeight: number }, margin = FOLLOW_MARGIN): boolean {
  const distance = box.scrollHeight - box.scrollTop - box.clientHeight
  return !Number.isFinite(distance) || distance <= margin
}
