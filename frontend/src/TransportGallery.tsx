import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, X } from 'lucide-react'

const transports = [
  { id: 'sea', label: 'Морской транспорт', caption: 'Порты, паромные линии и морские грузовые сообщения.', description: 'Морское пересечение связывает область с основной частью России. Вагон на пароме относится к морскому пересечению и не становится дополнительным сухопутным грузом.', image: 'sea.jpg', alt: 'Контейнерное судно в порту у грузовых кранов' },
  { id: 'rail_land', label: 'Железная дорога', caption: 'Грузовые поезда и сухопутный транзит.', description: 'Сухопутный железнодорожный транзит учитывается отдельно от перевозки вагонов на пароме. Сопоставимый ряд массы по нужной паре территорий в интерфейс пока не подключён.', image: 'rail.jpg', alt: 'Грузовой поезд с зелёными вагонами на железной дороге' },
  { id: 'air', label: 'Авиаперевозки', caption: 'Доставка грузов с особыми требованиями к срокам.', description: 'Авиация рассматривается как самостоятельный вид перевозок. Общий грузооборот аэропорта без проверки маршрутов и направлений нельзя считать массой исследуемого коридора.', image: 'air.jpg', alt: 'Самолёт над освещённой взлётно-посадочной полосой' },
  { id: 'road', label: 'Автомобильные перевозки', caption: 'Гибкие логистические цепочки и автотранспортные коридоры.', description: 'Автомобильные перевозки учитываются по направлению и массе груза. Количество пересечений грузовиков на границе нельзя переводить в тонны универсальным коэффициентом.', image: 'road.jpg', alt: 'Колонна грузовых автомобилей на мокрой трассе' },
]

export function TransportGallery() {
  const [selected, setSelected] = useState<(typeof transports)[number] | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    if (selected && !dialog.current?.open) dialog.current?.showModal()
    if (!selected && dialog.current?.open) dialog.current.close()
  }, [selected])

  return (
    <section className="section" id="transport" aria-labelledby="transport-title">
      <div className="transport-intro"><div><p className="eyebrow">Транспортная система</p><h2 id="transport-title">Четыре среды.<br /><em>Одна логистическая сеть.</em></h2></div></div>
      <div className="transport-grid">{transports.map((transport) => (
        <button className={`transport-card transport-${transport.id}`} key={transport.id} onClick={() => setSelected(transport)} aria-haspopup="dialog">
          <img src={`./images/${transport.image}`} alt={transport.alt} loading="lazy" />
          <span className="transport-shade" />
          <span className="transport-copy"><span className="transport-index">{transport.id === 'rail_land' ? 'ЖЕЛЕЗНАЯ ДОРОГА' : transport.id === 'sea' ? 'МОРЕ' : transport.id === 'air' ? 'АВИА' : 'АВТО'}</span><strong>{transport.label}</strong><span>{transport.caption}</span></span>
          <span className="go-arrow"><ArrowUpRight size={20} /></span>
        </button>
      ))}</div>
      <dialog ref={dialog} className="transport-dialog" aria-labelledby="transport-dialog-title" onCancel={() => setSelected(null)} onClose={() => setSelected(null)}>
        {selected && <><img src={`./images/${selected.image}`} alt={selected.alt} /><button className="icon-button dialog-close" onClick={() => setSelected(null)} aria-label="Закрыть описание транспорта"><X size={20} /></button><div className="transport-dialog-copy"><p className="eyebrow">Транспортная система</p><h2 id="transport-dialog-title">{selected.label}</h2><p>{selected.description}</p><span className="tag">Описание вида · не числовой ряд</span></div></>}
      </dialog>
    </section>
  )
}
