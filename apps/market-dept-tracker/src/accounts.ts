import { randomBytes, scryptSync, timingSafeEqual } from 'node:crypto'

/** The passwords of the post accounts. */

/** A random session token, the same shape the person links use. */
export function newSessionToken(): string {
  return randomBytes(16).toString('hex')
}

/** The hash stored in the database: scrypt with a random salt. */
export function hashPassword(password: string): string {
  const salt = randomBytes(16)
  const hash = scryptSync(password, salt, 32)
  return `scrypt$${salt.toString('hex')}$${hash.toString('hex')}`
}

/** Whether the password matches the stored hash, in constant time. */
export function verifyPassword(password: string, stored: string): boolean {
  const parts = stored.split('$')
  if (parts.length !== 3 || parts[0] !== 'scrypt') return false
  let salt: Buffer
  let expected: Buffer
  try {
    salt = Buffer.from(parts[1] ?? '', 'hex')
    expected = Buffer.from(parts[2] ?? '', 'hex')
  } catch {
    return false
  }
  if (salt.length === 0 || expected.length === 0) return false
  const actual = scryptSync(password, salt, expected.length)
  return actual.length === expected.length && timingSafeEqual(actual, expected)
}

/** A short password without characters that are easy to mix up. */
export function randomPassword(length = 10): string {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
  return Array.from(randomBytes(length), (byte) => alphabet[byte % alphabet.length]).join('')
}
