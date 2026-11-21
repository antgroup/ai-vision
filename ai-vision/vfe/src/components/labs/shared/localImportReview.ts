import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import type { LocalImportBatchCollision } from "./localImportBatchCollision"

export interface LocalImportCandidate<T> {
  name: string
  source: string
  detail?: string
  value: T
  fingerprint: string
}

export interface LocalImportOverwrite {
  name: string
  version: string
}

export interface LocalImportMatch extends LocalImportOverwrite {
  requestedName: string
}

export function fingerprintImportValue(value: unknown): string {
  return (
    JSON.stringify(value, (_key, item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return item
      return Object.fromEntries(
        Object.keys(item)
          .sort()
          .map((key) => [key, item[key]])
      )
    }) ?? ""
  )
}

type ConflictAction = "keep" | "replace" | "rename"
type NameError = "invalidName" | "nameTaken" | "nameCheckFailed"

interface Choice {
  source?: number
  action?: ConflictAction
  version?: string
  draftName?: string
  acceptedName?: string
  checking?: symbol
  error?: NameError
}

interface ReviewState<T> {
  candidates: readonly LocalImportCandidate<T>[] | null
  revision: number
  choices: ReadonlyMap<string, Choice>
  saved: ReadonlySet<LocalImportCandidate<T>>
  linkedGroups: ReadonlyMap<string, string>
}

interface Group<T> {
  key: string
  name: string
  members: LocalImportCandidate<T>[]
  options: LocalImportCandidate<T>[]
  fingerprints: Set<string>
  duplicateCount: number
  existing?: LocalImportMatch
}

interface ConflictRow {
  key: string
  name: string
  options: Pick<LocalImportCandidate<unknown>, "source" | "detail">[]
  selected?: number
  hasSource: boolean
  duplicateCount: number
  existing?: LocalImportMatch
  action: ConflictAction
  replaceConfirmed: boolean
  draftName: string
  acceptedName?: string
  checking: boolean
  error?: NameError
}

interface Entry<T> {
  groupKey: string
  name: string
  value: T
  conflict: boolean
  overwrite?: LocalImportOverwrite
}

const nameKey = (name: string) => name.trim().toLowerCase().normalize("NFC")
const MAX_CHECK_NAMES = 50
const MAX_NAME_LENGTH = 128

function groupCandidates<T>(
  candidates: readonly LocalImportCandidate<T>[],
  matches: ReadonlyMap<string, LocalImportMatch>,
  linkedGroups: ReadonlyMap<string, string>
) {
  const groups = new Map<string, Group<T>>()
  for (const candidate of candidates) {
    const existing = matches.get(candidate.name)
    let key = nameKey(existing?.name ?? candidate.name)
    for (
      let linked = linkedGroups.get(key);
      linked;
      linked = linkedGroups.get(key)
    )
      key = linked
    let group = groups.get(key)
    if (!group) {
      group = {
        key,
        name: existing?.name ?? candidate.name,
        members: [],
        options: [],
        fingerprints: new Set(),
        duplicateCount: 0,
        existing,
      }
      groups.set(key, group)
    }
    group.members.push(candidate)
    group.existing ??= existing
    if (group.fingerprints.has(candidate.fingerprint)) {
      group.duplicateCount++
    } else {
      group.fingerprints.add(candidate.fingerprint)
      group.options.push(candidate)
    }
  }
  return [...groups.values()]
}

function suggestName(name: string, taken: ReadonlySet<string>) {
  for (let number = 2; number <= taken.size + 2; number++) {
    const suffix = `-${number}`
    const prefix = Array.from(name)
      .slice(0, MAX_NAME_LENGTH - suffix.length)
      .join("")
    const candidate = `${prefix}${suffix}`
    if (!taken.has(nameKey(candidate))) return candidate
  }
  return ""
}

type CheckMatches = (
  spaceId: string,
  names: string[],
  signal?: AbortSignal
) => Promise<LocalImportMatch[]>

async function checkNames(
  check: CheckMatches,
  spaceId: string,
  names: string[],
  signal?: AbortSignal
) {
  const matches: LocalImportMatch[] = []
  for (let offset = 0; offset < names.length; offset += MAX_CHECK_NAMES) {
    signal?.throwIfAborted()
    matches.push(
      ...(await check(
        spaceId,
        names.slice(offset, offset + MAX_CHECK_NAMES),
        signal
      ))
    )
  }
  return matches
}

export function useLocalImportReview<T>(
  feature: "mcp" | "skill",
  spaceId: string,
  candidates: readonly LocalImportCandidate<T>[] | null,
  check: CheckMatches
) {
  const [state, setState] = useState<ReviewState<T>>({
    candidates,
    revision: 0,
    choices: new Map(),
    saved: new Set(),
    linkedGroups: new Map(),
  })
  if (state.candidates !== candidates) {
    setState({
      candidates,
      revision: state.revision + 1,
      choices: new Map(),
      saved: new Set(),
      linkedGroups: new Map(),
    })
  }
  const names = useMemo(
    () => [...new Set(candidates?.map(({ name }) => name))],
    [candidates]
  )
  const lookup = useQuery({
    queryKey: [
      "labs",
      feature,
      "import-conflicts",
      spaceId,
      state.revision,
      names,
    ],
    enabled: !!spaceId && names.length > 0,
    queryFn: ({ signal }) => checkNames(check, spaceId, names, signal),
    refetchOnWindowFocus: false,
    retry: false,
    gcTime: 0,
  })
  const status =
    candidates === null
      ? "idle"
      : !names.length
        ? "ready"
        : lookup.isFetching || lookup.isPending
          ? "loading"
          : lookup.isError
            ? "error"
            : "ready"
  const matches = useMemo(
    () => new Map(lookup.data?.map((match) => [match.requestedName, match])),
    [lookup.data]
  )
  const groups = useMemo(
    () =>
      groupCandidates(
        (candidates ?? []).filter((candidate) => !state.saved.has(candidate)),
        matches,
        state.linkedGroups
      ),
    [candidates, matches, state.linkedGroups, state.saved]
  )
  const taken = useMemo(
    () => new Set(names.map(nameKey).concat(groups.map(({ key }) => key))),
    [names, groups]
  )
  const rows: ConflictRow[] = []
  const entries: Entry<T>[] = []
  let needsDecision = false

  for (const group of groups) {
    const choice = state.choices.get(group.key) ?? {}
    const selected = group.options.length === 1 ? 0 : choice.source
    const candidate =
      selected === undefined ? undefined : group.options[selected]
    const action = choice.action ?? "keep"
    const replaceConfirmed =
      !!group.existing && choice.version === group.existing.version
    const conflict =
      group.options.length > 1 || group.duplicateCount > 0 || !!group.existing

    if (conflict) {
      const showNames =
        new Set(group.options.map(({ name }) => nameKey(name))).size > 1
      rows.push({
        key: group.key,
        name: group.name,
        options: group.options.map(({ name, source, detail }) => ({
          source: showNames ? `${name} · ${source}` : source,
          detail,
        })),
        selected,
        hasSource: !!candidate,
        duplicateCount: group.duplicateCount,
        existing: group.existing,
        action,
        replaceConfirmed,
        draftName: choice.draftName ?? "",
        acceptedName: choice.acceptedName,
        checking: !!choice.checking,
        error: choice.error,
      })
    }
    if (
      (!group.existing && group.options.length > 1 && selected === undefined) ||
      (action === "replace" &&
        group.existing &&
        (!candidate || !replaceConfirmed)) ||
      (action === "rename" && (!candidate || !choice.acceptedName))
    )
      needsDecision = true

    if (status !== "ready" || !candidate) continue
    if (action === "rename") {
      if (choice.acceptedName)
        entries.push({
          groupKey: group.key,
          name: choice.acceptedName,
          value: candidate.value,
          conflict,
        })
    } else if (!group.existing) {
      entries.push({
        groupKey: group.key,
        name: candidate.name,
        value: candidate.value,
        conflict,
      })
    } else if (action === "replace" && replaceConfirmed) {
      const name =
        matches.get(candidate.name)?.name === group.existing.name
          ? candidate.name
          : group.existing.name
      entries.push({
        groupKey: group.key,
        name,
        value: candidate.value,
        conflict,
        overwrite: { name, version: group.existing.version },
      })
    }
  }

  function update(
    key: string,
    change: (choice: Choice, current: ReviewState<T>) => Choice
  ) {
    setState((current) => {
      if (current.candidates !== candidates) return current
      const choices = new Map(current.choices)
      choices.set(key, change(choices.get(key) ?? {}, current))
      return { ...current, choices }
    })
  }

  function chooseSource(key: string, source: number | undefined) {
    update(key, (choice) => ({
      ...choice,
      source,
      version: undefined,
      acceptedName: undefined,
      checking: undefined,
      error: undefined,
    }))
  }

  function chooseAction(key: string, action: ConflictAction) {
    const row = rows.find((item) => item.key === key)
    const group = groups.find((item) => item.key === key)
    if (!row || !group) return
    const unavailable = new Set(taken)
    for (const item of rows) {
      if (item.acceptedName) unavailable.add(nameKey(item.acceptedName))
    }
    const sourceName = group.options[row.selected ?? 0]?.name ?? row.name
    update(key, (choice) => ({
      source: choice.source,
      action,
      draftName:
        action === "rename" ? suggestName(sourceName, unavailable) : undefined,
    }))
  }

  function confirmReplace(key: string) {
    const row = rows.find((item) => item.key === key)
    if (row?.existing && row.hasSource)
      update(key, (choice) => ({ ...choice, version: row.existing?.version }))
  }

  function editName(key: string, draftName: string) {
    update(key, (choice) => ({
      ...choice,
      draftName,
      acceptedName: undefined,
      error: undefined,
      checking: undefined,
    }))
  }

  async function confirmName(key: string) {
    const row = rows.find((item) => item.key === key)
    if (!row || !row.hasSource || row.checking) return
    const name = row.draftName.trim()
    const normalized = nameKey(name)
    const alreadySelected = (current: ReviewState<T>) =>
      [...current.choices].some(
        ([other, choice]) =>
          other !== key &&
          choice.acceptedName &&
          nameKey(choice.acceptedName) === normalized
      )
    if (!name || Array.from(name).length > MAX_NAME_LENGTH) {
      update(key, (choice) => ({ ...choice, error: "invalidName" }))
      return
    }
    if (taken.has(normalized) || alreadySelected(state)) {
      update(key, (choice) => ({ ...choice, error: "nameTaken" }))
      return
    }
    const request = Symbol()
    update(key, (choice) => ({
      ...choice,
      checking: request,
      error: undefined,
    }))
    let error: NameError | undefined
    try {
      if ((await check(spaceId, [name])).length) error = "nameTaken"
    } catch {
      error = "nameCheckFailed"
    }
    update(key, (choice, current) => {
      if (choice.checking !== request) return choice
      const problem =
        error ?? (alreadySelected(current) ? "nameTaken" : undefined)
      return {
        ...choice,
        acceptedName: problem ? undefined : name,
        error: problem,
        checking: undefined,
      }
    })
  }

  function reportBatchCollision(collision?: LocalImportBatchCollision) {
    if (!collision) return false
    const requested = entries.find(
      ({ name }) => name === collision.requestedName
    )
    const earlier = entries.find(
      ({ name }) => name === collision.conflictingRequestedName
    )
    if (!requested || !earlier || requested.groupKey === earlier.groupKey)
      return false

    // A collision involving an alternative destination needs a new name for
    // that destination. The original sources still belong to separate groups.
    const renamed = [requested, earlier].find(
      ({ groupKey, name }) => state.choices.get(groupKey)?.acceptedName === name
    )
    if (renamed) {
      update(renamed.groupKey, (choice) => ({
        ...choice,
        acceptedName: undefined,
        error: "nameTaken",
      }))
      return true
    }

    setState((current) => {
      if (current.candidates !== candidates) return current
      // These keys are the current representatives, so a later collision can
      // join an already linked group without leaving its other aliases behind.
      const linkedGroups = new Map(current.linkedGroups)
      linkedGroups.set(requested.groupKey, earlier.groupKey)
      const choices = new Map(current.choices)
      choices.delete(requested.groupKey)
      choices.delete(earlier.groupKey)
      return { ...current, linkedGroups, choices }
    })
    return true
  }

  return {
    entries,
    rows,
    status,
    needsDecision,
    canFinishWithoutSaving:
      status === "ready" &&
      !needsDecision &&
      !entries.length &&
      rows.some(({ existing, action }) => existing && action === "keep"),
    chooseSource,
    chooseAction,
    confirmReplace,
    editName,
    confirmName,
    reportBatchCollision,
    async refresh() {
      await lookup.refetch()
      const renames = rows.flatMap((row) =>
        row.acceptedName ? [[row.key, row.acceptedName] as const] : []
      )
      if (!renames.length) return
      let used: Set<string> | undefined
      try {
        const matches = await checkNames(
          check,
          spaceId,
          renames.map(([, name]) => name)
        )
        used = new Set(matches.map((match) => match.requestedName))
      } catch {
        // Keep a pending rename unconfirmed when its destination cannot be rechecked.
      }
      setState((current) => {
        if (current.candidates !== candidates) return current
        const choices = new Map(current.choices)
        for (const [key, name] of renames) {
          const choice = choices.get(key)
          if (choice?.acceptedName === name && (!used || used.has(name))) {
            choices.set(key, {
              ...choice,
              acceptedName: undefined,
              error: used ? "nameTaken" : "nameCheckFailed",
            })
          }
        }
        return { ...current, choices }
      })
    },
    markSaved(requestedNames: ReadonlySet<string>) {
      const settledKeys = new Set(
        entries.flatMap(({ name, groupKey }) =>
          requestedNames.has(name) ? [groupKey] : []
        )
      )
      if (!settledKeys.size) return
      setState((current) => {
        if (current.candidates !== candidates) return current
        const saved = new Set(current.saved)
        const choices = new Map(current.choices)
        // Settle the sources covered by each receipt before the next lookup
        // can regroup pending sources under a newly saved database name.
        for (const group of groups) {
          if (settledKeys.has(group.key)) {
            group.members.forEach((candidate) => saved.add(candidate))
            choices.delete(group.key)
          }
        }
        return { ...current, saved, choices }
      })
    },
  } as const
}

export type LocalImportReview<T> = ReturnType<typeof useLocalImportReview<T>>
