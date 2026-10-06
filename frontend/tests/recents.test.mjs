import { test } from "node:test";
import assert from "node:assert/strict";
import {
  RECENTS_LIMIT,
  addRecent,
  readRecents,
  recentsKey,
  writeRecents,
} from "../src/utils/recents.js";

const project = (name, label = name) => ({ kind: "project", name, label });

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => data.set(k, v),
  };
}

test("newest first", () => {
  const list = addRecent(addRecent([], project("a")), project("b"));
  assert.deepEqual(
    list.map((r) => r.name),
    ["b", "a"]
  );
});

test("a revisit moves to the top without duplicating", () => {
  let list = [project("a"), project("b"), project("a")].reduce(addRecent, []);
  assert.deepEqual(
    list.map((r) => r.name),
    ["a", "b"]
  );
});

test("a rename replaces the stored label", () => {
  const list = addRecent(
    addRecent([], project("a", "Old")),
    project("a", "New")
  );
  assert.deepEqual(
    list.map((r) => r.label),
    ["New"]
  );
});

test("capped", () => {
  const list = Array.from({ length: RECENTS_LIMIT + 3 }, (_, i) =>
    project(`p${i}`)
  ).reduce(addRecent, []);
  assert.equal(list.length, RECENTS_LIMIT);
});

test("untitled entries are ignored", () => {
  assert.deepEqual(addRecent([], project("a", "")), []);
});

test("same name, different kind are separate entries", () => {
  const list = addRecent(addRecent([], project("x")), {
    kind: "import",
    name: "x",
    label: "x",
  });
  assert.equal(list.length, 2);
});

test("lists are per user", () => {
  const storage = memoryStorage();
  writeRecents(storage, "a@x.com", addRecent([], project("a")));
  assert.equal(readRecents(storage, "b@x.com").length, 0);
  assert.equal(readRecents(storage, "a@x.com").length, 1);
  assert.notEqual(recentsKey("a@x.com"), recentsKey("b@x.com"));
});

test("corrupt storage reads as empty", () => {
  const storage = memoryStorage();
  storage.setItem(recentsKey("a"), "{not json");
  assert.deepEqual(readRecents(storage, "a"), []);
});
