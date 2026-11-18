/** Plugin configuration keeps its Space scope across category and provider pages. */
function pluginPath(pathname: string, spaceId?: string | null) {
  const search = spaceId ? `?${new URLSearchParams({ spaceId })}` : ""
  return `${pathname}${search}`
}

export function pluginsPath(spaceId?: string | null) {
  return pluginPath("/plugins", spaceId)
}

export function sandboxPluginPath(spaceId?: string | null) {
  return pluginPath("/plugins/sandbox", spaceId)
}

export function openShellPluginPath(spaceId?: string | null) {
  return pluginPath("/plugins/sandbox/openshell", spaceId)
}

export function isPluginsPath(pathname: string) {
  return pathname === "/plugins" || pathname.startsWith("/plugins/")
}
