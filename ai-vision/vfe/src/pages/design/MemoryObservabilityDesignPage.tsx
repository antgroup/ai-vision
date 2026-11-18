import { Navigate, useLocation } from "react-router-dom"

/** Preserve old bookmarks and server deep links on the production page. */
export function MemoryObservabilityDesignPage() {
  const location = useLocation()
  return (
    <Navigate to={`/labs/memory${location.search}${location.hash}`} replace />
  )
}
