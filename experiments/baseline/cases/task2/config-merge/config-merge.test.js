const test = require("node:test");
const assert = require("node:assert/strict");
const { mergeConfig } = require("./config-merge");

test("deep merges plain objects", () => {
  const defaults = { server: { host: "127.0.0.1", port: 80 }, flags: { cache: true } };
  const result = mergeConfig(defaults, { server: { port: 8080 } });
  assert.deepEqual(result, { server: { host: "127.0.0.1", port: 8080 }, flags: { cache: true } });
});

test("replaces arrays and preserves explicit null", () => {
  assert.deepEqual(mergeConfig({ tags: ["a"], value: 1 }, { tags: ["b"], value: null }), { tags: ["b"], value: null });
});

test("does not mutate inputs and blocks prototype pollution", () => {
  const defaults = { nested: { keep: true } };
  const overrides = JSON.parse('{"nested":{"add":1},"__proto__":{"polluted":true}}');
  const result = mergeConfig(defaults, overrides);
  result.nested.add = 2;
  assert.deepEqual(defaults, { nested: { keep: true } });
  assert.equal({}.polluted, undefined);
  assert.equal(Object.hasOwn(result, "__proto__"), false);
});
