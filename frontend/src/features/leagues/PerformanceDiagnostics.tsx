import { useEffect, useState } from "react";
import { diagnosticsEnabled, readPerformanceDiagnostics } from "../../performance-diagnostics";

export default function PerformanceDiagnostics() {
  const [reading, setReading] = useState<ReturnType<typeof readPerformanceDiagnostics> | null>(null);
  useEffect(() => {
    if (!diagnosticsEnabled) return;
    setReading(readPerformanceDiagnostics());
    const timer = window.setInterval(() => setReading(readPerformanceDiagnostics()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (!diagnosticsEnabled || !reading) return null;
  const ms = (value: number | null) => value === null ? "No sample" : `${Math.round(value)} ms`;
  return <section className="league-section" aria-labelledby="performance-diagnostics-heading">
    <h2 id="performance-diagnostics-heading">Local performance diagnostics</h2>
    <p className="league-help">This page visit only; nothing is sent or saved. Reload on the device and connection you want to test. Cached loads are not cold-load measurements. The longest observed interaction is a lab sample, not field INP.</p>
    <dl className="league-source-details">
      <div><dt>First contentful paint</dt><dd>{ms(reading.fcp)}</dd></div>
      <div><dt>Largest contentful paint so far</dt><dd>{ms(reading.lcp)}</dd></div>
      <div><dt>Longest observed interaction</dt><dd>{ms(reading.interaction)}</dd></div>
      <div><dt>Cumulative layout shift</dt><dd>{reading.cls === null ? "Not supported by this browser" : reading.cls.toFixed(3)}</dd></div>
      <div><dt>Long tasks over 50 ms</dt><dd>{reading.longTasks ?? "Not supported by this browser"}</dd></div>
      <div><dt>League API transfers</dt><dd>{reading.requests} requests · {(reading.transferred / 1024).toFixed(1)} KiB transferred · {(reading.decoded / 1024).toFixed(1)} KiB decoded</dd></div>
    </dl>
  </section>;
}
