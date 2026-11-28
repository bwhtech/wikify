import { test } from "node:test";
import assert from "node:assert/strict";
import { agentDraft } from "../src/components/review/meta.js";

const issue = (lint) => ({
  title: "Table rows have the wrong number of cells",
  reason: "Some rows have more or fewer cells than the header.",
  lint,
});

test("names each lint line", () => {
  const draft = agentDraft(
    issue([
      {
        code: "ragged_row",
        line: 4,
        message: "table row has 2 cells, header has 3",
      },
      { code: "unclosed_fence", line: 0, message: "unclosed code fence" },
    ])
  );
  assert.equal(
    draft,
    [
      "Fix this section: Table rows have the wrong number of cells. Some rows have more or fewer cells than the header.",
      "",
      "What the check found:",
      "- Line 4: table row has 2 cells, header has 3",
      "- unclosed code fence",
    ].join("\n")
  );
});

test("caps long lint lists", () => {
  const lint = Array.from({ length: 11 }, (_, i) => ({
    line: i + 1,
    message: "bad row",
  }));
  const lines = agentDraft(issue(lint)).split("\n");
  assert.equal(lines.filter((l) => l.startsWith("- Line")).length, 8);
  assert.equal(lines.at(-1), "- 3 more");
});

test("works without lint", () => {
  assert.equal(
    agentDraft({ title: "This section has no type", reason: "No type fits." }),
    "Fix this section: This section has no type. No type fits."
  );
});
