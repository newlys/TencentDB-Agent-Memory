function slugify(input) {
  return String(input).toLowerCase().replaceAll(" ", "-");
}

module.exports = { slugify };
