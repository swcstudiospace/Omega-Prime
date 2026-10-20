import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useDashboard } from './useDashboard'
import { fixture } from '../test/fixtures'
class FakeEventSource { static latest: FakeEventSource; listeners = new Map<string, EventListener>(); onopen: (() => void) | null = null; onerror: (() => void) | null = null; closed = false; constructor(_url: string) { FakeEventSource.latest = this } addEventListener(name: string, callback: EventListener) { this.listeners.set(name, callback) } close() { this.closed = true } emit(data: unknown) { this.listeners.get('snapshot')?.(new MessageEvent('snapshot', { data: JSON.stringify(data) })) } }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
describe('useDashboard transport', () => {
  it('fetches once and consumes the named snapshot SSE event', async () => { vi.stubGlobal('EventSource', FakeEventSource); vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => fixture })); const { result, unmount } = renderHook(() => useDashboard()); await waitFor(() => expect(result.current.data).toEqual(fixture)); act(() => FakeEventSource.latest.emit({ ...fixture, generated_at: 'newer' })); expect(result.current.data?.generated_at).toBe('newer'); unmount(); expect(FakeEventSource.latest.closed).toBe(true) })
  it('starts a single polling interval on stream failure and cleans it on unmount', async () => { vi.useFakeTimers(); vi.stubGlobal('EventSource', FakeEventSource); vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline'))); const setIntervalSpy = vi.spyOn(globalThis, 'setInterval'); const { unmount } = renderHook(() => useDashboard()); act(() => FakeEventSource.latest.onerror?.()); expect(setIntervalSpy).toHaveBeenCalledTimes(1); unmount(); expect(FakeEventSource.latest.closed).toBe(true) })
})
