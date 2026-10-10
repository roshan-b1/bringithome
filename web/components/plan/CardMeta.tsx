import { Chip } from '@/components/ui/Chip'
import { Icon } from '@/components/ui/Icon'
import type { Card } from '@/contracts/api'
import { clock } from '@/lib/time'
import { evidenceSlots } from './planModel'
import styles from './plan.module.css'
import { t } from '@/lib/copy'

// Under every card: what its facts rest on (CLAUDE.md: every plan card shows its evidence and "live" vs "schedule
// only"). A live card replayed under as_of or from fixtures reads Replay, never Live.

export function CardMeta({ card, replay }: { card: Card; replay: boolean }) {
  const source =
    card.source === 'static' ? (
      <span className={styles.staticSource} title={t('source.static_label')}>
        <Icon name="shield" size={16} strokeWidth={2.5} />
        <span aria-hidden="true">{t('source.static')}</span>
        <span className="sr-only">{t('source.static_label')}</span>
      </span>
    ) : (
      <Chip kind={card.source === 'schedule' ? 'schedule' : replay ? 'replay' : 'live'} />
    )
  return (
    <div className={styles.meta}>
      {source}
      {card.evidence.length ? (
        <ul role="list" className={styles.evidence} aria-label={t('plan.evidence_label')}>
          {card.evidence.map((e, i) => (
            <li key={i}>{t('plan.evidence', evidenceSlots(e, clock))}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
