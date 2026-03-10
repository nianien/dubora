/** Shared negative-ID generator for temporary cues (before server assigns real IDs). */
let _next = -1
export function nextTempId(): number {
  return _next--
}
