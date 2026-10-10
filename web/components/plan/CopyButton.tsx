'use client'

import { useEffect, useState } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { t } from '@/lib/copy'

// Copy to the clipboard, then say "Copied" for a moment (spoken too: the label is inside a polite live region).
// navigator.clipboard needs a secure context and a user gesture, which a tap on https or localhost is; the hidden
// textarea fallback covers older in-app browsers. Nothing is kept: the text is built from the plan on each tap.

async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}

export function CopyButton({ text, label, variant = 'secondary' }: { text: string; label: string; variant?: 'primary' | 'secondary' | 'quiet' }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const id = window.setTimeout(() => setCopied(false), 2500)
    return () => window.clearTimeout(id)
  }, [copied])
  return (
    <BigButton
      variant={variant}
      size="md"
      icon={copied ? 'check' : 'copy'}
      onClick={async () => setCopied(await writeClipboard(text))}
    >
      <span aria-live="polite">{copied ? t('action.copied') : label}</span>
    </BigButton>
  )
}
