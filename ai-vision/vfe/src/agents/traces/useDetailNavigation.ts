import { useCallback, useRef, useState } from "react"

export type DetailTimeRange = {
  from?: string
  to?: string
}

export type DetailNavigationTarget =
  | {
      kind: "trace"
      id: string
      timeRange?: DetailTimeRange | null
      navigationKey: number
    }
  | {
      kind: "session"
      id: string
      timeRange?: DetailTimeRange | null
      focusTraceId?: string | null
      navigationKey: number
    }

type DetailTargetInput = DetailNavigationTarget extends infer Target
  ? Target extends DetailNavigationTarget
    ? Omit<Target, "navigationKey">
    : never
  : never

type DetailNavigationState = {
  current: DetailNavigationTarget | null
  returnTo: DetailNavigationTarget | null
}

function isSameTarget(
  left: DetailNavigationTarget,
  right: DetailTargetInput
): boolean {
  return left.kind === right.kind && left.id === right.id
}

/**
 * Keeps detail navigation deliberately bounded to one return target.
 *
 * A session and trace can link to one another, but we must not model that as
 * an unbounded browser-like stack: Session A -> Trace T -> Session A should
 * pop back to Session A instead of growing A -> T -> A indefinitely.
 */
export function useDetailNavigation(
  initialTarget: DetailTargetInput | null = null
) {
  const nextNavigationKey = useRef(initialTarget ? 2 : 1)
  const createTarget = useCallback(
    (target: DetailTargetInput): DetailNavigationTarget => ({
      ...target,
      navigationKey: nextNavigationKey.current++,
    }),
    []
  )
  const [state, setState] = useState<DetailNavigationState>(() => ({
    current: initialTarget ? { ...initialTarget, navigationKey: 1 } : null,
    returnTo: null,
  }))

  const openFromList = useCallback(
    (target: DetailTargetInput) => {
      setState({ current: createTarget(target), returnTo: null })
    },
    [createTarget]
  )

  const openRelated = useCallback(
    (target: DetailTargetInput) => {
      setState((previous) => {
        if (previous.current && isSameTarget(previous.current, target)) {
          return previous
        }
        if (previous.returnTo && isSameTarget(previous.returnTo, target)) {
          return { current: createTarget(target), returnTo: null }
        }
        return {
          current: createTarget(target),
          returnTo: previous.current,
        }
      })
    },
    [createTarget]
  )

  const closeCurrent = useCallback((navigationKey?: number) => {
    setState((previous) => {
      if (
        navigationKey !== undefined &&
        previous.current?.navigationKey !== navigationKey
      ) {
        return previous
      }
      return { current: previous.returnTo, returnTo: null }
    })
  }, [])

  const clear = useCallback(() => {
    setState({ current: null, returnTo: null })
  }, [])

  return {
    current: state.current,
    returnTo: state.returnTo,
    openFromList,
    openRelated,
    closeCurrent,
    clear,
  }
}
