'use client'

import { useEffect, useState } from 'react'
import { prefersAppleMaps } from './reunionModel'

/** Apple Maps on iPhone, iPad and Mac Safari, Google Maps elsewhere; read after mount (the export prerenders with no navigator). */
export function useAppleMaps(): boolean {
  const [apple, setApple] = useState(false)
  useEffect(() => setApple(prefersAppleMaps(navigator.userAgent, navigator.maxTouchPoints ?? 0)), [])
  return apple
}
