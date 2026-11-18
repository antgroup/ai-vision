/** Match the canonical backend's two active native-query slots per process.
 * Cancellation stops a caller, not admitted server work: drain the complete
 * transport promise before handing its slot to the next request.
 */
function createNativeQueryScheduler() {
  const queue: (() => void)[] = []
  let active = 0
  function pump() {
    while (active < 2 && queue.length) queue.shift()!()
  }
  return function schedule<T>(
    operation: () => Promise<T>,
    signal?: AbortSignal | null
  ): Promise<T> {
    if (signal?.aborted) return Promise.reject(signal.reason)
    return new Promise<T>((resolve, reject) => {
      let started = false
      let canceled = false
      function cancel() {
        canceled = true
        if (!started) {
          const index = queue.indexOf(start)
          if (index >= 0) queue.splice(index, 1)
        }
        reject(signal?.reason)
      }
      function start() {
        started = true
        active++
        Promise.resolve()
          .then(() => {
            if (canceled) throw signal?.reason
            return operation()
          })
          .then(
            (value) => {
              if (!canceled) resolve(value)
            },
            (error) => {
              if (!canceled) reject(error)
            }
          )
          .finally(() => {
            signal?.removeEventListener("abort", cancel)
            active--
            pump()
          })
      }
      signal?.addEventListener("abort", cancel, { once: true })
      queue.push(start)
      pump()
    })
  }
}

export const scheduleNativeQuery = createNativeQueryScheduler()
