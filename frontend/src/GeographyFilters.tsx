import { FilterSelect } from './FilterSelect'
import { PlaceSearch } from './PlaceSearch'
import { CORRIDORS, cities, regions, changeCorridor, catalogSource, type Geography } from './geography'
import type { Direction } from './dashboard-data'

const cityOptions = (region: string) => [{ value: 'all', label: 'Весь регион' }, ...cities.filter((city) => city.regionId === region).map((city) => ({ value: city.id, label: city.area ? `${city.label} · ${city.area}` : city.label }))]

export function GeographyFilters({ value, direction, onChange, onDirection }: {
  value: Geography; direction: Direction; onChange: (value: Geography) => void; onDirection: (value: Direction) => void
}) {
  const studyLabel = CORRIDORS.find(({ id }) => id === value.corridor)!.label
  const study = <div className="endpoint-fields"><div className="field"><span>Регион</span><div className="static-field">{studyLabel}</div></div><PlaceSearch label="Город в исследуемом регионе" value={value.studyCity} options={cityOptions(value.corridor)} allowManual onChange={(studyCity) => onChange({ ...value, studyCity })} /></div>
  const other = <div className="endpoint-fields"><PlaceSearch label="Другой регион России" value={value.otherRegion} options={[{ value: 'all', label: 'Вся остальная Россия' }, ...regions.filter(({ id }) => id !== value.corridor).map(({ id, label }) => ({ value: id, label }))]} onChange={(otherRegion) => onChange({ ...value, otherRegion, otherCity: 'all' })} /><PlaceSearch label="Город в другом регионе" value={value.otherCity} options={value.otherRegion === 'all' ? [{ value: 'all', label: 'Сначала выберите регион' }] : cityOptions(value.otherRegion)} disabled={value.otherRegion === 'all'} allowManual onChange={(otherCity) => onChange({ ...value, otherCity })} /></div>
  return <div className="geography-controls">
    <div className="corridor-fields"><FilterSelect label="Исследуемый регион" value={value.corridor} options={CORRIDORS.map(({ id, label }) => ({ value: id, label }))} onChange={(corridor) => onChange(changeCorridor(value, corridor))} /><FilterSelect label="Направление перевозки" value={direction} options={[{ value:'inbound', label:'Ввоз в исследуемый регион' }, { value:'outbound', label:'Вывоз из исследуемого региона' }]} onChange={(next) => onDirection(next as Direction)} /></div>
    <div className="endpoint-grid"><div className="endpoint-card"><h3>Откуда</h3>{direction === 'inbound' ? other : study}</div><div className="endpoint-card"><h3>Куда</h3>{direction === 'inbound' ? study : other}</div></div>
    <p className="catalog-credit">Справочник: <a href="https://github.com/hflabs/city" target="_blank" rel="noreferrer">HFLabs / DaData</a> · <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noreferrer">CC BY-SA 4.0</a> · снимок {catalogSource.version_date}. Отсутствующий город можно указать вручную.</p>
  </div>
}
