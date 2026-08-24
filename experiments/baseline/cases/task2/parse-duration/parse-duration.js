function parseDuration(value) {
  const match = /^(\d+)s$/.exec(String(value));
  return match ? Number(match[1]) * 1000 : 0;
}

module.exports = { parseDuration };
