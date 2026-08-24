class LruCache {
  constructor(capacity) {
    this.capacity = capacity;
    this.values = new Map();
  }

  get(key) {
    return this.values.get(key);
  }

  set(key, value) {
    this.values.set(key, value);
    if (this.values.size > this.capacity) this.values.delete(key);
    return this;
  }

  has(key) {
    return this.values.has(key);
  }
}

module.exports = { LruCache };
