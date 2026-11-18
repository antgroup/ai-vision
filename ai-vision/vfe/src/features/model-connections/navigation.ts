/** Model management has a Space scope; Agent/time filters belong to its callers. */
export function modelConnectionsPath(spaceId?: string | null) {
  const search = spaceId ? `?${new URLSearchParams({ spaceId })}` : ""
  return `/model-connections${search}`
}

export function modelConnectionPath(spaceId: string, connectionUuid: string) {
  const search = new URLSearchParams({ spaceId })
  return `/model-connections/${encodeURIComponent(connectionUuid)}?${search}`
}

export function isModelConnectionsPath(pathname: string) {
  return /^\/model-connections(?:\/[^/]+)?\/?$/.test(pathname)
}
