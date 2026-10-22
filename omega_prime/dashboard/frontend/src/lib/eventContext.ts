import { createContext, useContext } from 'react'
import type { EventLogState } from '../hooks/useEventLog'

const noop = () => {}
export const EMPTY_EVENT_LOG: EventLogState = { events: [], toasts: [], log: noop, dismissToast: noop, clear: noop }
export const EventLogContext = createContext<EventLogState>(EMPTY_EVENT_LOG)
export function useEvents(): EventLogState { return useContext(EventLogContext) }
