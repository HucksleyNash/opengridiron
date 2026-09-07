import type { Forecast, WeeklyReport } from "./types";

export function safeSourceUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password;
  } catch { return false; }
}

export function sourceLabel(value: string, sources: WeeklyReport["sources"]): string {
  const source = sources.find((item) => item.url === value);
  const url = new URL(value);
  const host = url.hostname.replace(/^www\./, "");
  let title = url.pathname.split("/").filter(Boolean).at(-1) || "Home";
  try { title = decodeURIComponent(title); } catch { /* Preserve malformed path escapes as text. */ }
  const isDownload = /\.(csv|parquet|json|zip|pdf)$/i.test(title);
  if (source) return `${source.name}${isDownload ? " (file)" : ""}`;
  title = title.replace(/[-_]+/g, " ");
  return `${host} · ${title}${isDownload ? " (file)" : ""}`;
}

export function availability(player?: Forecast, conditional = false) {
  const status = player?.status?.trim() || "Status not supplied";
  const knownActive = ["ACTIVE", "A", "HEALTHY"].includes(status.toUpperCase());
  const needsVerification = conditional || !player || player.conditional || !knownActive || player.points == null || Boolean(player.warnings.length);
  return { status, needsVerification, confidence: !player || player.confidence === "unavailable" ? "Forecast unavailable" : `${player.confidence} confidence` };
}

export function reportForWeek<T extends { id: number; week: number; has_report: boolean }>(runs: T[], week: number, selectedId?: number): T | undefined {
  const matching = runs.filter((run) => run.week === week);
  return matching.find((run) => run.id === selectedId) || matching.find((run) => run.has_report) || matching[0];
}
