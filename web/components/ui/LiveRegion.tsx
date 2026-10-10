// A polite live region for step changes and the match (decision 12: aria-live on each step and on the match).
// Keyed by the text, so the same words announced twice in a row are still read.
export function LiveRegion({ text, assertive = false }: { text: string | null | undefined; assertive?: boolean }) {
  return (
    <p className="sr-only" aria-live={assertive ? 'assertive' : 'polite'} aria-atomic="true">
      <span key={text ?? ''}>{text ?? ''}</span>
    </p>
  )
}
