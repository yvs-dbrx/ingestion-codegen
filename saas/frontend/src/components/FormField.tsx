import type { InputHTMLAttributes } from 'react'

export function FormField({
  label,
  ...rest
}: { label: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium text-text">{label}</span>
      <input
        {...rest}
        className="rounded-md border border-border bg-surface px-3 py-2 text-text outline-none focus:border-accent"
      />
    </label>
  )
}

export function ErrorBanner({ message, problems }: { message: string; problems?: string[] }) {
  return (
    <div className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
      <p>{message}</p>
      {problems && problems.length > 0 && (
        <ul className="mt-1 list-inside list-disc">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
