import type { Page } from "@playwright/test";

export async function mockAnalysisLibrary(page: Page, getRuns: () => Record<string, any>[]) {
  const root = (run: Record<string, any>, all: Record<string, any>[]) => {
    const seen = new Set<number>();
    while (run.parent_run_id && !seen.has(run.id)) {
      seen.add(run.id);
      const parent = all.find(item => item.id === run.parent_run_id);
      if (!parent) break;
      run = parent;
    }
    return run;
  };
  await page.route("**/api/v1/analysis/library?*", route => {
    const params = new URL(route.request().url()).searchParams;
    const all = getRuns();
    const groups = new Map<number, Record<string, any>[]>();
    for (const run of all) {
      const id = root(run, all).id;
      groups.set(id, [...groups.get(id) || [], run]);
    }
    const items = [...groups].map(([rootId, runs]) => {
      runs.sort((a, b) => b.id - a.id);
      const matches = runs.filter(run => {
        const context = run.context || {};
        const search = (params.get("q") || "").toLowerCase();
        const scope = params.get("scope");
        return (!search || JSON.stringify([run.question, run.output, context]).toLowerCase().includes(search))
          && (!scope || ["league:" + context.league_id, "pool:" + context.pool_id].includes(scope))
          && (!params.get("week") || String(context.week) === params.get("week"))
          && (!params.get("status") || run.status === params.get("status"));
      });
      if (!matches.length) return null;
      const match = matches[0];
      return { id: match.id, root_id: rootId, latest_id: runs[0].id, title: all.find(run => run.id === rootId)!.question, context: match.context || {}, status: match.status, preview: match.output?.summary || match.error || "", created_at: match.created_at, updated_at: runs[0].created_at, reply_count: runs.length - 1 };
    }).filter(item => item !== null).sort((a, b) => b.latest_id - a.latest_id);
    const offset = Number(params.get("offset") || 0);
    const limit = Number(params.get("limit") || 25);
    return route.fulfill({ json: { items: items.slice(offset, offset + limit), total: items.length, run_count: all.length, offset, limit, has_more: offset + limit < items.length } });
  });
  await page.route(/\/api\/v1\/analysis\/runs\/\d+$/, route => {
    const id = Number(new URL(route.request().url()).pathname.split("/").at(-1));
    const all = getRuns();
    const selected = all.find(run => run.id === id);
    if (!selected) return route.fulfill({ status: 404, json: { detail: "This saved analysis is unavailable." } });
    let run = selected;
    const turns = [];
    while (run && !turns.some(item => item.id === run.id)) {
      turns.unshift(run);
      run = all.find(item => item.id === run.parent_run_id)!;
    }
    const ancestor = root(selected, all);
    const members = all.filter(item => root(item, all).id === ancestor.id);
    const parents = new Set(members.map(item => item.parent_run_id));
    return route.fulfill({ json: { selected_id: id, root_id: ancestor.id, title: ancestor.question, context: selected.context || {}, turns, branches: members.filter(item => item.id !== id && !parents.has(item.id)).map(item => ({ id: item.id, title: item.question, status: item.status, updated_at: item.created_at })), lineage_incomplete: false } });
  });
}

export async function showAnalysisHistory(page: Page) {
  await page.locator(".analyst-workspace").waitFor({ state: "visible" });
  const back = page.getByRole("button", { name: "Back to history", exact: true });
  if (await back.isVisible()) await back.click();
}
