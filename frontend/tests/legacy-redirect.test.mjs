import assert from 'node:assert/strict'
import test from 'node:test'
import { legacyRedirect } from '../src/legacy-redirect.ts'

test('Vite сохраняет обычные старые ссылки и пропускает остальные маршруты', () => {
  for (const [url, target] of [
    ['/dashboard', '/'], ['/dashboard/', '/'], ['/dashboard/index.html', '/'],
    ['/dashboard/assets/app.js?old=1', '/assets/app.js'], ['/dashboard/forecast/2030', '/forecast/2030'],
    ['/capacity', '/#capacity'], ['/capacity/', '/#capacity'], ['/capacity.html', '/#capacity'],
    ['/frontend/capacity.html', '/#capacity'], ['/dashboard/capacity.html', '/#capacity'],
  ]) assert.equal(legacyRedirect(url), target)
  for (const url of ['/', '/api/v1/health', '/dashboard-other', '/assets/app.js']) assert.equal(legacyRedirect(url), null)
})

test('Vite не отдаёт внешние Location при двойных, закодированных и обратных слешах', () => {
  for (const path of [
    '/dashboard//example.com', '/dashboard/%2Fexample.com', '/dashboard/%2f%2fexample.com',
    '/dashboard/\\example.com', '/dashboard/%5Cexample.com', '/dashboard/\\/example.com',
    '/dashboard/%252Fexample.com', '/dashboard/%255cexample.com', '/dashboard/%zz',
    '/dashboard/%0d%0aLocation%3A%20https%3A%2F%2Fexample.com',
  ]) {
    const location = legacyRedirect(path)
    assert.equal(location, '/', path)
    assert.equal(new URL(location, 'https://flocast.example').origin, 'https://flocast.example')
  }
})
