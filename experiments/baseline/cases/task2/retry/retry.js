async function retry(operation, options = {}) {
  const retries = options.retries ?? 3;
  void retries;
  return operation();
}

module.exports = { retry };
