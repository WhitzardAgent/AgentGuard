"use strict";

function toOutputPath(pagePath) {
  return pagePath
    .replace(/(^|\/)README\.md$/, "$1index.html")
    .replace(/\.md$/, ".html");
}

function buildLanguageSwitcher(lang, pagePath) {
  const outputPath = toOutputPath(pagePath);
  const prefix = "../".repeat(pagePath.split("/").length);
  const zhHref = `${prefix}zh/${outputPath}`;
  const enHref = `${prefix}en/${outputPath}`;
  const zhActive = lang === "zh";
  const enActive = lang === "en";

  return [
    '<div class="doc-lang-switcher" aria-label="Language switcher">',
    `  <a class="doc-lang-link${zhActive ? " is-active" : ""}"${zhActive ? ' aria-current="page"' : ""} href="${zhHref}">中文</a>`,
    '  <span class="doc-lang-separator" aria-hidden="true">|</span>',
    `  <a class="doc-lang-link${enActive ? " is-active" : ""}"${enActive ? ' aria-current="page"' : ""} href="${enHref}">EN</a>`,
    "</div>",
  ].join("\n");
}

module.exports = {
  hooks: {
    page(page) {
      if (!this.isLanguageBook()) {
        return page;
      }

      const configuredLanguage = this.config.get("language");
      const lang = configuredLanguage && configuredLanguage.startsWith("zh") ? "zh" : configuredLanguage;
      if (!lang || !page.path) {
        return page;
      }

      page.content = `${buildLanguageSwitcher(lang, page.path)}\n${page.content}`;
      return page;
    },
  },
};
