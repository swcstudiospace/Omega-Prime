import { describe, expect, it } from 'vitest'
import { fixture } from '../test/fixtures'
import { cardModel } from './taskCard'

const task = fixture.active![0]!
describe('task card model', () => {
  it('splits a well-formed 000NN - Title row and keeps its description', () => {
    const card = cardModel({ ...task, title: '00039 - Sandbox stack CI', display_id: '00374 - 00039 - Sandbox stack CI' })
    expect(card).toMatchObject({ seq: '00374', taskNumber: '00039', title: 'Sandbox stack CI', description: 'Compile and verify the release.', malformed: false, problems: [] })
  })
  it('flags rows without the 000NN prefix or a description, without rewriting them', () => {
    const row = { ...task, title: 'omp process 2088581', detail: null }
    const card = cardModel(row)
    expect(card.malformed).toBe(true)
    expect(card.problems).toEqual(['no 000NN - prefix', 'missing description'])
    expect(card.rawTitle).toBe('omp process 2088581')
    expect(row.title).toBe('omp process 2088581')
    expect(cardModel({ ...task, title: '', detail: '' }).problems).toEqual(['no 000NN - prefix', 'missing title', 'missing description'])
  })
  it('uses the finished summary as the description', () => {
    expect(cardModel({ ...fixture.finished![0]!, title: '00010 - Publish report' })).toMatchObject({ description: 'Report published.', malformed: false })
  })
  it('reads a waiting request number from its id and its blocker as the description', () => {
    expect(cardModel({ id: 'wait:00037-railway-login', ask: 'Log Railway back in', blocks: 'fleet deploy' })).toMatchObject({ taskNumber: '00037', title: 'Log Railway back in', description: 'fleet deploy', malformed: false })
    expect(cardModel({ id: 'wait-1', ask: 'Approve deployment' }).problems).toEqual(['no 000NN task number', 'missing description'])
  })
})
