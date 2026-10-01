import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execute = promisify(execFile);
const root = fileURLToPath(new URL(".", import.meta.url));

export default function configureEleventy(eleventyConfig) {
  eleventyConfig.setNunjucksEnvironmentOptions({
    autoescape: true,
    throwOnUndefined: true,
  });

  eleventyConfig.addPassthroughCopy({ "docs/src/assets": "assets" });
  for (const target of ["src/**/*.rs", "Cargo.toml", "Cargo.lock"]) {
    eleventyConfig.addWatchTarget(target);
  }

  // Refresh metadata on watch rebuilds as well as the initial build.
  let capabilities;
  eleventyConfig.on("eleventy.before", async () => {
    const { stdout } = await execute(`${root}scripts/cargo`, ["run", "--quiet", "--locked", "--", "capabilities"], {
      cwd: root,
      maxBuffer: 4 * 1024 * 1024,
    });
    capabilities = JSON.parse(stdout);
  });
  eleventyConfig.addGlobalData("capabilities", () => capabilities);

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
