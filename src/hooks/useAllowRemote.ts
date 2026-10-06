import { useCallback, useState } from 'react'
import { readFlag, writeFlag } from '../lib/flag'

const KEY = 'clank.allowRemote'

function storage() {
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}

/** May code excerpts be sent to a remote answer model? Off until the person turns it on, and remembered after that. */
export function useAllowRemote(): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState(() => readFlag(storage(), KEY, false))
  const set = useCallback((next: boolean) => {
    setValue(next)
    writeFlag(storage(), KEY, next)
  }, [])
  return [value, set]
}
