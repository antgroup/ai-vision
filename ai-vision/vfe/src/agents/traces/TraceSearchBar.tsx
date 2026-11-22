import {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useRef,
  useState,
} from "react"
import { Loader2, Search, X } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { Dialog, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import type { TraceSearchType, TraceSearchValue } from "@/agents/types/trace"
import {
  traceToolbarActiveClassName,
  traceToolbarControlClassName,
  traceToolbarInputClassName,
  traceToolbarRemoveClassName,
  traceToolbarSplitTriggerClassName,
} from "./traceToolbarStyles"

type ContentSearchType = Exclude<TraceSearchType, "traceId">

const TRACE_SEARCH_WAVE_PATHS = [
  "M0 31 C22 31 30 28 48 27 C66 26 72 19 90 20 C108 21 118 29 138 27 C158 25 164 7 184 8 C204 9 212 27 232 26 C252 25 260 18 278 22 C296 26 304 30 320 31 L320 32 L0 32 Z",
  "M0 31 C20 30 28 22 48 24 C68 26 76 29 94 27 C112 25 120 12 140 15 C160 18 168 28 188 26 C208 24 216 4 236 7 C256 10 264 27 282 25 C300 23 306 29 320 31 L320 32 L0 32 Z",
] as const

const TRACE_SEARCH_WAVE_VALUES = [
  TRACE_SEARCH_WAVE_PATHS[0],
  TRACE_SEARCH_WAVE_PATHS[1],
  TRACE_SEARCH_WAVE_PATHS[0],
].join(";")

const TRACE_SEARCH_INFINITY_PATH =
  "M4 12C4 4.5 11 4.5 16 12C21 19.5 28 19.5 28 12C28 4.5 21 4.5 16 12C11 19.5 4 19.5 4 12Z"

function TraceSearchInfinityLoader() {
  const gradientId = useId().replaceAll(":", "")

  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 32 24"
      fill="none"
      className="relative z-10 h-5 w-8 shrink-0"
    >
      <defs>
        <linearGradient
          id={gradientId}
          x1="3"
          y1="5"
          x2="29"
          y2="19"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="var(--trace-search-wave-cyan)" />
          <stop offset="0.52" stopColor="var(--trace-search-wave-pink)" />
          <stop offset="1" stopColor="var(--trace-search-wave-yellow)" />
        </linearGradient>
      </defs>
      <path
        d={TRACE_SEARCH_INFINITY_PATH}
        stroke="var(--trace-search-divider)"
        strokeWidth="2.25"
        strokeLinecap="round"
      />
      <path
        className="trace-search-infinity-progress motion-reduce:[animation:none]"
        d={TRACE_SEARCH_INFINITY_PATH}
        stroke={`url(#${gradientId})`}
        strokeWidth="2.25"
        strokeLinecap="round"
        pathLength="100"
        strokeDasharray="22 78"
      />
    </svg>
  )
}

interface TraceSearchBarProps {
  idKind?: "trace" | "session"
  idSearchClassName?: string
  traceIdValue: string
  onTraceIdChange: (value: string) => void
  onTraceIdSubmit: () => void
  onTraceIdClear: () => void
  activeContentSearch: TraceSearchValue | null
  onContentSubmit: (value: TraceSearchValue) => Promise<void>
  onContentCancel: () => void
  onContentClear: () => void
  contentSearchEnabled?: boolean
  fuzzySearchEnabled?: boolean
  fuzzySearchValue?: string
  fuzzySearchLoading?: boolean
  onFuzzySearchChange?: (value: string) => void
  onFuzzySearchSubmit?: () => void
  onFuzzySearchClear?: () => void
  disabled?: boolean
}

export interface TraceSearchBarHandle {
  openContentSearch: () => void
}

export const TraceSearchBar = forwardRef<
  TraceSearchBarHandle,
  TraceSearchBarProps
>(function TraceSearchBar(
  {
    idKind = "trace",
    idSearchClassName,
    traceIdValue,
    onTraceIdChange,
    onTraceIdSubmit,
    onTraceIdClear,
    activeContentSearch,
    onContentSubmit,
    onContentCancel,
    onContentClear,
    contentSearchEnabled = true,
    fuzzySearchEnabled = false,
    fuzzySearchValue = "",
    fuzzySearchLoading = false,
    onFuzzySearchChange = () => {},
    onFuzzySearchSubmit = () => {},
    onFuzzySearchClear,
    disabled = false,
  },
  ref
) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [contentType, setContentType] = useState<ContentSearchType>("input")
  const [contentKeyword, setContentKeyword] = useState("")
  const [isComposing, setIsComposing] = useState(false)
  const [isSearching, setIsSearching] = useState(false)
  const contentInputRef = useRef<HTMLInputElement>(null)
  const contentSearchRequestIdRef = useRef(0)
  const contentSearchDescriptionId = useId()

  const openContentSearch = useCallback(() => {
    if (!contentSearchEnabled || disabled) return
    if (
      activeContentSearch?.type === "input" ||
      activeContentSearch?.type === "output"
    ) {
      setContentType(activeContentSearch.type)
      setContentKeyword(activeContentSearch.keyword)
    } else {
      setContentKeyword("")
    }
    setOpen(true)
  }, [activeContentSearch, contentSearchEnabled, disabled])

  useImperativeHandle(
    ref,
    () => ({
      openContentSearch,
    }),
    [openContentSearch]
  )

  useEffect(() => {
    if (!contentSearchEnabled) return

    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault()
        openContentSearch()
      }
    }

    window.addEventListener("keydown", handleShortcut)
    return () => window.removeEventListener("keydown", handleShortcut)
  }, [contentSearchEnabled, openContentSearch])

  useEffect(() => {
    if (!open) return
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches
    const timer = window.setTimeout(
      () => contentInputRef.current?.focus(),
      reduceMotion ? 0 : 420
    )
    return () => window.clearTimeout(timer)
  }, [open])

  const submitContentSearch = async () => {
    const keyword = contentKeyword.trim()
    if (!keyword || isSearching) return

    setContentKeyword(keyword)
    setIsSearching(true)
    const requestId = ++contentSearchRequestIdRef.current

    try {
      await onContentSubmit({ type: contentType, keyword })
      if (contentSearchRequestIdRef.current !== requestId) return
      setIsSearching(false)
      setOpen(false)
    } catch (error) {
      if (contentSearchRequestIdRef.current !== requestId) return
      setIsSearching(false)
      if (error instanceof DOMException && error.name === "AbortError") {
        window.setTimeout(() => contentInputRef.current?.focus(), 0)
        return
      }
      setOpen(false)
    }
  }

  const cancelContentSearch = () => {
    contentSearchRequestIdRef.current += 1
    onContentCancel()
    setIsSearching(false)
    window.setTimeout(() => contentInputRef.current?.focus(), 0)
  }

  const activeContentType =
    activeContentSearch?.type === "input" ||
    activeContentSearch?.type === "output"
      ? activeContentSearch.type
      : null
  const activeContentSummary =
    activeContentType && activeContentSearch?.keyword
      ? `${t(`traceSearch.types.${activeContentType}`)} ${t("traceSearch.contains")} “${activeContentSearch.keyword}”`
      : null

  return (
    <>
      {contentSearchEnabled ? (
        <div
          className={cn(
            "order-first inline-flex max-w-full",
            activeContentSummary && [
              traceToolbarActiveClassName,
              "max-w-[min(20rem,100%)]",
            ]
          )}
        >
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={cn(
              traceToolbarControlClassName,
              "min-w-0 gap-2",
              activeContentSummary && [
                traceToolbarSplitTriggerClassName,
                "shrink",
              ]
            )}
            aria-label={t("traceSearch.openContentAria")}
            aria-describedby={
              activeContentSummary ? contentSearchDescriptionId : undefined
            }
            title={activeContentSummary ?? undefined}
            onClick={openContentSearch}
            disabled={disabled}
          >
            {activeContentSummary ? (
              <>
                <Search className="size-3.5 text-muted-foreground" />
                <span className="min-w-0 truncate">{activeContentSummary}</span>
              </>
            ) : (
              <>
                <span>{t("traceSearch.fullText")}</span>
                <span className="text-xs text-muted-foreground">⌘K</span>
              </>
            )}
          </Button>
          {activeContentSummary && (
            <>
              <button
                type="button"
                className={traceToolbarRemoveClassName}
                aria-label={t("traceSearch.clearContentAria")}
                onClick={onContentClear}
              >
                <X className="size-3" />
              </button>
              <span id={contentSearchDescriptionId} className="sr-only">
                {activeContentSummary}
              </span>
            </>
          )}
        </div>
      ) : null}

      {fuzzySearchEnabled ? (
        <div
          className="relative w-[280px] min-w-[220px] shrink-0"
          aria-busy={fuzzySearchLoading}
        >
          <button
            type="button"
            className="absolute top-1/2 left-2.5 z-10 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            aria-label={t("traceSearch.fuzzySearchAria")}
            onClick={onFuzzySearchSubmit}
            disabled={disabled || fuzzySearchLoading}
          >
            {fuzzySearchLoading ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Search className="size-3.5" />
            )}
          </button>
          <Input
            className={cn(traceToolbarInputClassName, "pr-8 pl-8")}
            aria-label={t("traceSearch.fuzzySearchAria")}
            placeholder={t("traceSearch.placeholders.fuzzy")}
            value={fuzzySearchValue}
            disabled={disabled}
            onChange={(event) => onFuzzySearchChange(event.target.value)}
            onCompositionStart={() => setIsComposing(true)}
            onCompositionEnd={() => setIsComposing(false)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !isComposing &&
                !event.nativeEvent.isComposing &&
                !fuzzySearchLoading
              ) {
                onFuzzySearchSubmit()
              }
            }}
          />
          {fuzzySearchValue && (
            <button
              type="button"
              className="absolute top-1/2 right-2 z-10 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              aria-label={t("traceSearch.clearFuzzyAria")}
              onClick={onFuzzySearchClear}
              disabled={disabled}
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
      ) : null}

      <div
        className={cn(
          "relative w-[280px] min-w-[220px] shrink-0",
          idSearchClassName
        )}
      >
        <button
          type="button"
          className="absolute top-1/2 left-2.5 z-10 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={t(
            idKind === "session"
              ? "sessionFilters.searchId"
              : "traceSearch.searchAria"
          )}
          onClick={onTraceIdSubmit}
          disabled={disabled}
        >
          <Search className="size-3.5" />
        </button>
        <Input
          className={cn(traceToolbarInputClassName, "pr-8 pl-8")}
          aria-label={t(
            idKind === "session"
              ? "traceLabels.sessionId"
              : "traceSearch.types.traceId"
          )}
          placeholder={t(
            idKind === "session"
              ? "sessionFilters.partialId"
              : "traceSearch.placeholders.traceId"
          )}
          value={traceIdValue}
          disabled={disabled}
          onChange={(event) => onTraceIdChange(event.target.value)}
          onCompositionStart={() => setIsComposing(true)}
          onCompositionEnd={() => setIsComposing(false)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !isComposing &&
              !event.nativeEvent.isComposing
            ) {
              onTraceIdSubmit()
            }
          }}
        />
        {traceIdValue && (
          <button
            type="button"
            className="absolute top-1/2 right-2 z-10 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            aria-label={t("traceSearch.clearAria")}
            onClick={onTraceIdClear}
            disabled={disabled}
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>

      {contentSearchEnabled ? (
        <Dialog
          open={open}
          onOpenChange={(nextOpen) => {
            if (!nextOpen && isSearching) return
            setOpen(nextOpen)
          }}
        >
          <DialogContent
            size="detail"
            className="gap-0 bg-transparent p-0 shadow-none ring-0 duration-100"
            showCloseButton={false}
            onOpenAutoFocus={(event) => event.preventDefault()}
            onEscapeKeyDown={(event) => {
              if (isSearching) event.preventDefault()
            }}
            onPointerDownOutside={(event) => {
              if (isSearching) event.preventDefault()
            }}
          >
            <DialogTitle className="sr-only">
              {t("traceSearch.modalTitle")}
            </DialogTitle>
            <DialogDescription className="sr-only">
              {t(
                idKind === "session"
                  ? "sessionFilters.searchDescription"
                  : "traceSearch.modalDescription"
              )}
            </DialogDescription>

            <div className="trace-search-content-enter relative w-full">
              <div
                aria-hidden="true"
                className="trace-search-launch-glow pointer-events-none absolute inset-x-5 -inset-y-4 rounded-full blur-xl motion-reduce:hidden"
              />
              <div
                aria-hidden="true"
                className="trace-search-local-scrim pointer-events-none absolute -inset-x-8 -inset-y-5 rounded-[40px]"
              />
              <div
                aria-busy={isSearching}
                className="trace-search-control group relative z-10 flex h-14 items-center gap-2 overflow-hidden rounded-4xl border px-3 text-foreground transition-[border-color,box-shadow,background-color] sm:gap-3 sm:px-4"
              >
                <div
                  className="relative z-10 grid shrink-0 grid-cols-2 items-center"
                  role="radiogroup"
                  aria-label={t("traceSearch.contentScopeAria")}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      "trace-search-scope-indicator absolute bottom-0 h-0.5 w-4 -translate-x-1/2 rounded-full bg-primary",
                      contentType === "input" ? "left-1/4" : "left-3/4"
                    )}
                  />
                  {(["input", "output"] as const).map((type) => {
                    const selected = contentType === type
                    return (
                      <button
                        key={type}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        aria-label={t(
                          `traceSearch.search${type === "input" ? "Input" : "Output"}Aria`
                        )}
                        disabled={isSearching}
                        className={cn(
                          "relative h-8 px-1.5 text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-wait disabled:opacity-70",
                          selected
                            ? "text-foreground"
                            : "text-muted-foreground hover:text-foreground"
                        )}
                        onClick={() => {
                          setContentType(type)
                          window.requestAnimationFrame(() =>
                            contentInputRef.current?.focus()
                          )
                        }}
                      >
                        {t(`traceSearch.types.${type}`)}
                      </button>
                    )
                  })}
                </div>

                <div className="h-5 w-px shrink-0 bg-border" />
                {isSearching ? (
                  <TraceSearchInfinityLoader />
                ) : (
                  <Search
                    className="relative z-10 size-4 shrink-0 text-muted-foreground transition-colors group-focus-within:text-foreground"
                    strokeWidth={1.5}
                  />
                )}
                <input
                  ref={contentInputRef}
                  className="relative z-10 min-w-0 flex-1 bg-transparent text-sm font-normal text-foreground placeholder:text-[var(--trace-search-placeholder)] focus:outline-none disabled:cursor-wait disabled:opacity-70"
                  aria-label={t("traceSearch.contentKeywordAria")}
                  placeholder={t(`traceSearch.placeholders.${contentType}`)}
                  value={contentKeyword}
                  disabled={isSearching}
                  onChange={(event) => {
                    setContentKeyword(event.target.value)
                  }}
                  onCompositionStart={() => setIsComposing(true)}
                  onCompositionEnd={() => setIsComposing(false)}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      !isComposing &&
                      !event.nativeEvent.isComposing
                    ) {
                      submitContentSearch()
                    }
                  }}
                />

                {isSearching ? (
                  <div className="trace-search-status-enter relative z-10 flex shrink-0 items-center gap-1">
                    <span
                      role="status"
                      aria-live="polite"
                      className="max-w-32 truncate text-sm font-normal text-muted-foreground"
                    >
                      {t("traceSearch.searching", {
                        type: t(`traceSearch.types.${contentType}`),
                      })}
                    </span>
                    <button
                      type="button"
                      className="flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
                      aria-label={t("traceSearch.cancelContentAria")}
                      onClick={cancelContentSearch}
                    >
                      <X className="size-4" />
                    </button>
                  </div>
                ) : null}

                <div
                  aria-hidden="true"
                  className={cn(
                    "trace-search-wave pointer-events-none absolute bottom-0 h-6 w-2/3 motion-reduce:hidden",
                    isSearching && "trace-search-wave-searching"
                  )}
                >
                  <svg
                    className="size-full overflow-visible"
                    viewBox="0 0 320 32"
                    preserveAspectRatio="none"
                  >
                    <defs>
                      <linearGradient
                        id="trace-search-wave-gradient"
                        x1="0"
                        y1="0"
                        x2="1"
                        y2="0"
                      >
                        <stop
                          offset="0%"
                          stopColor="var(--trace-search-wave-cyan)"
                          stopOpacity="0"
                        />
                        <stop
                          offset="28%"
                          stopColor="var(--trace-search-wave-cyan)"
                          stopOpacity="0.62"
                        />
                        <stop
                          offset="52%"
                          stopColor="var(--trace-search-wave-pink)"
                          stopOpacity="0.72"
                        />
                        <stop
                          offset="74%"
                          stopColor="var(--trace-search-wave-pink)"
                          stopOpacity="0.42"
                        />
                        <stop
                          offset="100%"
                          stopColor="var(--trace-search-wave-pink)"
                          stopOpacity="0"
                        />
                      </linearGradient>
                      <filter
                        id="trace-search-wave-blur"
                        x="-20%"
                        y="-50%"
                        width="140%"
                        height="180%"
                      >
                        <feGaussianBlur stdDeviation="3" />
                      </filter>
                    </defs>
                    <path
                      d={TRACE_SEARCH_WAVE_PATHS[0]}
                      fill="url(#trace-search-wave-gradient)"
                      filter="url(#trace-search-wave-blur)"
                      opacity="0.55"
                    >
                      <animate
                        attributeName="d"
                        dur="7.6s"
                        repeatCount="indefinite"
                        calcMode="spline"
                        keyTimes="0;0.5;1"
                        keySplines="0.42 0 0.58 1;0.42 0 0.58 1"
                        values={TRACE_SEARCH_WAVE_VALUES}
                      />
                    </path>
                    <path
                      d={TRACE_SEARCH_WAVE_PATHS[0]}
                      fill="url(#trace-search-wave-gradient)"
                      opacity="0.7"
                    >
                      <animate
                        attributeName="d"
                        dur="7.6s"
                        repeatCount="indefinite"
                        calcMode="spline"
                        keyTimes="0;0.5;1"
                        keySplines="0.42 0 0.58 1;0.42 0 0.58 1"
                        values={TRACE_SEARCH_WAVE_VALUES}
                      />
                    </path>
                  </svg>
                </div>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  )
})
