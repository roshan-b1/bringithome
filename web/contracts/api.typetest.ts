// Compile-time guard for the runtime check's design: each line below must FAIL to type-check (tsc reports an
// unused @ts-expect-error otherwise), proving obj<T>() rejects a checker that drifts from its interface.
import { int, lit, nullable, obj, opt, str } from './api.ts'

// a nullable field checked as never-null
// @ts-expect-error string is stricter than string | null
obj<{ a: string | null }>({ a: str })

// a required field checked as optional
// @ts-expect-error string | undefined is looser than string
obj<{ a: string }>({ a: opt(str) })

// a field the interface has but the checker forgets
// @ts-expect-error missing key b
obj<{ a: string; b: number }>({ a: str })

// a literal union that lost a member
// @ts-expect-error 'x' | 'y' is not 'x' | 'y' | 'z'
obj<{ a: 'x' | 'y' | 'z' }>({ a: lit('x', 'y') })

// and the right shapes compile
obj<{ a: string | null; b?: number | null; c: 'x' | 'y' }>({ a: nullable(str), b: opt(nullable(int)), c: lit('x', 'y') })
