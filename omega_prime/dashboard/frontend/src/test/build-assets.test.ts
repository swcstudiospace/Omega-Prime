import { describe, expect, it } from 'vitest'
import { readdir, readFile, stat } from 'node:fs/promises'
import { gzipSync } from 'node:zlib'
import path from 'node:path'
const root = path.resolve(process.cwd(), '../static/cc')
async function filesIn(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true })
  const nested = await Promise.all(entries.map(entry => entry.isDirectory() ? filesIn(path.join(directory, entry.name)) : [path.join(directory, entry.name)]))
  return nested.flat()
}
describe('built static Command Center assets', () => {
  it.skipIf(process.env.CC_CHECK_BUILD !== '1')('contains local hashed assets, fonts, licenses and no runtime external references', async () => {
    const htmlPath = path.join(root, 'index.html')
    const html = await readFile(htmlPath, 'utf8')
    const files = await filesIn(root)
    const assets = files.filter(file => file.includes(`${path.sep}assets${path.sep}`))
    expect(html).toContain('/legacy')
    expect(html).not.toMatch(/<script(?![^>]*src=)/i)
    expect(assets.some(file => /\/[\w-]+-[\w-]{6,}\.js$/.test(file))).toBe(true)
    expect(assets.some(file => /\/[\w-]+-[\w-]{6,}\.css$/.test(file))).toBe(true)
    expect(assets.filter(file => file.endsWith('.woff2'))).toHaveLength(4)
    expect(files.some(file => file.endsWith('.map'))).toBe(false)
    expect(files.some(file => file.includes('node_modules') || file.endsWith('.tsx'))).toBe(false)

    for (const match of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
      const value = match[1]!
      if (!value.startsWith('/cc/')) continue
      const resolved = path.resolve(root, value.slice('/cc/'.length))
      expect(resolved.startsWith(`${root}${path.sep}`)).toBe(true)
      await expect(stat(resolved)).resolves.toBeDefined()
    }
    for (const name of ['Anybody-OFL.txt', 'DM-Sans-OFL.txt', 'Space-Mono-OFL.txt', 'Magic-UI-MIT.txt', 'Aceternity-Attribution.txt']) {
      expect(files.some(file => file.endsWith(path.join('licenses', name)))).toBe(true)
    }
    const distributable = assets.filter(file => /\.(js|css|woff2)$/.test(file))
    let rawBytes = 0, gzipBytes = 0
    for (const file of distributable) {
      const content = await readFile(file)
      const source = content.toString('utf8')
      rawBytes += content.byteLength
      gzipBytes += gzipSync(content).byteLength
      expect(source).not.toMatch(/data:font/i)
      expect(source).not.toMatch(/(?:fetch|EventSource)\s*\(\s*['"]https?:\/\//i)
      expect(source).not.toMatch(/new EventSource\(\s*['"]https?:\/\//i)
      if (file.endsWith('.css')) for (const match of source.matchAll(/url\((['"]?)(.*?)\1\)/g)) {
        const ref = match[2]!
        expect(ref).not.toMatch(/^(?:https?:|data:|\/\/)/i)
        const resolved = ref.startsWith('/cc/') ? path.resolve(root, ref.slice('/cc/'.length)) : path.resolve(path.dirname(file), ref)
        expect(resolved.startsWith(`${root}${path.sep}`)).toBe(true)
        await expect(stat(resolved)).resolves.toBeDefined()
      }
    }
    console.info(`Built assets: ${assets.length}; distributable bytes: ${rawBytes}; gzip bytes: ${gzipBytes}`)
    expect(gzipBytes).toBeGreaterThan(0)
  })
})
