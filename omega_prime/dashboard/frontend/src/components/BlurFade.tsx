import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'motion/react'
export function BlurFade({ children, className }: { children: ReactNode; className?: string }) {
  const reduced = useReducedMotion()
  return <motion.div className={className} initial={reduced ? false : { opacity: 1, y: 5, filter: 'blur(1px)' }} animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }} transition={{ duration: reduced ? 0 : 0.24 }}>{children}</motion.div>
}
