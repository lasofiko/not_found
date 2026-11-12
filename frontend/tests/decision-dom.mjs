// TypeScript's in-process transpiler keeps these DOM tests usable on Windows
// environments that cannot spawn esbuild's service or Node test workers.
import ts from 'typescript'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
export { act, browser, createElement } from './dom-harness.mjs'

export async function decisionComponent() {
  const folder = new URL('../node_modules/.tmp/decision-dom/', import.meta.url)
  await mkdir(fileURLToPath(folder), { recursive: true })
  for (const name of ['api', 'comparison-api', 'DecisionWorkspace']) {
    const extension = name === 'DecisionWorkspace' ? 'tsx' : 'ts'
    const source = await readFile(new URL(`../src/${name}.${extension}`, import.meta.url), 'utf8')
    const code = ts.transpileModule(source, { fileName: `${name}.${extension}`, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } }).outputText
      .replace(/import ['"]\.\/DecisionWorkspace\.css['"];?/g, '')
      .replace(/from ['"]\.\/(api|comparison-api)['"]/g, "from './$1.mjs'")
    await writeFile(new URL(`${name}.mjs`, folder), code)
  }
  return import(pathToFileURL(fileURLToPath(new URL('DecisionWorkspace.mjs', folder))).href)
}
