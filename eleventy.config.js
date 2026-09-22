export default function configureEleventy(eleventyConfig) {
  eleventyConfig.setNunjucksEnvironmentOptions({
    autoescape: true,
    throwOnUndefined: true,
  });

  eleventyConfig.addPassthroughCopy({ "docs/src/assets": "assets" });
  eleventyConfig.addWatchTarget("src/wifimap/");
  eleventyConfig.addWatchTarget("pyproject.toml");
  eleventyConfig.addWatchTarget("docs/extract_capabilities.py");

  return {
    dir: {
      input: "docs/src",
      output: "docs/_site",
      includes: "_includes",
      data: "_data",
    },
    htmlTemplateEngine: "njk",
    markdownTemplateEngine: "njk",
    templateFormats: ["md", "njk"],
  };
}
