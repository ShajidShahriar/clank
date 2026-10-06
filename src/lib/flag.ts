// A true/false setting kept in the browser's storage. A missing, full or blocked storage means "the default", never a crash.
export type FlagStorage = { getItem(key: string): string | null, setItem(key: string, value: string): void }

export function readFlag(storage: FlagStorage | undefined, key: string, fallback: boolean): boolean {
  try {
    const value = storage?.getItem(key)
    return value === 'true' ? true : value === 'false' ? false : fallback
  } catch {
    return fallback
  }
}

export function writeFlag(storage: FlagStorage | undefined, key: string, value: boolean): void {
  try {
    storage?.setItem(key, value ? 'true' : 'false')
  } catch {
    /* blocked or full: the setting simply does not persist */
  }
}
