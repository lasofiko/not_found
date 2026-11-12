import { build } from 'esbuild'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { JSDOM } from 'jsdom'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

export { act, createElement }

export async function component(name, plugins = []) {
  const output = fileURLToPath(new URL(`../node_modules/.tmp/dom-${name}-${plugins.length}.mjs`, import.meta.url))
  await mkdir(fileURLToPath(new URL('../node_modules/.tmp/', import.meta.url)), { recursive: true })
  await build({ entryPoints: [fileURLToPath(new URL(`../src/${name}.tsx`, import.meta.url))], outfile: output,
    bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic', loader: { '.css': 'empty' },
    define: { 'import.meta.env.BASE_URL': '"/"' }, plugins })
  return import(pathToFileURL(output).href)
}

export function browser() {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/', pretendToBeVisual: true })
  const saved = new Map()
  for (const [name, value] of Object.entries({ window: dom.window, document: dom.window.document,
    HTMLElement: dom.window.HTMLElement, Element: dom.window.Element, Node: dom.window.Node, FormData: dom.window.FormData,
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window), cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    getComputedStyle: dom.window.getComputedStyle, IS_REACT_ACT_ENVIRONMENT: true,
    fetch: () => { throw new Error('Внешняя сеть запрещена в тестах React') } })) {
    saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value })
  }
  const container = document.querySelector('#root')
  const root = createRoot(container)
  return {
    dom, container, root,
    render: async value => act(async () => { root.render(value) }),
    event: (element, name, options = {}) => element.dispatchEvent(new dom.window.Event(name, { bubbles: true, cancelable: true, ...options })),
    key: (element, key, shiftKey = false) => element.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true })),
    async close() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [name, descriptor] of saved) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else delete globalThis[name]
      }
    },
  }
}
