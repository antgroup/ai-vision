import { Navigate, useLocation } from "react-router-dom"
import { openShellPluginPath } from "@/features/plugins/navigation"

/** Keep saved Sandbox links working after the plugin navigation migration. */
export default function SandboxConnectionsPage() {
  const location = useLocation()
  return (
    <Navigate
      to={`${openShellPluginPath()}${location.search}${location.hash}`}
      replace
    />
  )
}
