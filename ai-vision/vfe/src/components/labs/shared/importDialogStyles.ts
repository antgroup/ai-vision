/** Shared presentation for MCP and Skills imports; business state stays local. */
const control = "h-9 gap-1.5 rounded-[10px] px-3"
const button = `${control} border-[var(--color-border)] bg-[var(--color-bg-page)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]`
const action = `${control} border-[var(--color-border-labs)] bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)] hover:bg-[var(--color-bg-auxiliary)] hover:text-[var(--color-labs)] dark:bg-[var(--color-bg-labs-tag)] dark:hover:bg-[var(--color-bg-auxiliary)]`
const ghost = `${control} text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]`

export const importDialogStyles = {
  dialog: "flex flex-col gap-4 overflow-hidden rounded-[12px] shadow-none",
  header:
    "-mx-6 -mt-6 shrink-0 gap-1 rounded-t-[11px] border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-6 py-4 pr-12",
  title:
    "flex items-center gap-2 text-base leading-6 font-medium [overflow-wrap:anywhere] text-[var(--color-text-primary)]",
  titleIcon:
    "flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]",
  description: "text-sm leading-[22px] text-[var(--color-text-description)]",
  tabs: "flex min-h-0 flex-1 flex-col gap-4 overflow-hidden",
  tabList: "shrink-0",
  tab: "focus-visible:outline-[var(--color-labs)] data-[state=active]:text-[var(--color-labs)] data-[state=active]:after:bg-[var(--color-labs)] data-[state=active]:after:opacity-100 dark:data-[state=active]:text-[var(--color-labs)]",
  content: "flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1",
  panel: "flex min-h-0 flex-1 flex-col gap-4 overflow-hidden",
  toolbar: "flex shrink-0 flex-col gap-3",
  results: "min-h-0 flex-1 overflow-y-auto pr-1",
  cardGrid:
    "grid grid-cols-[repeat(auto-fill,minmax(min(280px,100%),1fr))] gap-3",
  card: "flex min-w-0 flex-col rounded-[12px] border border-[var(--color-border)]",
  cardBody: "flex min-w-0 flex-1 flex-col gap-1.5 px-3.5 py-3",
  previewList:
    "flex flex-col divide-y divide-[var(--color-border)] rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg-page)]",
  cardFooter:
    "mt-auto flex items-center gap-2 border-t border-[var(--color-border)] px-3.5 py-2",
  field:
    "h-9 rounded-[10px] border-[var(--color-border)] bg-[var(--color-bg-page)] text-sm text-[var(--color-text-primary)] focus-visible:border-[var(--color-labs)] focus-visible:ring-[var(--color-border-labs)]",
  editor:
    "field-sizing-fixed h-56 shrink-0 rounded-[10px] border-[var(--color-border)] bg-[var(--color-bg-page)] font-mono text-xs leading-5 focus-visible:border-[var(--color-labs)] focus-visible:ring-[var(--color-border-labs)] sm:h-72 md:text-xs",
  button,
  action,
  primary: `${control} border-transparent bg-[var(--color-labs-action)] text-[var(--color-labs-action-text)] hover:bg-[var(--color-labs-action-hover)] dark:bg-[var(--color-labs-action)] dark:hover:bg-[var(--color-labs-action-hover)]`,
  ghost,
  compactAction: `${action} h-8`,
  compactGhost: `${ghost} h-8`,
  footer: "shrink-0 gap-2",
} as const
