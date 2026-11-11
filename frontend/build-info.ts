import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

// Keep the input list and encoding in sync with backend/frontend.py.
const inputs = ['index.html', 'package.json', 'package-lock.json', 'vite.config.ts', 'build-info.ts',
  'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json', 'postcss.config.js', 'tailwind.config.js']
const digest = (value: string | Buffer) => createHash('sha256').update(value).digest('hex')

export function frontendSourceDigest(root: string): string {
  const files = inputs.filter(name => existsSync(path.join(root, name)))
  const collect = (directory: string) => {
    if (!existsSync(path.join(root, directory))) return
    for (const entry of readdirSync(path.join(root, directory), { withFileTypes: true })) {
      const name = directory + '/' + entry.name
      if (entry.isDirectory()) collect(name)
      else if (entry.isFile()) files.push(name)
    }
  }
  collect('src')
  collect('public')
  return digest(files.sort().map(name => name + '\0' + digest(readFileSync(path.join(root, name))) + '\n').join(''))
}

export function buildInfo(): Plugin {
  let root = ''
  let output = ''
  let source = ''
  return {
    name: 'flocast-build-info', apply: 'build',
    configResolved(config) { root = config.root; output = path.resolve(root, config.build.outDir) },
    buildStart() { source = frontendSourceDigest(root) },
    writeBundle() {
      if (source !== frontendSourceDigest(root)) throw new Error('Исходники изменились во время сборки. Повторите npm run build.')
      writeFileSync(path.join(output, 'build-info.json'), JSON.stringify({
        version: 1, source_sha256: source, index_sha256: digest(readFileSync(path.join(output, 'index.html'))),
      }))
    },
  }
}
