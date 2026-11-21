export class LocalImportBatchCollision extends Error {
  readonly requestedName: string
  readonly conflictingRequestedName: string

  constructor(
    requestedName: string,
    conflictingRequestedName: string,
    message: string
  ) {
    super(message)
    this.requestedName = requestedName
    this.conflictingRequestedName = conflictingRequestedName
  }

  matchesBatch(names: readonly string[]) {
    return (
      names.includes(this.requestedName) &&
      names.includes(this.conflictingRequestedName)
    )
  }
}

export function readLocalImportBatchCollision(data: unknown, message: string) {
  if (
    !data ||
    typeof data !== "object" ||
    !("requestedName" in data) ||
    !("conflictingRequestedName" in data)
  )
    return
  const { requestedName, conflictingRequestedName } = data
  if (
    typeof requestedName === "string" &&
    requestedName &&
    typeof conflictingRequestedName === "string" &&
    conflictingRequestedName &&
    requestedName !== conflictingRequestedName
  )
    return new LocalImportBatchCollision(
      requestedName,
      conflictingRequestedName,
      message
    )
}
