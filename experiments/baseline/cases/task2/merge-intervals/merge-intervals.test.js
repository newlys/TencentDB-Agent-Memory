const test = require("node:test");
const assert = require("node:assert/strict");
const { mergeIntervals } = require("./merge-intervals");

test("merges overlaps and touching intervals", () => {
  assert.deepEqual(mergeIntervals([[1, 3], [2, 6], [8, 10], [10, 12]]), [[1, 6], [8, 12]]);
});

test("sorts an unsorted input without mutating it", () => {
  const input = [[9, 11], [1, 2], [3, 5]];
  assert.deepEqual(mergeIntervals(input), [[1, 2], [3, 5], [9, 11]]);
  assert.deepEqual(input, [[9, 11], [1, 2], [3, 5]]);
});

test("validates interval shape and ordering", () => {
  assert.throws(() => mergeIntervals([[3, 1]]), RangeError);
  assert.throws(() => mergeIntervals([[1]]), TypeError);
});
