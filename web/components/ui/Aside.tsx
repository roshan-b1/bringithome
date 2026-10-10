'use client'

import { createContext, useContext, type ReactNode } from 'react'

// The laptop layout's right pane (Screen, at 900 px and up). AppShell provides what a screen shows there when it
// has nothing of its own (the clue panel); the results and plan screens pass their map and live view instead.
// Below 900 px the pane is not drawn at all.
const DefaultAside = createContext<ReactNode>(null)

export const DefaultAsideProvider = DefaultAside.Provider
export const useDefaultAside = (): ReactNode => useContext(DefaultAside)
