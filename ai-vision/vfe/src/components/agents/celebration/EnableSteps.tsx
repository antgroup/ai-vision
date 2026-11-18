import { useTranslation } from "react-i18next"
import { useEffect, useState } from "react"
import { Check, Circle } from "lucide-react"

const STEPS = [
  "agentAccess.enableSteps.channel",
  "agentAccess.enableSteps.endpoint",
  "agentAccess.enableSteps.configuration",
]

const STEP_DELAY = 500

export function EnableSteps({ onComplete }: { onComplete: () => void }) {
  const { t } = useTranslation()
  const [doneCount, setDoneCount] = useState(0)

  useEffect(() => {
    if (doneCount < STEPS.length) {
      const timer = setTimeout(
        () => setDoneCount((count) => count + 1),
        STEP_DELAY
      )
      return () => clearTimeout(timer)
    }
    const timer = setTimeout(onComplete, 400)
    return () => clearTimeout(timer)
  }, [doneCount, onComplete])

  const progress = (doneCount / STEPS.length) * 100

  return (
    <div className="space-y-5 py-2">
      <ul className="space-y-3">
        {STEPS.map((label, index) => {
          const done = index < doneCount
          const active = index === doneCount
          return (
            <li key={label} className="flex items-center gap-3">
              <div
                className={`flex size-6 shrink-0 items-center justify-center rounded-full transition-colors duration-300 ${
                  done
                    ? "bg-emerald-500 text-white"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                {done ? (
                  <Check className="size-3.5" />
                ) : (
                  <Circle
                    className={`size-3.5 ${active ? "animate-pulse" : ""}`}
                  />
                )}
              </div>
              <span
                className={`text-sm transition-colors duration-300 ${
                  done ? "font-medium text-foreground" : "text-muted-foreground"
                }`}
              >
                {t(label)}
              </span>
            </li>
          )
        })}
      </ul>

      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-emerald-500 transition-all duration-500 ease-out"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  )
}
