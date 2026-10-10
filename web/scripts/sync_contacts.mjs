#!/usr/bin/env node
// Copies the verified contact list (api/backtrack_api/data/contacts.json, A's file, values from PREP-REPORT
// section 6) into the web bundle, so the safety footer and the report card render with no server (decision 12:
// "contacts.json and copy.ts ship in the bundle"). Runs before every build; the copy is checked in so dev servers
// and fresh clones work without it. It reads the API's file and never writes there.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const src = join(web, '..', 'api', 'backtrack_api', 'data', 'contacts.json')
const dst = join(web, 'lib', 'contacts.json')
let text
try {
  text = readFileSync(src, 'utf8')
} catch {
  console.log(`sync_contacts: ${src} not found, keeping the checked-in copy`)
  process.exit(0)
}
JSON.parse(text) // refuse to copy a broken file
let old = ''
try {
  old = readFileSync(dst, 'utf8')
} catch {}
if (old === text) console.log('sync_contacts: lib/contacts.json is current')
else {
  writeFileSync(dst, text)
  console.log('sync_contacts: lib/contacts.json updated from the API copy')
}
