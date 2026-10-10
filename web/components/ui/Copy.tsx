import { Fragment, type ReactNode } from 'react'
import { templateParts, type CopyId } from '@/lib/copy'

// Renders a copy template with React nodes in its slots, so "Call {number}" can hold a tel: link and the words
// around it still come from lib/copy.ts (and so through the copy lint). A slot with no node drops its optional
// phrase, exactly like t().
export function Copy({ id, slots }: { id: CopyId; slots: Record<string, ReactNode> }) {
  const present = new Set(Object.entries(slots).filter(([, v]) => v !== null && v !== undefined && v !== '').map(([k]) => k))
  return (
    <>
      {templateParts(id, present).map((p, i) => (
        <Fragment key={i}>{'text' in p ? p.text : slots[p.slot]}</Fragment>
      ))}
    </>
  )
}
