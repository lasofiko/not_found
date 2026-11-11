import { useState } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { Command } from 'cmdk'
import { Check, ChevronDown, Search } from 'lucide-react'

export function PlaceSearch({ label, value, options, onChange, disabled = false, allowManual = false }: {
  label: string; value: string; options: { value: string; label: string }[]
  onChange: (value: string) => void; disabled?: boolean; allowManual?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const selected = options.find((option) => option.value === value)?.label ?? (value.startsWith('manual:') ? value.slice(7) : 'Выберите место')
  const choose = (next: string) => { onChange(next); setOpen(false); setQuery('') }
  return (
    <div className="field">
      <span>{label}</span>
      <Popover.Root open={open} onOpenChange={(next) => { setOpen(next); if (!next) setQuery('') }}>
        <Popover.Trigger className="filter-select" aria-label={label} disabled={disabled}><span>{selected}</span><ChevronDown size={16} /></Popover.Trigger>
        <Popover.Content className="place-menu" align="start" sideOffset={8} collisionPadding={16}>
          <Command label={label}>
            <div className="place-query"><Search size={16} /><Command.Input aria-label={`Поиск: ${label}`} placeholder="Начните вводить название…" value={query} onValueChange={setQuery} /></div>
            <Command.List>
              <Command.Empty>Ничего не найдено в справочнике.</Command.Empty>
              {options.map((option) => <Command.Item className="filter-option" key={option.value} value={option.label} onSelect={() => choose(option.value)}>{option.label}{option.value === value && <Check size={15} />}</Command.Item>)}
              {allowManual && query.trim().length > 1 && <Command.Item className="filter-option" value={`Указать вручную ${query}`} onSelect={() => choose(`manual:${query.trim().slice(0,120)}`)}>Указать «{query.trim()}» вручную</Command.Item>}
            </Command.List>
          </Command>
        </Popover.Content>
      </Popover.Root>
    </div>
  )
}
