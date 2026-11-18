import { useState, useEffect, useCallback, useMemo, useRef } from "react"
import { X, Info, AlertTriangle, AlertCircle } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { api } from "@/lib/api"
import { useAgent } from "@/contexts/AgentContext"

interface NotifyItem {
  enabled?: boolean
  content: string
  level?: "info" | "warning" | "error"
  expires_at?: string | null
}

interface NotifyConfig {
  enabled: boolean
  global?: NotifyItem | null
  agents?: Record<string, NotifyItem> | null
}

function hashContent(content: string): string {
  let hash = 0
  for (let i = 0; i < content.length; i++) {
    const char = content.charCodeAt(i)
    hash = ((hash << 5) - hash + char) | 0
  }
  return hash.toString(36)
}

function isExpired(expiresAt: string | null | undefined): boolean {
  if (!expiresAt) return false
  const expiry = new Date(expiresAt)
  return expiry.getTime() <= Date.now()
}

const DISMISS_PREFIX = "platform_notify_dismissed:"

const levelIcons = {
  info: Info,
  warning: AlertTriangle,
  error: AlertCircle,
}

const levelStyles = {
  info: "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-500/40 dark:bg-blue-950/30 dark:text-blue-200",
  warning: "border-amber-400 bg-amber-50 text-amber-900 dark:border-amber-500/50 dark:bg-amber-950/30 dark:text-amber-200",
  error: "border-red-300 bg-red-50 text-red-900 dark:border-red-500/50 dark:bg-red-950/30 dark:text-red-200",
}

function MarqueeText({ content }: { content: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const textRef = useRef<HTMLDivElement>(null)
  const [needsScroll, setNeedsScroll] = useState(false)

  useEffect(() => {
    const container = containerRef.current
    const text = textRef.current
    if (!container || !text) return
    const check = () => setNeedsScroll(text.scrollWidth > container.clientWidth)
    check()
    const ro = new ResizeObserver(check)
    ro.observe(container)
    return () => ro.disconnect()
  }, [content])

  const rendered = <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>

  return (
    <div ref={containerRef} className="min-w-0 flex-1 overflow-hidden">
      <div
        ref={textRef}
        className={`inline-flex whitespace-nowrap [&_p]:inline [&_a]:underline ${needsScroll ? "animate-[notify-marquee_30s_linear_infinite] hover:pause" : ""}`}
      >
        <span className="pr-16">{rendered}</span>
        {needsScroll && <span className="pr-16">{rendered}</span>}
      </div>
    </div>
  )
}

function NotifyAlert({ item, dismissKey }: { item: NotifyItem; dismissKey: string }) {
  const [dismissed, setDismissed] = useState(() => {
    const stored = localStorage.getItem(dismissKey)
    return stored === hashContent(item.content)
  })

  const handleDismiss = useCallback(() => {
    localStorage.setItem(dismissKey, hashContent(item.content))
    setDismissed(true)
  }, [item.content, dismissKey])

  if (dismissed) return null

  const level = item.level || "info"
  const Icon = levelIcons[level] || Info

  return (
    <div className="px-4 pt-2 pb-0">
      <Alert className={`overflow-hidden rounded-lg py-3 ${levelStyles[level]}`}>
        <Icon />
        <AlertDescription className="min-w-0 flex items-center gap-2 text-inherit">
          <MarqueeText content={item.content} />
          <button
            type="button"
            onClick={handleDismiss}
            className="shrink-0 rounded p-0.5 opacity-60 hover:opacity-100"
          >
            <X className="size-4" />
          </button>
        </AlertDescription>
      </Alert>
    </div>
  )
}

export function NotificationBanner() {
  const [config, setConfig] = useState<NotifyConfig | null>(null)
  const { currentAgent } = useAgent()

  useEffect(() => {
    api.get<{ code: number; data: NotifyConfig | null }>("/platform/notify", { skipAuthRedirect: true })
      .then((res) => {
        if (res.data && res.data.enabled) {
          setConfig(res.data)
        }
      })
      .catch(() => {})
  }, [])

  const items = useMemo(() => {
    if (!config) return []
    const result: { key: string; item: NotifyItem }[] = []

    if (config.global && config.global.enabled !== false && config.global.content && !isExpired(config.global.expires_at)) {
      result.push({ key: "global", item: config.global })
    }

    if (config.agents && currentAgent) {
      const agentProduct = currentAgent.agent_product || ""
      const runtimeType = (currentAgent as Record<string, unknown>).runtime_type as string || ""

      for (const [matchKey, agentItem] of Object.entries(config.agents)) {
        if (!agentItem || agentItem.enabled === false || !agentItem.content || isExpired(agentItem.expires_at)) continue
        if (matchKey === agentProduct || matchKey === runtimeType) {
          result.push({ key: `agent:${matchKey}`, item: agentItem })
        }
      }
    }

    return result
  }, [config, currentAgent])

  if (items.length === 0) return null

  return (
    <>
      {items.map(({ key, item }) => (
        <NotifyAlert key={key} item={item} dismissKey={`${DISMISS_PREFIX}${key}`} />
      ))}
    </>
  )
}
