// Opt-in, local-only measurements. No telemetry, persistence, or third-party requests.
export const diagnosticsEnabled = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("diagnostics") === "1";
const measurements = { lcp: null as number | null, interaction: null as number | null, cls: null as number | null, longTasks: null as number | null };

export function startPerformanceDiagnostics() {
  if (!diagnosticsEnabled || typeof PerformanceObserver === "undefined") return;
  let windowStart = 0, previousShift = 0, windowScore = 0;
  for (const type of ["largest-contentful-paint", "event", "layout-shift", "longtask"]) {
    if (!PerformanceObserver.supportedEntryTypes.includes(type)) continue;
    if (type === "layout-shift") measurements.cls = 0;
    if (type === "longtask") measurements.longTasks = 0;
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (type === "largest-contentful-paint") measurements.lcp = entry.startTime;
        if (type === "longtask") measurements.longTasks = (measurements.longTasks || 0) + 1;
        if (type === "event" && "interactionId" in entry && entry.interactionId) measurements.interaction = Math.max(measurements.interaction || 0, entry.duration);
        if (type === "layout-shift") {
          const shift = entry as PerformanceEntry & { hadRecentInput: boolean; value: number };
          if (shift.hadRecentInput) continue;
          if (entry.startTime - previousShift > 1000 || entry.startTime - windowStart > 5000) { windowStart = entry.startTime; windowScore = 0; }
          previousShift = entry.startTime;
          windowScore += shift.value;
          measurements.cls = Math.max(measurements.cls || 0, windowScore);
        }
      }
    });
    observer.observe({ type, buffered: true, ...(type === "event" ? { durationThreshold: 16 } : {}) });
  }
}

export function readPerformanceDiagnostics() {
  const fcp = performance.getEntriesByName("first-contentful-paint")[0]?.startTime ?? null;
  const resources = (performance.getEntriesByType("resource") as PerformanceResourceTiming[])
    .filter((entry) => new URL(entry.name).pathname.startsWith("/api/v1/leagues/"));
  return { ...measurements, fcp, requests: resources.length,
    transferred: resources.reduce((total, entry) => total + entry.transferSize, 0),
    decoded: resources.reduce((total, entry) => total + entry.decodedBodySize, 0),
  };
}
