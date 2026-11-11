import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

// The backend verifies these exact inputs before serving the production UI.
export function sourceHashes(root: string): Record<string, string> {
  const files: string[] = []
  const walk = (directory: string) => {
    for (const entry of readdirSync(path.join(root, directory), { withFileTypes: true })) {
      const name = path.posix.join(directory, entry.name)
      if (entry.isDirectory()) walk(name)
      else if (entry.isFile()) files.push(name)
    }
  }
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (entry.isFile() && ['.html', '.json', '.js', '.ts'].includes(path.extname(entry.name))) files.push(entry.name)
    else if (entry.isDirectory() && ['src', 'public'].includes(entry.name)) walk(entry.name)
  }
  return Object.fromEntries(files.sort().map(file => [file, createHash('sha256').update(readFileSync(path.join(root, file))).digest('hex')]))
}

export function frontendBuildInfo(): Plugin {
  let inputs: Record<string, string>
  return {
    name: 'flocast-build-info',
    apply: 'build',
    configResolved(config) { inputs = sourceHashes(config.root) },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'flocast-build.json', source: JSON.stringify({ version: 1, source_sha256: inputs }) })
    },
  }
}

export function retireDevelopmentWorker(): Plugin {
  return {
    name: 'flocast-development-worker',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const pathname = request.url?.split('?')[0]
        if (pathname !== '/sw.js' && pathname !== '/dashboard/sw.js') return next()
        // A previous production PWA must not mask Vite's current source code.
        response.setHeader('Content-Type', 'application/javascript')
        response.setHeader('Cache-Control', 'no-store')
        response.end("self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.registration.unregister().then(()=>self.clients.matchAll()).then(cs=>Promise.all(cs.map(c=>c.navigate('/'))))));")
      })
    },
  }
}
