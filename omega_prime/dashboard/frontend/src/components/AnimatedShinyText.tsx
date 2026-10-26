import type { ReactNode } from 'react'
import { useReducedMotion } from 'motion/react'
export function AnimatedShinyText({ children }: { children: ReactNode }) {
  const reduced = useReducedMotion()
  return <span className={reduced ? undefined : 'shiny-text'}>{children}</span>
}
