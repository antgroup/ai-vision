import { useEffect, useId, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Check, ChevronsUpDown, ExternalLink, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  createModelConnection,
  discoverModelConnectionModels,
  getModelDiscoveryErrorReason,
  listModelConnectionReferences,
  updateModelConnection,
} from "./api"
import {
  getModelProviders,
  inferModelProvider,
  isModelDiscoverySupported,
  normalizeModelEndpoint,
} from "./providers"
import type { ModelConnection } from "./types"
import { connectionConfigurationChanged } from "./connectionPresentation"

interface ModelConnectionFormProps {
  spaceId: string
  editing: ModelConnection | null
  onCancel: () => void
  onSaved: (connection: ModelConnection) => void
}

export function ModelConnectionForm({
  spaceId,
  editing,
  onCancel,
  onSaved,
}: ModelConnectionFormProps) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const fieldId = useId()
  const initialPreset = editing
    ? inferModelProvider(editing.endpoint)
    : undefined
  const providers = getModelProviders(i18n.resolvedLanguage ?? i18n.language)
  const [providerId, setProviderId] = useState(
    initialPreset?.provider.id ?? (editing ? "custom" : "")
  )
  const [regionId, setRegionId] = useState(initialPreset?.region.id ?? "custom")
  const [endpoint, setEndpoint] = useState(editing?.endpoint ?? "")
  const [apiKey, setApiKey] = useState("")
  const [modelName, setModelName] = useState(editing?.modelName ?? "")
  const [customName, setCustomName] = useState<string | null>(
    editing?.name ?? null
  )
  const [discoveredModels, setDiscoveredModels] = useState<string[] | null>(
    null
  )
  const [discovering, setDiscovering] = useState(false)
  const [discoveryError, setDiscoveryError] = useState<string | null>(null)
  const [modelsOpen, setModelsOpen] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [reviewImpact, setReviewImpact] = useState(false)
  const generation = useRef(0)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      generation.current += 1
    }
  }, [])

  const provider = providers.find((item) => item.id === providerId)
  const region = provider?.regions.find((item) => item.id === regionId)
  const name =
    customName ??
    (modelName.trim()
      ? `${provider ? t(provider.nameKey) : ""} · ${modelName.trim()}`.replace(
          /^ · /,
          ""
        )
      : "")
  const endpointChanged =
    !!editing &&
    normalizeModelEndpoint(endpoint) !==
      normalizeModelEndpoint(editing.endpoint)
  const canUseStoredKey = !!editing?.apiKeyConfigured && !endpointChanged
  const hasKey = !!apiKey.trim() || canUseStoredKey
  const discoverySupported = isModelDiscoverySupported(endpoint)
  const candidates = discoveredModels ?? provider?.staticModels ?? []
  const configurationChanged =
    !!editing &&
    connectionConfigurationChanged(editing, endpoint, modelName, apiKey)
  const references = useQuery({
    queryKey: ["model-connection-references", spaceId, editing?.uuid],
    queryFn: () => listModelConnectionReferences(spaceId, editing!.uuid),
    enabled: !!editing && reviewImpact && configurationChanged,
    staleTime: 0,
  })

  function resetDiscovery() {
    setReviewImpact(false)
    generation.current += 1
    setDiscovering(false)
    setDiscoveredModels(null)
    setDiscoveryError(null)
    setModelsOpen(false)
  }

  function selectProvider(id: string) {
    const next = providers.find((item) => item.id === id)
    if (!next) return
    resetDiscovery()
    setProviderId(id)
    setRegionId(next.regions[0].id)
    setEndpoint(next.regions[0].baseUrl)
    setApiKey("")
    setModelName("")
  }

  function selectRegion(id: string) {
    const next = provider?.regions.find((item) => item.id === id)
    if (!next) return
    resetDiscovery()
    setRegionId(id)
    setEndpoint(next.baseUrl)
    setApiKey("")
    setModelName("")
  }

  async function fetchModels() {
    if (discovering || !hasKey || !discoverySupported) return
    const request = ++generation.current
    setDiscovering(true)
    setDiscoveryError(null)
    try {
      const result = await discoverModelConnectionModels(spaceId, {
        endpoint: endpoint.trim(),
        ...(apiKey.trim()
          ? { api_key: apiKey.trim() }
          : { connection_uuid: editing!.uuid }),
      })
      if (!mounted.current || generation.current !== request) return
      setDiscoveredModels(result.items)
      setModelsOpen(result.items.length > 0)
    } catch (error) {
      if (!mounted.current || generation.current !== request) return
      setDiscoveryError(getModelDiscoveryErrorReason(error))
    } finally {
      if (mounted.current && generation.current === request)
        setDiscovering(false)
    }
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        name: name.trim(),
        endpoint: endpoint.trim(),
        model_name: modelName.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      }
      return editing
        ? updateModelConnection(spaceId, editing.uuid, payload)
        : createModelConnection(spaceId, { ...payload, api_key: apiKey.trim() })
    },
    onSuccess: (connection) => {
      void queryClient.invalidateQueries({
        queryKey: ["model-connections", spaceId],
      })
      if (!mounted.current) return
      toast.success(
        t(editing ? "modelConnections.updated" : "modelConnections.created")
      )
      onSaved(connection)
    },
    onMutate: () => setSaveError(null),
    onError: (error) => {
      if (!mounted.current) return
      const status = "statusCode" in error ? error.statusCode : undefined
      const reason = getModelDiscoveryErrorReason(error)
      setSaveError(
        status === 409
          ? "modelConnections.nameConflict"
          : status === 403
            ? "modelConnections.permissionDenied"
            : reason === "missing_api_key"
              ? "modelConnections.discoveryErrors.missing_api_key"
              : "modelConnections.saveFailed"
      )
    },
  })

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {t(
            editing ? "modelConnections.editTitle" : "modelConnections.create"
          )}
        </DialogTitle>
      </DialogHeader>
      <div className="grid gap-4 py-2">
        <div
          className={
            provider && provider.regions.length > 1
              ? "grid gap-4 sm:grid-cols-2"
              : "grid gap-4"
          }
        >
          <ConnectionField
            id={`${fieldId}-provider`}
            label={t("modelConnections.provider")}
            required
          >
            <Select value={providerId} onValueChange={selectProvider}>
              <SelectTrigger id={`${fieldId}-provider`} className="w-full">
                <SelectValue
                  placeholder={t("modelConnections.chooseProvider")}
                />
              </SelectTrigger>
              <SelectContent>
                {providers.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {t(item.nameKey)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </ConnectionField>
          {provider && provider.regions.length > 1 ? (
            <ConnectionField
              id={`${fieldId}-region`}
              label={t("modelConnections.region")}
              required
            >
              <Select value={regionId} onValueChange={selectRegion}>
                <SelectTrigger id={`${fieldId}-region`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {provider.regions.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {t(item.labelKey)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </ConnectionField>
          ) : null}
        </div>
        {inferModelProvider(endpoint)?.provider.id === "anthropic" ? (
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("modelConnections.anthropicHint")}
          </p>
        ) : null}
        <ConnectionField
          id={`${fieldId}-endpoint`}
          label={t("modelConnections.baseUrl")}
          required
        >
          <Input
            id={`${fieldId}-endpoint`}
            value={endpoint}
            autoComplete="off"
            placeholder="https://api.example.com/v1"
            onChange={(event) => {
              resetDiscovery()
              const nextEndpoint = event.target.value
              const preset = inferModelProvider(nextEndpoint)
              setEndpoint(nextEndpoint)
              setProviderId(preset?.provider.id ?? "custom")
              setRegionId(preset?.region.id ?? "custom")
              setApiKey("")
            }}
          />
        </ConnectionField>
        <ConnectionField
          id={`${fieldId}-key`}
          label={t("modelConnections.apiKey")}
          required={!canUseStoredKey}
          action={
            region?.apiKeyUrl ? (
              <a
                href={region.apiKeyUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                {t("modelConnections.getApiKey")}
                <ExternalLink className="size-3" />
              </a>
            ) : undefined
          }
        >
          <Input
            id={`${fieldId}-key`}
            type="password"
            autoComplete="new-password"
            value={apiKey}
            onChange={(event) => {
              resetDiscovery()
              setApiKey(event.target.value)
            }}
            placeholder={t(
              canUseStoredKey
                ? "modelConnections.replaceKeyPlaceholder"
                : "modelConnections.apiKeyPlaceholder"
            )}
          />
          {editing ? (
            <p className="text-xs text-muted-foreground">
              {t(
                endpointChanged
                  ? "modelConnections.endpointChangedKeyHint"
                  : "modelConnections.formDescription"
              )}
            </p>
          ) : null}
        </ConnectionField>
        <ConnectionField
          id={`${fieldId}-model`}
          label={t("modelConnections.model")}
          required
          action={
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1 text-xs"
              disabled={!hasKey || !discoverySupported || discovering}
              onClick={() => void fetchModels()}
            >
              {discovering ? <Loader2 className="size-3 animate-spin" /> : null}
              {t(
                discovering
                  ? "modelConnections.fetchingModels"
                  : "modelConnections.fetchModels"
              )}
            </Button>
          }
        >
          <div className="flex gap-2">
            <Input
              id={`${fieldId}-model`}
              value={modelName}
              onChange={(event) => {
                setReviewImpact(false)
                setModelName(event.target.value)
              }}
              placeholder={t("modelConnections.modelInputPlaceholder")}
              className="min-w-0 flex-1"
            />
            <Popover open={modelsOpen} onOpenChange={setModelsOpen}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  disabled={!candidates.length}
                  aria-label={t("modelConnections.browseModels")}
                  aria-expanded={modelsOpen}
                >
                  <ChevronsUpDown className="size-4" />
                </Button>
              </PopoverTrigger>
              <PopoverContent
                align="end"
                className="w-[min(24rem,calc(100vw-3rem))] p-0"
              >
                <Command>
                  <CommandInput
                    placeholder={t("modelConnections.searchModels")}
                    aria-label={t("modelConnections.searchModels")}
                  />
                  <CommandList>
                    <CommandEmpty>
                      {t("modelConnections.noModelsMatch")}
                    </CommandEmpty>
                    <CommandGroup
                      heading={t(
                        discoveredModels
                          ? "modelConnections.fetchedCandidates"
                          : "modelConnections.staticCandidates"
                      )}
                    >
                      {candidates.map((model) => (
                        <CommandItem
                          key={model}
                          value={model}
                          onSelect={() => {
                            setReviewImpact(false)
                            setModelName(model)
                            setModelsOpen(false)
                          }}
                        >
                          <span className="min-w-0 font-mono text-xs break-all">
                            {model}
                          </span>
                          {model === modelName ? (
                            <Check className="ml-auto size-3.5" />
                          ) : null}
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
          </div>
          {discoveredModels || (providerId && providerId !== "custom") ? (
            <p className="text-xs text-muted-foreground" role="status">
              {discoveredModels
                ? t(
                    discoveredModels.length
                      ? "modelConnections.fetchedModelsHint"
                      : "modelConnections.emptyModelsHint",
                    { count: discoveredModels.length }
                  )
                : t(
                    candidates.length
                      ? "modelConnections.staticModelsHint"
                      : "modelConnections.fetchOrEnterHint"
                  )}
            </p>
          ) : null}
          {endpoint.trim() && !discoverySupported ? (
            <p className="text-xs text-muted-foreground">
              {t("modelConnections.discoveryUnsupported")}
            </p>
          ) : null}
          {discoveryError ? (
            <p role="alert" className="text-xs text-destructive">
              {t(`modelConnections.discoveryErrors.${discoveryError}`)}
            </p>
          ) : null}
        </ConnectionField>
        <ConnectionField
          id={`${fieldId}-name`}
          label={t("modelConnections.name")}
          required
        >
          <Input
            id={`${fieldId}-name`}
            value={name}
            onChange={(event) => setCustomName(event.target.value)}
            placeholder={t("modelConnections.autoNamePlaceholder")}
          />
        </ConnectionField>
      </div>
      {saveError ? (
        <p role="alert" className="text-xs text-destructive">
          {t(saveError)}
        </p>
      ) : null}
      {reviewImpact && configurationChanged ? (
        <section
          className="space-y-2 rounded-lg border bg-muted/30 p-4 text-sm"
          aria-label={t("modelConnections.impactTitle")}
        >
          <h3 className="font-medium">{t("modelConnections.impactTitle")}</h3>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("modelConnections.impactDescription")}
          </p>
          {references.isLoading || references.isFetching ? (
            <p role="status" className="flex items-center text-xs">
              <Loader2 className="mr-2 size-3 animate-spin" />
              {t("modelConnections.checkingReferences")}
            </p>
          ) : references.isError ? (
            <>
              <p role="alert" className="text-xs text-destructive">
                {t("modelConnections.referencesFailed")}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void references.refetch()}
              >
                {t("common.retry")}
              </Button>
            </>
          ) : references.data?.length ? (
            <ul className="max-h-32 space-y-1 overflow-y-auto text-xs">
              {references.data.map((reference) => (
                <li key={reference.uuid} className="break-all">
                  {t("modelConnections.referenceKind")} · {reference.name}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t("modelConnections.impactNone")}
            </p>
          )}
        </section>
      ) : null}
      <DialogFooter>
        <Button
          variant="outline"
          onClick={onCancel}
          disabled={saveMutation.isPending}
        >
          {t("common.cancel")}
        </Button>
        <Button
          disabled={
            saveMutation.isPending ||
            !providerId ||
            !name.trim() ||
            !endpoint.trim() ||
            !modelName.trim() ||
            !hasKey ||
            (reviewImpact &&
              configurationChanged &&
              (references.isLoading ||
                references.isFetching ||
                references.isError ||
                !references.data))
          }
          onClick={() => {
            if (configurationChanged && !reviewImpact) setReviewImpact(true)
            else saveMutation.mutate()
          }}
        >
          {saveMutation.isPending ? (
            <Loader2 className="mr-1 size-3.5 animate-spin" />
          ) : null}
          {t(
            reviewImpact && configurationChanged
              ? "modelConnections.saveWithImpact"
              : "common.save"
          )}
        </Button>
      </DialogFooter>
    </>
  )
}

function ConnectionField({
  id,
  label,
  required,
  action,
  children,
}: {
  id: string
  label: string
  required?: boolean
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1.5">
      <div className="flex min-h-5 items-center justify-between gap-2">
        <label
          htmlFor={id}
          className="text-xs font-medium text-muted-foreground"
        >
          {label}
          {required ? <span className="text-destructive"> *</span> : null}
        </label>
        {action}
      </div>
      {children}
    </div>
  )
}
