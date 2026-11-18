import { PluginWorkspace } from "@/features/plugins/PluginWorkspace"

export default function PluginsPage() {
  return <PluginWorkspace level="catalog" />
}

export function SandboxPluginPage() {
  return <PluginWorkspace level="sandbox" />
}

export function OpenShellPluginPage() {
  return <PluginWorkspace level="openshell" />
}
