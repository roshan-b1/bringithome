// A small inline icon set, drawn like the logo: one rounded stroke, round joins, no fills. Inline (no icon font or
// library) so icons need no request and inherit currentColor. Always decorative: the text next to them names the
// action, so every icon is aria-hidden.

export type IconName =
  | 'back'
  | 'next'
  | 'check'
  | 'phone'
  | 'pin'
  | 'locate'
  | 'search'
  | 'bus'
  | 'train'
  | 'streetcar'
  | 'clock'
  | 'replay'
  | 'alert'
  | 'copy'
  | 'mail'
  | 'link'
  | 'calendar'
  | 'bag'
  | 'wallet'
  | 'key'
  | 'laptop'
  | 'question'
  | 'home'
  | 'shield'
  | 'walk'
  | 'mic'
  | 'close'
  | 'house'
  | 'message'

const PATHS: Record<IconName, string> = {
  back: 'M15 5l-7 7 7 7',
  next: 'M9 5l7 7-7 7',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  phone: 'M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM11 18h2',
  pin: 'M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  locate: 'M12 19a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM12 2v3M12 19v3M2 12h3M19 12h3M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  search: 'M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.5 15.5L20 20',
  bus: 'M6 4h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM4 11h16M8 18v2M16 18v2M8 14.5h.01M16 14.5h.01',
  train: 'M8 3h8a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3zM5 10h14M9 13.5h.01M15 13.5h.01M8 21l2-4M16 21l-2-4',
  streetcar: 'M7 6h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2zM5 12h14M12 6V3M9 3h6M9 21l1.5-3M15 21l-1.5-3',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  replay: 'M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4',
  alert: 'M12 3l9.5 17h-19L12 3zM12 10v4M12 17.5h.01',
  copy: 'M9 9h10v11H9zM5 15V4h10',
  mail: 'M4 6h16v12H4zM4 7l8 6 8-6',
  link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
  calendar: 'M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM4 10h16M8 3v4M16 3v4',
  bag: 'M6 8h12l1 12H5L6 8zM9 8V6a3 3 0 0 1 6 0v2',
  wallet: 'M4 7h15a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4zM4 7l11-3v3M16 13h.01',
  key: 'M8 15a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM11.5 11H21M18 11v3M15 11v2',
  laptop: 'M5 5h14v10H5zM3 19h18',
  question: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17h.01',
  home: 'M4 11l8-7 8 7v9H4z',
  shield: 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z',
  walk: 'M13 4.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM10 21l2-6 3 3v3M8 12l2-4 4 1 2 4M12 15l-1-4',
  mic: 'M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM6 11a6 6 0 0 0 12 0M12 17v4',
  close: 'M6 6l12 12M18 6L6 18',
  // the logo's house as a route line: the brand's own glyph at icon size
  house: 'M3.5 19.5h3V10l5.5-5 5.5 5v9.5h-4',
  message: 'M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-4 3.5V16H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z',
}

export function Icon({ name, size = 24, strokeWidth = 2.25, className }: { name: IconName; size?: number; strokeWidth?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
