import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Download, ExternalLink, Loader2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"

import { importDialogStyles } from "../../components/labs/shared/importDialogStyles"
import { ImportRegistrySearch } from "../../components/labs/shared/ImportRegistrySearch"
import { resolveSkillRepo, searchSkillRegistry } from "./skillApi"
import type { RegistrySkill } from "./skillApi"
import { installRepoSkills, skillResolveErrorKey } from "./skillImportRemote"

const PAGE_SIZE = 20
const MIN_QUERY_LENGTH = 2

// The registry also lists non-GitHub providers. Only owner/repo sources
// have a corresponding target in the current GitHub import API.
function isGithubRepositorySource(source: string): boolean {
  return /^[^/\s]+\/[^/\s]+$/.test(source.trim())
}

interface SkillImportRegistryPanelProps {
  spaceId: string
}

/** Searches skills.sh and imports the selected result's SKILL.md. */
export function SkillImportRegistryPanel({
  spaceId,
}: SkillImportRegistryPanelProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [keyword, setKeyword] = useState("")
  const [skills, setSkills] = useState<RegistrySkill[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(false)
  const [visible, setVisible] = useState(PAGE_SIZE)
  const [installingId, setInstallingId] = useState("")
  const [failedId, setFailedId] = useState("")
  const [failedMessage, setFailedMessage] = useState("")
  const [installedIds, setInstalledIds] = useState<Set<string>>(new Set())

  const trimmed = keyword.trim()
  const tooShort = trimmed.length > 0 && trimmed.length < MIN_QUERY_LENGTH
  const shown = skills ? skills.slice(0, visible) : []

  const search = async () => {
    if (trimmed.length < MIN_QUERY_LENGTH || searching) return
    setSearching(true)
    setSearchError(false)
    try {
      const data = await searchSkillRegistry(spaceId, trimmed)
      setSkills(data.skills)
      setVisible(PAGE_SIZE)
    } catch {
      setSearchError(true)
    } finally {
      setSearching(false)
    }
  }

  const install = async (skill: RegistrySkill) => {
    if (installingId || !isGithubRepositorySource(skill.source)) return
    setInstallingId(skill.id)
    setFailedId("")
    setFailedMessage("")
    try {
      const skillId = (skill.skillId || skill.name).trim()
      if (!skillId) {
        setFailedId(skill.id)
        setFailedMessage(t("labs.skill.import.resolveErrors.unresolved"))
        return
      }
      const repo = await resolveSkillRepo(spaceId, {
        source: skill.source,
        skillId,
      })
      const [entry] = repo.skills
      if (repo.skills.length !== 1 || typeof entry?.content !== "string") {
        setFailedId(skill.id)
        setFailedMessage(t("labs.skill.import.resolveErrors.unresolved"))
        return
      }
      const result = await installRepoSkills(
        spaceId,
        skill.source,
        [entry],
        repo.branch
      )
      if (!result.saved) {
        setFailedId(skill.id)
        setFailedMessage(t("labs.skill.import.registry.installFailed"))
        return
      }
      setInstalledIds((current) => new Set(current).add(skill.id))
      await queryClient.invalidateQueries({
        queryKey: ["labs", "skill", "configs", spaceId],
      })
    } catch (error) {
      setFailedId(skill.id)
      const key = skillResolveErrorKey(error)
      setFailedMessage(key ? t(key) : "")
    } finally {
      setInstallingId("")
    }
  }

  return (
    <div className={importDialogStyles.panel}>
      <div className={importDialogStyles.toolbar}>
        <ImportRegistrySearch
          keyword={keyword}
          onKeywordChange={setKeyword}
          placeholder={t("labs.skill.import.registry.searchPlaceholder")}
          searchLabel={t("labs.skill.import.registry.searchButton")}
          onSearch={() => void search()}
          inputDisabled={searching}
          searchDisabled={trimmed.length < MIN_QUERY_LENGTH || searching}
          busy={searching}
          invalid={tooShort}
        />

        {tooShort ? (
          <p className="text-xs leading-5 text-[var(--color-text-description)]">
            {t("labs.skill.import.registry.minChars")}
          </p>
        ) : null}
      </div>

      <div className={importDialogStyles.results}>
        {searchError ? (
          <p
            role="alert"
            className="mb-3 text-xs leading-5 text-[var(--color-error)]"
          >
            {t("labs.skill.import.registry.failed")}
          </p>
        ) : null}

        {skills && skills.length === 0 && !searchError ? (
          <p className="py-6 text-center text-sm leading-[22px] text-[var(--color-text-description)]">
            {t("labs.skill.import.registry.empty")}
          </p>
        ) : null}

        <div className={importDialogStyles.cardGrid}>
          {shown.map((skill) => {
            const installed = installedIds.has(skill.id)
            const installing = installingId === skill.id
            const supported = isGithubRepositorySource(skill.source)
            return (
              <div key={skill.id} className={importDialogStyles.card}>
                <div className={importDialogStyles.cardBody}>
                  <h4 className="truncate text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
                    {skill.name || skill.skillId}
                  </h4>
                  <div className="flex items-center gap-1.5">
                    <span
                      className="min-w-0 truncate font-mono text-xs leading-5 text-[var(--color-text-secondary)]"
                      title={skill.source}
                    >
                      {skill.source}
                    </span>
                    <span
                      className="ml-auto inline-flex shrink-0 items-center gap-1 text-xs leading-5 text-[var(--color-text-secondary)]"
                      aria-label={t("labs.skill.import.registry.installsAria")}
                    >
                      <Download className="size-3" aria-hidden="true" />
                      {skill.installs.toLocaleString()}
                    </span>
                  </div>
                  {!supported ? (
                    <p
                      id={`registry-source-${skill.id}`}
                      className="text-xs leading-5 text-[var(--color-text-description)]"
                    >
                      {t("labs.skill.import.registry.githubOnly")}
                    </p>
                  ) : null}
                  {failedId === skill.id ? (
                    <p
                      role="alert"
                      className="text-xs leading-5 text-[var(--color-error)]"
                    >
                      {failedMessage ||
                        t("labs.skill.import.registry.installFailed")}
                    </p>
                  ) : null}
                </div>
                <div className={importDialogStyles.cardFooter}>
                  <Button
                    asChild
                    variant="ghost"
                    className={`${importDialogStyles.compactGhost} flex-1`}
                  >
                    <a href={skill.url} target="_blank" rel="noreferrer">
                      <ExternalLink className="size-4" />
                      {t("labs.skill.import.registry.view")}
                    </a>
                  </Button>
                  <Button
                    variant="outline"
                    className={`${importDialogStyles.compactAction} flex-1`}
                    disabled={!supported || installed || !!installingId}
                    aria-describedby={
                      !supported ? `registry-source-${skill.id}` : undefined
                    }
                    onClick={() => void install(skill)}
                  >
                    {installing ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : null}
                    {installed
                      ? t("labs.skill.import.registry.installed")
                      : installing
                        ? t("labs.skill.import.registry.installing")
                        : t("labs.skill.import.registry.install")}
                  </Button>
                </div>
              </div>
            )
          })}
        </div>

        {skills && skills.length > visible ? (
          <div className="flex justify-center pt-4">
            <Button
              variant="outline"
              className={importDialogStyles.button}
              onClick={() => setVisible((count) => count + PAGE_SIZE)}
            >
              {t("labs.skill.import.registry.loadMore")}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
