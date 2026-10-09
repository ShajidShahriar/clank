export const NAME: string
export function needed(probe: { name: string, displayName: string, sameIcon: boolean }, wanted: string): { name: boolean, displayName: boolean, icon: boolean }
export function realOps(app: string, icon: string): Parameters<typeof brand>[0]
export function brand(ops: {
  read(key: string): string
  set(key: string, value: string): void
  sameIcon(): boolean
  copyIcon(): void
  running(): boolean
  sign(): void
}): { changed: boolean, skipped: string | null }
