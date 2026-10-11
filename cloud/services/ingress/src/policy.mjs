// Deterministic route policy: the data tier is a ceiling; model output never widens it.
import { RequestError } from "./envelope.mjs";

export function choose_route({ dataTier, modelPreference }, models) {
  if (dataTier === "local-only") {
    throw new RequestError("DATA_TIER_DENIED", 403, "local-only requests must stay on the device");
  }
  if (modelPreference === "step-up") {
    if (dataTier !== "frontier-eligible") {
      throw new RequestError("DATA_TIER_DENIED", 403, "The step-up model needs data_tier frontier-eligible");
    }
    return { route: "private-cloud-step-up", modelId: models.stepUp };
  }
  return { route: "private-cloud", modelId: models.default };
}
