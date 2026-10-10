import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react'
import { Icon, type IconName } from './Icon'
import styles from './BigButton.module.css'

// The one big action. Primary is the orange of the logo's route line with navy text (4.7:1, AA at any size);
// secondary is navy on white; quiet is for the calm alternatives ("Not sure", "Skip"); danger is only for 911.
// Every variant is at least 56 px tall and full width by default, for one thumb on a moving bus. With `href` it
// renders a link (tel:, sms:, mailto: or a page), otherwise a button.

export type BigButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger'

interface Common {
  variant?: BigButtonVariant
  size?: 'md' | 'lg' // md 56 px, lg 64 px
  icon?: IconName
  iconEnd?: IconName
  sublabel?: ReactNode
  loading?: boolean
  fullWidth?: boolean
  children: ReactNode
  className?: string
}

export type BigButtonProps =
  | (Common & { href: string } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, keyof Common | 'href'>)
  | (Common & { href?: undefined } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, keyof Common>)

export function BigButton(props: BigButtonProps) {
  const { variant = 'primary', size = 'lg', icon, iconEnd, sublabel, loading = false, fullWidth = true, children, className, ...rest } = props
  const cls = [styles.button, styles[variant], styles[size], fullWidth ? styles.full : '', loading ? styles.loading : '', className ?? '']
    .filter(Boolean)
    .join(' ')
  const body = (
    <>
      {icon ? <Icon name={icon} className={styles.icon} /> : null}
      <span className={styles.text}>
        <span className={styles.label}>{children}</span>
        {sublabel ? <span className={styles.sublabel}>{sublabel}</span> : null}
      </span>
      {loading ? <span className={styles.spinner} aria-hidden="true" /> : iconEnd ? <Icon name={iconEnd} className={styles.icon} /> : null}
    </>
  )
  if ('href' in rest && typeof rest.href === 'string') {
    return (
      <a {...(rest as AnchorHTMLAttributes<HTMLAnchorElement>)} className={cls} aria-busy={loading || undefined}>
        {body}
      </a>
    )
  }
  const { type = 'button', disabled, ...buttonRest } = rest as ButtonHTMLAttributes<HTMLButtonElement>
  return (
    <button {...buttonRest} type={type} className={cls} disabled={disabled || loading} aria-busy={loading || undefined}>
      {body}
    </button>
  )
}
