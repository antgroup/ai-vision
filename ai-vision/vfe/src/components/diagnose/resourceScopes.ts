export const USER_SHARED_SPACE_UUID = "s-user_shared"
export const PLATFORM_MANAGED_ACTOR_NAME = "AI Vision"

export function isPlatformManagedJob(job: {
  space_uuid: string
  spec: Record<string, unknown>
}): boolean {
  return (
    isUserSharedSpace(job.space_uuid) ||
    job.spec.managed_by === "aivision.personal_diagnosis"
  )
}

export function isUserSharedSpace(
  spaceUuid: string | null | undefined
): boolean {
  return spaceUuid === USER_SHARED_SPACE_UUID
}
