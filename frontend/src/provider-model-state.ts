export const DISCOVERABLE_PROVIDER_TYPES = ["openai", "anthropic", "codex"] as const;

export function supportsProviderModelDiscovery(providerType: string, baseUrl = ""): boolean {
  return DISCOVERABLE_PROVIDER_TYPES.some((type) => type === providerType)
    && !(providerType === "codex" && baseUrl);
}

export function providerModelControl(
  providerType: string,
  manualEntry: boolean,
  baseUrl = "",
): "select" | "input" {
  return supportsProviderModelDiscovery(providerType, baseUrl) && !manualEntry ? "select" : "input";
}

export function providerModelPlaceholder({
  loading,
  hasCredential,
  optionCount,
  providerType,
}: {
  loading: boolean;
  hasCredential: boolean;
  optionCount: number;
  providerType?: string;
}): string {
  if (loading) return "Loading available models…";
  if (optionCount > 0) return "Select a model";
  if (hasCredential) return "No models loaded. Retry below.";
  return providerType === "codex"
    ? "Connect Codex CLI, then load models"
    : "Enter an API key, then load models";
}
