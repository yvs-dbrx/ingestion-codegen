export function StubPage({ title }: { title: string }) {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold text-text">{title}</h1>
      <p className="mt-2 text-text-muted">This section is coming in a later module.</p>
    </div>
  )
}
