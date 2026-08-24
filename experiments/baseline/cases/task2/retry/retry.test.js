const test = require("node:test");
const assert = require("node:assert/strict");
const { retry } = require("./retry");

test("retries failures and returns the eventual value", async () => {
  let attempts = 0;
  const value = await retry(async () => {
    attempts += 1;
    if (attempts < 3) throw new Error("temporary");
    return "ok";
  }, { retries: 3, delayMs: 0 });
  assert.equal(value, "ok");
  assert.equal(attempts, 3);
});

test("rethrows after the configured retry count", async () => {
  let attempts = 0;
  await assert.rejects(() => retry(async () => {
    attempts += 1;
    throw new TypeError("permanent");
  }, { retries: 2, delayMs: 0 }), { name: "TypeError", message: "permanent" });
  assert.equal(attempts, 3);
});

test("calls onRetry with error and one-based retry number", async () => {
  const seen = [];
  let attempts = 0;
  await retry(async () => {
    attempts += 1;
    if (attempts === 1) throw new Error("again");
    return true;
  }, { retries: 1, delayMs: 0, onRetry: (error, retryNumber) => seen.push([error.message, retryNumber]) });
  assert.deepEqual(seen, [["again", 1]]);
});
