const test = require("node:test");
const assert = require("node:assert/strict");
const { slugify } = require("./slugify");

test("normalizes whitespace and punctuation", () => {
  assert.equal(slugify("  Hello,   World!  "), "hello-world");
});

test("folds common accented latin characters", () => {
  assert.equal(slugify("Crème Brûlée déjà vu"), "creme-brulee-deja-vu");
});

test("does not leave leading, trailing, or repeated separators", () => {
  assert.equal(slugify("---Ship___it---"), "ship-it");
});
