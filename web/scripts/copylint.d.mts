// Types for the e2e test's import of the copy lint.
export interface Problem {
  kind: 'dash' | 'banned_word' | 'promise' | 'old_name'
  text: string
  at: number
}
export declare const BANNED_WORDS: Record<string, string[]>
export declare function lint(text: string): Problem[]
export declare function lintAll(table: Record<string, string>): Record<string, Problem[]>
