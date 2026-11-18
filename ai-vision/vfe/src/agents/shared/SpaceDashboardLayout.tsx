import {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { useTranslation } from "react-i18next"
import { DashboardSectionPresentation } from "./dashboardSectionPresentation"

interface SpaceDashboardChapter {
  id: string
  title: string
  content: ReactNode
}
interface ChapterHandle {
  openAndFocus: () => void
}
const Chapter = forwardRef<
  ChapterHandle,
  SpaceDashboardChapter & { anchorId: string; scopeId: string }
>(function Chapter({ id, title, content, anchorId, scopeId }, ref) {
  const { t } = useTranslation()
  const heading = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(
    () =>
      id === "overview" ||
      (typeof window !== "undefined" && window.location.hash === `#${anchorId}`)
  )
  const [visited, setVisited] = useState(open)
  const [failed, setFailed] = useState(false)
  const presentation = useMemo(() => ({ reportError: setFailed }), [])
  const openAndFocus = useCallback(() => {
    setOpen(true)
    setVisited(true)
    heading.current?.focus({ preventScroll: true })
    heading.current?.scrollIntoView?.({ block: "start" })
  }, [])
  useImperativeHandle(ref, () => ({ openAndFocus }), [openAndFocus])
  useEffect(() => {
    const restore = () => {
      const hash = window.location.hash
      const exact = hash === `#${anchorId}`
      // A shared URL may contain an earlier React useId. Recover only within
      // the same data scope and only when no current instance owns that ID.
      const previousInstance =
        hash.startsWith(`#${scopeId}-`) &&
        hash.endsWith(`-${id}`) &&
        !document.getElementById(hash.slice(1)) &&
        heading.current?.closest("[data-dashboard-scope]") ===
          document.querySelector(`[data-dashboard-scope="${scopeId}"]`)
      if (exact || previousInstance) openAndFocus()
    }
    restore()
    window.addEventListener("hashchange", restore)
    return () => window.removeEventListener("hashchange", restore)
  }, [anchorId, id, scopeId, openAndFocus])
  return (
    <section className="min-w-0 px-4 py-2" aria-labelledby={anchorId}>
      <h2>
        <button
          ref={heading}
          id={anchorId}
          type="button"
          aria-expanded={open}
          aria-controls={`${anchorId}-content`}
          className="flex w-full scroll-mt-24 flex-wrap items-center gap-2 rounded text-left text-base font-medium focus-visible:outline-2 focus-visible:outline-offset-4"
          onClick={() => {
            setVisited(true)
            setOpen((value) => !value)
          }}
        >
          {open ? (
            <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
          )}
          {title}
          {failed ? (
            <span className="text-xs font-normal text-destructive">
              {t("metrics.loadFailed")}
            </span>
          ) : null}
        </button>
      </h2>
      <div id={`${anchorId}-content`} hidden={!open} className="pt-3">
        {visited ? (
          <DashboardSectionPresentation.Provider value={presentation}>
            {content}
          </DashboardSectionPresentation.Provider>
        ) : null}
      </div>
    </section>
  )
})

/** Each optional section is first mounted on demand, then retained with its local controls. */
export function SpaceDashboardLayout({
  sections,
  anchorScope,
}: {
  sections: SpaceDashboardChapter[]
  anchorScope: string
}) {
  const { t } = useTranslation()
  const scopeHash = Array.from(anchorScope)
    .reduce(
      (hash, char) => Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0,
      2166136261
    )
    .toString(36)
  const scopeId = `dashboard-${scopeHash}`
  const prefix = `${scopeId}-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`
  const chapters = useRef(new Map<string, ChapterHandle>())
  return (
    <div className="flex min-w-0 flex-col gap-2" data-dashboard-scope={scopeId}>
      <nav
        aria-label={t("dashboard.sectionNavigation")}
        className="flex flex-wrap gap-1 border-b px-4 pb-2"
      >
        {sections.map((section) => (
          <a
            key={section.id}
            href={`#${prefix}-${section.id}`}
            className="rounded px-2 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2"
            onClick={() => chapters.current.get(section.id)?.openAndFocus()}
          >
            {section.title}
          </a>
        ))}
      </nav>
      {sections.map((section) => (
        <Chapter
          {...section}
          scopeId={scopeId}
          key={section.id}
          anchorId={`${prefix}-${section.id}`}
          ref={(instance) => {
            if (instance) chapters.current.set(section.id, instance)
            else chapters.current.delete(section.id)
          }}
        />
      ))}
    </div>
  )
}
