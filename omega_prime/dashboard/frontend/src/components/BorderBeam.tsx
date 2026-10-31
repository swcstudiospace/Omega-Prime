import { motion, useReducedMotion } from 'motion/react'
export function BorderBeam() {
  const reduced = useReducedMotion()
  if (reduced) return null
  return <motion.span aria-hidden="true" className="border-beam" initial={{ left: '-15%' }} animate={{ left: ['-15%', '115%'] }} transition={{ duration: 4.5, repeat: Infinity, ease: 'linear' }} />
}
