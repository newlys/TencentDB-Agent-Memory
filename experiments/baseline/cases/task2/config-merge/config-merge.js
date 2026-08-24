function mergeConfig(defaults, overrides) {
  return { ...defaults, ...overrides };
}

module.exports = { mergeConfig };
