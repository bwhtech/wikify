import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CONTEXT_BIAS,
  flattenGroups,
  rankGroups,
} from "../src/utils/paletteRanking.js";

const item = (key, search, extra = {}) => ({
  key,
  label: search,
  search,
  ...extra,
});

const groups = () => [
  {
    id: "goto",
    items: [item("goto:projects", "Projects"), item("goto:ask", "Ask")],
  },
  {
    id: "projects",
    emptyLimit: 1,
    items: [item("p:a", "Alpha Handbook"), item("p:b", "Beta Policies")],
  },
  { id: "imports", long: true, items: [] },
  {
    id: "ask",
    unranked: true,
    onlyWithQuery: true,
    items: [item("ask", "Ask")],
  },
];

const keys = (ranked) => flattenGroups(ranked).map((i) => i.key);

test("empty query shows every group's starters and drops query-only groups", () => {
  assert.deepEqual(keys(rankGroups("", groups())), [
    "goto:projects",
    "goto:ask",
    "p:a",
  ]);
});

test("empty groups are dropped", () => {
  assert.deepEqual(
    rankGroups("", groups()).map((g) => g.id),
    ["goto", "projects"]
  );
});

test("query ranks within groups and keeps unranked rows", () => {
  assert.deepEqual(keys(rankGroups("beta", groups())), ["p:b", "ask"]);
});

test("rows that no longer match the query drop out", () => {
  const stale = [
    {
      id: "imports",
      long: true,
      items: [item("i:1", "Leave Policy Handbook")],
    },
  ];
  assert.deepEqual(keys(rankGroups("payroll", stale)), []);
});

test("context project wins a tie but does not bury a better match", () => {
  const tie = [
    {
      id: "imports",
      items: [
        item("i:other", "Policy", { project: "other" }),
        item("i:here", "Policy", { project: "here" }),
      ],
    },
  ];
  assert.deepEqual(keys(rankGroups("policy", tie, "here")), [
    "i:here",
    "i:other",
  ]);
  assert.ok(CONTEXT_BIAS < 2);
});

test("long rows need a tighter match than short labels", () => {
  const title = "Onboarding Guide for Remote Teams";
  const short = [{ id: "projects", items: [item("p:1", title)] }];
  const long = [{ id: "imports", long: true, items: [item("i:1", title)] }];
  assert.deepEqual(keys(rankGroups("onbrd", short)), ["p:1"]);
  assert.deepEqual(keys(rankGroups("onbrd", long)), []);
});
