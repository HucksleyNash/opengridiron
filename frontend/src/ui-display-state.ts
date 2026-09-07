export type NewsFeedItem = {
  id: number;
  title: string;
  excerpt: string;
  category: string;
  severity: string;
  canonical_url: string;
  published_at?: string;
};

export function formatSourceLabel(source: string): string {
  if (source.startsWith("yahoo_")) return "Yahoo";
  if (source === "nflverse.draft_model") return "NFLverse draft model";
  return source.replaceAll("_", " ");
}

export function uniqueAlertsByTitle<T extends { title: string }>(alerts: T[]): T[] {
  const seen = new Set<string>();
  return alerts.filter((alert) => {
    const key = alert.title.trim().toLocaleLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function filterNewsItems(items: NewsFeedItem[], query: string, category: string): NewsFeedItem[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  return items.filter((item) => {
    if (category !== "all" && item.category !== category) return false;
    if (!normalizedQuery) return true;
    return `${item.title} ${item.excerpt}`.toLocaleLowerCase().includes(normalizedQuery);
  });
}

export function weeklyCardProgress(
  poolType: "survivor" | "confidence",
  selectionCount: number,
  requiredCount: number,
  weightCount?: number | null,
): string {
  const selections = `${selectionCount}/${requiredCount} ${requiredCount === 1 ? "selection" : "selections"}`;
  if (poolType !== "confidence" || typeof weightCount !== "number") return selections;
  return `${selections} · ${weightCount}/${requiredCount} weights`;
}
