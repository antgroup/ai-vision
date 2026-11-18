import type { Ref } from "react"
import { Loader2, Search } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { importDialogStyles } from "./importDialogStyles"

interface ImportRegistrySearchProps {
  keyword: string
  onKeywordChange: (value: string) => void
  placeholder: string
  inputRef?: Ref<HTMLInputElement>
  source?: string
  sources?: readonly { id: string; label: string }[]
  sourceLabel?: string
  onSourceChange?: (value: string) => void
  searchLabel: string
  onSearch: () => void
  inputDisabled?: boolean
  searchDisabled?: boolean
  busy?: boolean
  invalid?: boolean
}

/** Common search/source controls; each importer owns provider and request rules. */
export function ImportRegistrySearch({
  keyword,
  onKeywordChange,
  placeholder,
  inputRef,
  source,
  sources,
  sourceLabel,
  onSourceChange,
  searchLabel,
  onSearch,
  inputDisabled = false,
  searchDisabled = false,
  busy = false,
  invalid = false,
}: ImportRegistrySearchProps) {
  const SearchIcon = busy ? Loader2 : Search
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (!searchDisabled) onSearch()
      }}
    >
      <div className="relative min-w-0 flex-1 basis-[calc(100%-6rem)] sm:basis-0">
        <SearchIcon
          aria-hidden="true"
          className={`pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-[var(--color-text-description)] ${busy ? "motion-safe:animate-spin" : ""}`}
        />
        <Input
          ref={inputRef}
          value={keyword}
          onChange={(event) => onKeywordChange(event.target.value)}
          aria-label={placeholder}
          placeholder={placeholder}
          spellCheck={false}
          disabled={inputDisabled}
          aria-invalid={invalid}
          className={`${importDialogStyles.field} pl-9`}
        />
      </div>
      {sources && sources.length > 1 ? (
        <select
          aria-label={sourceLabel}
          value={source}
          onChange={(event) => onSourceChange?.(event.target.value)}
          className={`${importDialogStyles.field} order-3 w-full min-w-0 border px-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)] sm:order-none sm:w-52 sm:shrink-0`}
        >
          {sources.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.label}
            </option>
          ))}
        </select>
      ) : null}
      <Button
        type="submit"
        variant="outline"
        className={importDialogStyles.action}
        disabled={searchDisabled}
        aria-busy={busy}
      >
        {searchLabel}
      </Button>
    </form>
  )
}
