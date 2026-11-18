import { useRef, useEffect, useState, type ReactNode } from "react"

interface LazyDashboardSectionProps {
  children: ReactNode
  minHeight?: number
  rootMargin?: string
}

export function LazyDashboardSection({
  children,
  minHeight = 480,
  rootMargin = "0px",
}: LazyDashboardSectionProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [isVisible, setIsVisible] = useState(
    () => typeof IntersectionObserver === "undefined",
  )

  useEffect(() => {
    const node = ref.current
    if (!node) return

    if (typeof IntersectionObserver === "undefined") return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true)
        }
      },
      { root: null, rootMargin, threshold: 0 },
    )

    observer.observe(node)
    return () => observer.disconnect()
  }, [rootMargin])

  return (
    <div ref={ref} style={isVisible ? undefined : { minHeight }}>
      {isVisible ? children : null}
    </div>
  )
}
