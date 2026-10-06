import { useEffect, useState } from 'react'

/** Seconds left of a wait (a rate limit), counting down once a second; 0 when there is nothing to wait for. */
export function useCountdown(seconds: number | null): number {
  const [left, setLeft] = useState(seconds ?? 0)
  useEffect(() => {
    setLeft(seconds ?? 0)
    if (!seconds) return
    const timer = setInterval(() => setLeft((value) => (value > 1 ? value - 1 : 0)), 1000)
    return () => clearInterval(timer)
  }, [seconds])
  return left
}
