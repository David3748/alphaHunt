import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the alphaHunt research dashboard", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>alphaHunt — Evidence to signals<\/title>/i);
  assert.match(html, /Evidence first/);
  assert.match(html, /Causal blend/);
  assert.match(html, /CURRENT RESEARCH/);
  assert.match(html, /2009–2025 locked test/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/i);
});

test("ships the research artifacts and current watchlist", async () => {
  const [results, hypotheses, recommendations] = await Promise.all([
    readFile(new URL("../public/data/results.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/hypotheses.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/current_recommendations.json", import.meta.url), "utf8"),
  ]);
  const result = JSON.parse(results);
  const hypothesis = JSON.parse(hypotheses);
  const current = JSON.parse(recommendations);
  assert.ok(result.trades.length > 250);
  assert.equal(hypothesis.rules.causal_blend.passes_secondary_gate, true);
  assert.equal(current.signals.length, 4);
  assert.ok(current.signals.every((row) => row.method && row.invalidation));
});

test("ships the P(+20%) curve, span stats, and current book", async () => {
  const [timeseries, spans, book] = await Promise.all([
    readFile(new URL("../public/data/timeseries.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/spans.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/p_plus20_current.json", import.meta.url), "utf8"),
  ]);
  const curve = JSON.parse(timeseries);
  const spanStats = JSON.parse(spans);
  const current = JSON.parse(book);
  assert.equal(curve.rule, "p_plus20");
  assert.ok(curve.series.length > 400);
  assert.equal(curve.series[0].nav, 1);
  assert.ok(curve.series[curve.series.length - 1].nav > curve.series[curve.series.length - 1].spy);
  assert.ok(spanStats.universe_all_eligible.n >= 9000);
  assert.ok(spanStats.p_plus20_executed.pct > spanStats.universe_all_eligible.pct);
  assert.equal(current.rule, "p_plus20");
  assert.ok(current.trades.length >= 5);
  assert.ok(current.trades.every((row) => row.thesis && row.invalidation));
  assert.ok(current.trades.every((row) => typeof row.excess_return === "number"));
});
