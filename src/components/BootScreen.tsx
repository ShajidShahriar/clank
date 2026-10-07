import { useEffect, useState } from 'react'

/** Covers the window while the backend starts (it can take a while), then fades away. It comes back if the backend is restarted. */
function BootScreen({ booting, message }: { booting: boolean, message: string }) {
  const [mounted, setMounted] = useState(booting)
  useEffect(() => { if (booting) setMounted(true) }, [booting])
  if (!mounted) return null
  return (
    <div
      role="status"
      aria-live="polite"
      onTransitionEnd={() => { if (!booting) setMounted(false) }}
      className={`absolute inset-0 z-40 flex flex-col items-center justify-center bg-bg transition-opacity duration-500 ease-out ${booting ? 'opacity-100' : 'pointer-events-none opacity-0'}`}
    >
      <div className="drag absolute inset-x-0 top-0 h-[52px]" />
      <h1 className="animate-breathe text-[34px] font-semibold tracking-[-0.025em] text-label">Clank</h1>
      <div className="mt-6 h-[3px] w-40 overflow-hidden rounded-full bg-fill" aria-hidden>
        <div className="animate-sweep h-full w-1/3 rounded-full bg-accent" />
      </div>
      <p className="mt-4 max-w-[320px] px-6 text-center text-xs text-label-3">{message}</p>
    </div>
  )
}

export default BootScreen
