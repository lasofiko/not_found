import assert from 'node:assert/strict'
import test from 'node:test'
import { cities, regions, DEFAULT_GEOGRAPHY, CORRIDORS, changeCorridor, isDemoAvailable, routeSelection, selectionReport, mapLinks } from '../src/geography.ts'

test('три коридора доступны в обоих направлениях без подмены географии', () => {
  assert.equal(CORRIDORS.length, 3)
  for (const corridor of CORRIDORS) {
    const geo = changeCorridor(DEFAULT_GEOGRAPHY, corridor.id)
    const inbound = routeSelection(geo, 'inbound')
    const outbound = routeSelection(geo, 'outbound')
    assert.equal(inbound.to.label, corridor.label)
    assert.equal(inbound.from.label, 'Вся остальная Россия')
    assert.deepEqual(inbound.from, outbound.to)
    assert.deepEqual(inbound.to, outbound.from)
    assert.equal(isDemoAvailable(geo), corridor.id === '39')
  }
})

test('смена региона сбрасывает несовместимый город и исключает связь с самим собой', () => {
  const geo = changeCorridor({ ...DEFAULT_GEOGRAPHY, studyCity: 'manual:Калининград', otherRegion: '25', otherCity: 'manual:Владивосток' }, '25')
  assert.equal(geo.studyCity, 'all')
  assert.equal(geo.otherRegion, 'all')
  assert.equal(geo.otherCity, 'all')
  assert.throws(() => routeSelection({ ...geo, otherRegion: '25' }, 'inbound'))
})

test('городская детализация не наследует число агрегированного демо', () => {
  const city = cities.find((c) => c.regionId === '39')
  const geo = { ...DEFAULT_GEOGRAPHY, studyCity: city.id }
  assert.equal(isDemoAvailable(geo), false)
  assert.equal(routeSelection(geo, 'inbound').to.city, city.label)
  const otherCity = cities.find((c) => c.regionId !== '39')
  assert.throws(() => routeSelection({ ...geo, studyCity: otherCity.id }, 'inbound'))
  assert.throws(() => routeSelection({ ...DEFAULT_GEOGRAPHY, otherCity: otherCity.id }, 'inbound'))
})

test('ручной город сохраняется как название без вымышленных координат и объёмов', () => {
  const geo = { ...DEFAULT_GEOGRAPHY, otherRegion: '54', otherCity: 'manual:Новый город' }
  const route = routeSelection(geo, 'outbound')
  assert.equal(route.to.city, 'Новый город')
  assert.equal(route.to.point, null)
  const csv = selectionReport(geo, 'outbound')
  assert.match(csv, /"Статус";"unavailable"/)
  assert.match(csv, /Новый город/)
  assert.ok(!csv.includes('14.5'))
  assert.ok(!csv.includes('Объём,'))
})

test('карта меняет географию коридора, каталог не теряет регионы и связи городов', () => {
  assert.equal(cities.length, 1117)
  assert.ok(cities.every((city) => regions.some((r) => r.id === city.regionId)))
  assert.ok(cities.every((city) => Number.isFinite(city.lat) && Number.isFinite(city.lon)))
  const maps = CORRIDORS.map(({ id }) => mapLinks(changeCorridor(DEFAULT_GEOGRAPHY, id), 'inbound').embed)
  assert.equal(new Set(maps).size, 3)
})
