export class AgentQueryCryptoUnavailableError extends Error {
  constructor() {
    super(
      "Use a browser that supports secure random numbers to load encrypted queries."
    )
    this.name = "AgentQueryCryptoUnavailableError"
  }
}

export class AgentQueryCipherLoadError extends Error {
  constructor(cause: unknown) {
    super("Reload this page to load encrypted queries.", { cause })
    this.name = "AgentQueryCipherLoadError"
  }
}
