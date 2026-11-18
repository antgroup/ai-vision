import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  materialBytes,
  validateMaterials,
  type RuleMaterials,
  type RuleMaterialFile,
} from "./enhancedRuleModel"

export function EnhancedRuleMaterials({
  value,
  expectedText,
  onExpectedChange,
  onChange,
}: {
  value: RuleMaterials
  expectedText: string
  onExpectedChange: (value: string) => void
  onChange: (value: RuleMaterials) => void
}) {
  const { t } = useTranslation()
  const [uploadError, setUploadError] = useState<string | null>(null)
  const uploadGeneration = useRef(0)
  const fileInput = useRef<HTMLInputElement>(null)
  useEffect(
    () => () => {
      uploadGeneration.current += 1
    },
    []
  )
  const current = useRef(value)
  useLayoutEffect(() => {
    current.current = value
  }, [value])
  const updateFile = (index: number, patch: Partial<RuleMaterialFile>) => {
    onChange({
      ...value,
      files: value.files.map((file, i) =>
        i === index ? { ...file, ...patch, sha256: undefined } : file
      ),
    })
  }
  return (
    <details className="min-w-0 border-t pt-3">
      <summary className="cursor-pointer rounded-sm text-sm font-medium focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
        {t("enhancedRule.materials")}
        {value.files.length ? ` (${value.files.length})` : ""}
      </summary>
      <div className="mt-3 space-y-3">
        <p className="text-xs text-muted-foreground">
          {t("enhancedRule.limits")}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({
                ...value,
                files: [
                  ...value.files,
                  { path: "", encoding: "utf-8", content: "" },
                ],
              })
            }
          >
            {t("enhancedRule.addText")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => fileInput.current?.click()}
          >
            {t("enhancedRule.upload")}
          </Button>
          <div>
            <input
              type="file"
              ref={fileInput}
              tabIndex={-1}
              multiple
              accept=".py,.json,.csv,.xlsx,.txt"
              aria-label={t("enhancedRule.upload")}
              hidden
              onChange={async (event) => {
                const files = Array.from(event.target.files ?? [])
                event.target.value = ""
                const generation = ++uploadGeneration.current
                const initial = current.current
                if (initial.files.length + files.length > 20) {
                  setUploadError("fileCount")
                  return
                }
                if (files.some((file) => file.size > 256 * 1024)) {
                  setUploadError("fileSize")
                  return
                }
                try {
                  const additions = await Promise.all(
                    files.map(async (file) => {
                      const binary = file.name.toLowerCase().endsWith(".xlsx")
                      const content = binary
                        ? btoa(
                            Array.from(
                              new Uint8Array(await file.arrayBuffer()),
                              (byte) => String.fromCharCode(byte)
                            ).join("")
                          )
                        : await file.text()
                      return {
                        path: file.name,
                        encoding: binary
                          ? ("base64" as const)
                          : ("utf-8" as const),
                        content,
                      }
                    })
                  )
                  if (
                    generation !== uploadGeneration.current ||
                    current.current !== initial
                  )
                    return
                  const next = {
                    ...initial,
                    files: [...initial.files, ...additions],
                  }
                  const error = validateMaterials(next)
                  setUploadError(error)
                  if (!error) onChange(next)
                } catch {
                  if (generation === uploadGeneration.current)
                    setUploadError("encoding")
                }
              }}
            />
          </div>
        </div>
        {uploadError && (
          <p role="alert" className="text-xs text-destructive">
            {t(`enhancedRule.errors.${uploadError}`)}
          </p>
        )}
        {value.files.map((file, index) => (
          <div key={index} className="space-y-2 border-l-2 pl-3">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                aria-label={t("enhancedRule.path", { index: index + 1 })}
                value={file.path}
                onChange={(event) =>
                  updateFile(index, { path: event.target.value })
                }
                className="min-w-0 flex-1 font-mono text-xs"
                placeholder="tests/test_candidate.py"
              />
              <span className="text-xs text-muted-foreground">
                {(() => {
                  try {
                    return `${materialBytes(file)} B`
                  } catch {
                    return "—"
                  }
                })()}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={t("enhancedRule.removeFile", {
                  path: file.path || index + 1,
                })}
                onClick={() =>
                  onChange({
                    ...value,
                    files: value.files.filter((_, i) => i !== index),
                  })
                }
              >
                {t("common.delete")}
              </Button>
            </div>
            {file.encoding === "utf-8" ? (
              <Textarea
                aria-label={t("enhancedRule.content", {
                  path: file.path || index + 1,
                })}
                value={file.content}
                onChange={(event) =>
                  updateFile(index, { content: event.target.value })
                }
                rows={5}
                className="font-mono text-xs"
                spellCheck={false}
              />
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("enhancedRule.binary")}
              </p>
            )}
          </div>
        ))}
        <label className="block space-y-1.5 text-xs text-muted-foreground">
          <span>{t("enhancedRule.expected")}</span>
          <Textarea
            aria-label={t("enhancedRule.expected")}
            value={expectedText}
            onChange={(event) => onExpectedChange(event.target.value)}
            placeholder='{"answer": 42}'
            rows={3}
            spellCheck={false}
            className="font-mono text-xs"
          />
        </label>
      </div>
    </details>
  )
}
