const test = require("node:test");
const assert = require("node:assert/strict");
const { LruCache } = require("./lru-cache");

test("evicts the least recently used item", () => {
  const cache = new LruCache(2);
  cache.set("a", 1).set("b", 2);
  assert.equal(cache.get("a"), 1);
  cache.set("c", 3);
  assert.equal(cache.has("b"), false);
  assert.equal(cache.get("a"), 1);
  assert.equal(cache.get("c"), 3);
});

test("updating an item promotes it without changing size", () => {
  const cache = new LruCache(2);
  cache.set("a", 1).set("b", 2).set("a", 10).set("c", 3);
  assert.equal(cache.has("b"), false);
  assert.equal(cache.get("a"), 10);
});

test("rejects invalid capacities", () => {
  assert.throws(() => new LruCache(0), RangeError);
  assert.throws(() => new LruCache(1.5), RangeError);
});
