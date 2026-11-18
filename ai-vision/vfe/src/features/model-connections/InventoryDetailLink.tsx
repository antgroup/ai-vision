import type { ReactNode } from "react"
import { useLocation, useNavigate } from "react-router-dom"

/** Keep list position in this navigation entry, scoped to the owning Space. */
export function InventoryDetailLink({
  href,
  spaceId,
  className,
  children,
  ariaLabel,
}: {
  href: string
  spaceId: string
  className?: string
  children: ReactNode
  ariaLabel?: string
}) {
  const navigate = useNavigate()
  const location = useLocation()
  return (
    <a
      href={href}
      className={className}
      aria-label={ariaLabel}
      onClick={(event) => {
        if (
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        )
          return
        event.preventDefault()
        const position = {
          modelConnectionList: {
            spaceId,
            scrollTop: event.currentTarget.closest("main")?.scrollTop ?? 0,
          },
        }
        navigate(`${location.pathname}${location.search}`, {
          replace: true,
          state: position,
        })
        navigate(href, { state: { modelConnectionReturn: { spaceId } } })
      }}
    >
      {children}
    </a>
  )
}
