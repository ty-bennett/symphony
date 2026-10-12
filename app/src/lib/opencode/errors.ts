/** An error with the HTTP status the API should answer with. */
export class OcError extends Error {
  constructor(
    message: string,
    readonly status = 502,
  ) {
    super(message)
  }
}
