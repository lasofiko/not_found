/** Resolve legacy UI links to an internal path, never a network-path URL. */
export function legacyRedirect(url: string): string | null {
  const original = url.split('?')[0]
  const legacy = /^\/dashboard(?=\/|%|$)/.test(original)
  let pathname: string
  try {
    pathname = decodeURIComponent(original)
  } catch {
    return legacy ? '/' : null
  }
  const dashboard = /^\/dashboard(?=\/|$)/.test(pathname)
  const suffix = dashboard ? pathname.slice('/dashboard'.length) || '/' : pathname
  if (!dashboard && !['/capacity', '/capacity/', '/capacity.html', '/frontend/capacity.html'].includes(suffix)) return null
  const controlCharacters = [...suffix].some(character => character.charCodeAt(0) <= 32 || character.charCodeAt(0) === 127)
  if (suffix.includes('\\') || suffix.includes('//') || /%2f|%5c/i.test(suffix) || controlCharacters) return '/'
  if (['/capacity', '/capacity/', '/capacity.html', '/frontend/capacity.html'].includes(suffix)) return '/#capacity'
  if (suffix === '/index.html') return '/'
  return suffix.split('/').map(segment => encodeURIComponent(segment)).join('/')
}
