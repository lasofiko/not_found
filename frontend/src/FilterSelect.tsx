import * as Select from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'

export function FilterSelect({ label, value, onChange, options, disabled = false }: {
  label: string
  disabled?: boolean
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div className="field">
      <span>{label}</span>
      <Select.Root disabled={disabled} value={value} onValueChange={onChange}>
        <Select.Trigger className="filter-select" aria-label={label}>
          <Select.Value /><Select.Icon><ChevronDown size={16} /></Select.Icon>
        </Select.Trigger>
        <Select.Content className="filter-menu" position="popper" align="start" sideOffset={8} collisionPadding={16}>
          <Select.Viewport>
            {options.map((option) => (
              <Select.Item className="filter-option" key={option.value} value={option.value}>
                <Select.ItemText>{option.label}</Select.ItemText>
                <Select.ItemIndicator><Check size={15} /></Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Viewport>
        </Select.Content>
      </Select.Root>
    </div>
  )
}
