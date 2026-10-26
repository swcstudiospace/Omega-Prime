import { useEffect, useState } from 'react'
import { useReducedMotion } from 'motion/react'
export function NumberTicker({ value }: { value: number }) {
  const reduced = useReducedMotion()
  const [shown, setShown] = useState(value)
  useEffect(() => { if (reduced) setShown(value); else { const start = shown; const duration = 450; const began = performance.now(); let frame = 0; const tick = (now: number) => { const progress = Math.min(1, (now - began) / duration); setShown(Math.round(start + (value - start) * progress)); if (progress < 1) frame = requestAnimationFrame(tick) }; frame = requestAnimationFrame(tick); return () => cancelAnimationFrame(frame) } }, [value, reduced])
  return <span aria-label={String(value)}>{new Intl.NumberFormat('en-US').format(shown)}</span>
}
