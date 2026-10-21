import { CAST_TYPES, DATE_FORMATS, TIMESTAMP_FORMATS, type SilverStep } from '../lib/types'

type Kind = SilverStep['kind']

const KINDS: { kind: Kind; label: string }[] = [
  { kind: 'trim', label: 'Remove spaces around it' },
  { kind: 'upper', label: 'Make it UPPER CASE' },
  { kind: 'lower', label: 'Make it lower case' },
  { kind: 'null_if', label: 'Treat certain values as empty…' },
  { kind: 'cast', label: 'Convert to a type…' },
  { kind: 'parse_date', label: 'Read it as a date…' },
  { kind: 'parse_timestamp', label: 'Read it as a date and time…' },
  { kind: 'default', label: 'Use a value when it is empty…' },
]

function fresh(kind: Kind): SilverStep {
  switch (kind) {
    case 'null_if':
      return { kind, values: [''] }
    case 'cast':
      return { kind, type: 'STRING' }
    case 'parse_date':
      return { kind, format: DATE_FORMATS[0] }
    case 'parse_timestamp':
      return { kind, format: TIMESTAMP_FORMATS[0] }
    case 'default':
      return { kind, value: '' }
    default:
      return { kind } as SilverStep
  }
}

const field = 'rounded-md border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-accent'

/** The steps applied to one attribute, in order. Only the closed set of steps the server accepts can be added. */
export function StepEditor({ steps, onChange }: { steps: SilverStep[]; onChange: (steps: SilverStep[]) => void }) {
  const update = (i: number, step: SilverStep) => onChange(steps.map((s, j) => (j === i ? step : s)))
  const remove = (i: number) => onChange(steps.filter((_, j) => j !== i))

  return (
    <div className="flex flex-col gap-1.5">
      {steps.map((step, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2 rounded-md bg-border/30 px-2 py-1 text-xs">
          <span className="text-text-muted">{i + 1}.</span>
          <span className="text-text">{KINDS.find((k) => k.kind === step.kind)?.label.replace('…', '')}</span>

          {step.kind === 'null_if' && (
            <>
              <input
                className={`${field} w-40`}
                aria-label="Values to treat as empty"
                placeholder="e.g. 0, N/A"
                value={step.values.filter((v) => v !== '').join(', ')}
                onChange={(e) =>
                  update(i, {
                    ...step,
                    values: [
                      ...(step.values.includes('') ? [''] : []),
                      ...e.target.value.split(',').map((v) => v.trim()).filter((v) => v !== ''),
                    ],
                  })
                }
              />
              <label className="flex items-center gap-1 text-text-muted">
                <input
                  type="checkbox"
                  checked={step.values.includes('')}
                  onChange={(e) =>
                    update(i, {
                      ...step,
                      values: e.target.checked ? ['', ...step.values.filter((v) => v !== '')] : step.values.filter((v) => v !== ''),
                    })
                  }
                />
                blank
              </label>
            </>
          )}
          {step.kind === 'cast' && (
            <select className={field} aria-label="Type" value={step.type} onChange={(e) => update(i, { ...step, type: e.target.value })}>
              {CAST_TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          )}
          {step.kind === 'parse_date' && (
            <select className={field} aria-label="Date layout" value={step.format} onChange={(e) => update(i, { ...step, format: e.target.value })}>
              {DATE_FORMATS.map((f) => (
                <option key={f}>{f}</option>
              ))}
            </select>
          )}
          {step.kind === 'parse_timestamp' && (
            <select className={field} aria-label="Date and time layout" value={step.format} onChange={(e) => update(i, { ...step, format: e.target.value })}>
              {TIMESTAMP_FORMATS.map((f) => (
                <option key={f}>{f}</option>
              ))}
            </select>
          )}
          {step.kind === 'default' && (
            <input
              className={`${field} w-32`}
              aria-label="Value to use"
              value={step.value}
              onChange={(e) => update(i, { ...step, value: e.target.value })}
            />
          )}

          <button type="button" onClick={() => remove(i)} className="ml-auto text-text-muted hover:text-danger" aria-label={`Remove step ${i + 1}`}>
            ✕
          </button>
        </div>
      ))}
      {steps.length < 8 && (
        <select
          className={`${field} self-start text-text-muted`}
          aria-label="Add a step"
          value=""
          onChange={(e) => e.target.value && onChange([...steps, fresh(e.target.value as Kind)])}
        >
          <option value="">+ Add a step…</option>
          {KINDS.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
