const test = require("node:test");
const assert = require("node:assert/strict");
const { parseDuration } = require("./parse-duration");

test("parses and sums days, hours, minutes, seconds, and milliseconds", () => {
  assert.equal(parseDuration("1d 2h 30m 4s 250ms"), 95_404_250);
});

test("accepts compact input and decimals", () => {
  assert.equal(parseDuration("1.5h30m"), 7_200_000);
  assert.equal(parseDuration("250ms"), 250);
});

test("rejects empty, negative, unknown, and trailing input", () => {
  for (const value of ["", "-1s", "2 weeks", "1h nope", "1m 20"]) {
    assert.throws(() => parseDuration(value), TypeError);
  }
});
