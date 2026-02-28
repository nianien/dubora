/** ms <-> display format conversion */

/** Format milliseconds to MM:SS.mmm */
export function msToDisplay(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSec / 60)
  const seconds = totalSec % 60
  const millis = ms % 1000
  return `${pad2(minutes)}:${pad2(seconds)}.${pad3(millis)}`
}

/** Format milliseconds to MM:SS (short form) */
export function msToShort(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSec / 60)
  const seconds = totalSec % 60
  return `${pad2(minutes)}:${pad2(seconds)}`
}

/** Format milliseconds to seconds with 1 decimal */
export function msToSec(ms: number): string {
  return (ms / 1000).toFixed(1)
}

function pad2(n: number): string {
  return n.toString().padStart(2, '0')
}

function pad3(n: number): string {
  return n.toString().padStart(3, '0')
}
