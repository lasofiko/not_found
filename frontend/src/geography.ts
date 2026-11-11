import catalog from './geography-catalog.json' with { type: 'json' }
import type { Direction } from './dashboard-data.ts'

export const CORRIDORS = [
  { id: '39', label: 'Калининградская область', capital: 'Калининград' },
  { id: '25', label: 'Приморский край', capital: 'Владивосток' },
  { id: '41', label: 'Камчатский край', capital: 'Петропавловск-Камчатский' },
]
export type Geography = { corridor: string; studyCity: string; otherRegion: string; otherCity: string }
export const DEFAULT_GEOGRAPHY: Geography = { corridor: '39', studyCity: 'all', otherRegion: 'all', otherCity: 'all' }
export const regions = catalog.regions
export const cities = catalog.cities
export const catalogSource = catalog.source
export const isDemoAvailable = (geo: Geography) => geo.corridor === '39' && geo.studyCity === 'all' && geo.otherRegion === 'all' && geo.otherCity === 'all'

export function changeCorridor(geo: Geography, corridor: string): Geography {
  return { ...geo, corridor, studyCity: 'all', ...(geo.otherRegion === corridor ? { otherRegion: 'all', otherCity: 'all' } : {}) }
}

function endpoint(regionId: string, cityId: string) {
  if (regionId === 'all') return { label: 'Вся остальная Россия', region: null, city: null, point: null }
  const region = regions.find(({ id }) => id === regionId)
  if (!region) throw new Error('Неизвестный регион')
  if (cityId.startsWith('manual:')) {
    return { label: `${cityId.slice(7)}, ${region.label}`, region: region.label, city: cityId.slice(7), point: null }
  }
  const city = cityId === 'all' ? null : cities.find(({ id, regionId: parent }) => id === cityId && parent === regionId)
  if (cityId !== 'all' && !city) throw new Error('Город не относится к выбранному региону')
  const capital = CORRIDORS.find(({ id }) => id === regionId)?.capital
  const point = city ?? cities.find((item) => item.regionId === regionId && item.label === capital) ?? null
  return { label: city ? `${city.label}, ${region.label}` : region.label, region: region.label, city: city?.label ?? null, point }
}

export function routeSelection(geo: Geography, direction: Direction) {
  if (!CORRIDORS.some(({ id }) => id === geo.corridor) || geo.corridor === geo.otherRegion) throw new Error('Выберите разные регионы коридора')
  if (geo.otherRegion === 'all' && geo.otherCity !== 'all') throw new Error('Сначала выберите другой регион')
  const study = endpoint(geo.corridor, geo.studyCity)
  const other = endpoint(geo.otherRegion, geo.otherCity)
  return direction === 'inbound' ? { from: other, to: study } : { from: study, to: other }
}

export function mapLinks(geo: Geography, direction: Direction) {
  const route = routeSelection(geo, direction)
  const points = [route.from.point, route.to.point].filter((p) => p !== null)
  const fallback = cities.find((c) => c.regionId === geo.corridor && c.label === CORRIDORS.find((r) => r.id === geo.corridor)?.capital)!
  const marker = points[0] ?? fallback
  const latitudes = points.length ? points.map((p) => p.lat) : [fallback.lat]
  const longitudes = points.length ? points.map((p) => p.lon) : [fallback.lon]
  const bbox = [Math.max(-180, Math.min(...longitudes) - 7), Math.max(-85, Math.min(...latitudes) - 4), Math.min(180, Math.max(...longitudes) + 7), Math.min(85, Math.max(...latitudes) + 4)]
  return {
    embed: `https://www.openstreetmap.org/export/embed.html?bbox=${bbox.join('%2C')}&layer=mapnik&marker=${marker.lat}%2C${marker.lon}`,
    full: `https://www.openstreetmap.org/#map=5/${marker.lat}/${marker.lon}`,
  }
}

export function selectionReport(geo: Geography, direction: Direction) {
  const route = routeSelection(geo, direction)
  const rows = [['FloCast', 'Выбранное направление'], ['Статус', 'unavailable'], ['Направление', direction], ['Откуда', route.from.label], ['Куда', route.to.label], ['Коридор', CORRIDORS.find(({ id }) => id === geo.corridor)!.label], ['Покрытие', 'Данные и модель для выбранной детализации не подключены; числовых результатов нет'], ['Географический справочник', catalog.source.url]]
  return '\uFEFF' + rows.map((row) => row.map((cell) => `"${cell.replaceAll('"', '""')}"`).join(';')).join('\r\n') + '\r\n'
}
