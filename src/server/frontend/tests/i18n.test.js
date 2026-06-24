const test = require("node:test");
const assert = require("node:assert/strict");

const ELEMENT_NODE = 1;
const TEXT_NODE = 3;

class FakeNode {
  constructor(nodeType) {
    this.nodeType = nodeType;
    this.parentElement = null;
  }
}

class FakeTextNode extends FakeNode {
  constructor(value) {
    super(TEXT_NODE);
    this.nodeValue = value;
  }
}

class FakeElement extends FakeNode {
  constructor(tagName, options = {}) {
    super(ELEMENT_NODE);
    this.tagName = String(tagName || "div").toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this._textContent = String(options.textContent || "");
    this.id = options.id || "";
    if (this.id) {
      this.attributes.set("id", this.id);
    }
    for (const [key, value] of Object.entries(options.attributes || {})) {
      this.setAttribute(key, value);
    }
  }

  appendChild(node) {
    node.parentElement = this;
    this.children.push(node);
    return node;
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "id") {
      this.id = String(value);
    }
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }

  click() {
    this.listeners.click?.();
  }

  querySelectorAll(selector) {
    if (selector !== "*") {
      return [];
    }
    const found = [];
    const visit = (node) => {
      if (!(node instanceof FakeElement)) {
        return;
      }
      found.push(node);
      node.children.forEach(visit);
    };
    this.children.forEach(visit);
    return found;
  }

  get textContent() {
    if (this.children.length) {
      return this.children.map((child) => {
        if (child instanceof FakeTextNode) {
          return child.nodeValue;
        }
        if (child instanceof FakeElement) {
          return child.textContent;
        }
        return "";
      }).join("");
    }
    return this._textContent;
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }
}

class FakeButtonElement extends FakeElement {}
class FakeInputElement extends FakeElement {
  constructor(tagName, options = {}) {
    super(tagName, options);
    this.value = String(options.value || "");
  }
}
class FakeTextAreaElement extends FakeInputElement {}

function createStorage(initial = {}) {
  const store = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
    removeItem(key) {
      store.delete(key);
    },
  };
}

function collectTextNodes(root) {
  const nodes = [];
  const visit = (node) => {
    if (node instanceof FakeTextNode) {
      nodes.push(node);
      return;
    }
    if (node instanceof FakeElement) {
      node.children.forEach(visit);
    }
  };
  visit(root);
  return nodes;
}

function createDocument(body, title = "AgentGuard Frontend Preview") {
  const documentElement = new FakeElement("html");
  documentElement.lang = "en";

  const ids = new Map();
  const register = (node) => {
    if (node instanceof FakeElement && node.id) {
      ids.set(node.id, node);
    }
    if (node instanceof FakeElement) {
      node.children.forEach(register);
    }
  };
  register(body);

  return {
    body,
    title,
    readyState: "complete",
    documentElement,
    getElementById(id) {
      return ids.get(id) || null;
    },
    addEventListener() {},
    createTreeWalker(root) {
      const nodes = collectTextNodes(root);
      let index = 0;
      return {
        nextNode() {
          const next = nodes[index] || null;
          index += 1;
          return next;
        },
      };
    },
  };
}

function loadI18n({ language = null, body, title } = {}) {
  global.Node = { TEXT_NODE, ELEMENT_NODE };
  global.NodeFilter = { SHOW_TEXT: 4 };
  global.HTMLElement = FakeElement;
  global.HTMLButtonElement = FakeButtonElement;
  global.HTMLInputElement = FakeInputElement;
  global.HTMLTextAreaElement = FakeTextAreaElement;
  global.MutationObserver = undefined;

  global.localStorage = createStorage(language ? { "agentguard.language": language } : {});
  global.document = createDocument(body, title);

  let reloadCount = 0;
  global.window = {
    localStorage: global.localStorage,
    location: {
      reload() {
        reloadCount += 1;
      },
    },
  };

  delete require.cache[require.resolve("../static/common/i18n.js")];
  require("../static/common/i18n.js");

  return {
    api: global.window.AgentGuardI18n,
    reloadCount() {
      return reloadCount;
    },
  };
}

test("i18n defaults to English and toggles to Chinese", () => {
  const body = new FakeElement("body");
  const button = new FakeButtonElement("button", { id: "sidebar-language-toggle", textContent: "中文" });
  body.appendChild(button);

  const { api, reloadCount } = loadI18n({ body });

  assert.equal(api.getLanguage(), "en");
  assert.equal(global.document.documentElement.lang, "en");
  assert.equal(button.textContent, "中文");

  button.click();

  assert.equal(global.localStorage.getItem("agentguard.language"), "zh");
  assert.equal(reloadCount(), 1);
});

test("i18n applies explicit translations and translated values in Chinese mode", () => {
  const body = new FakeElement("body");
  const button = new FakeButtonElement("button", { id: "sidebar-language-toggle", textContent: "中文" });
  const title = new FakeElement("h1", { attributes: { "data-i18n": "Home" }, textContent: "Home" });
  const refresh = new FakeButtonElement("button", { attributes: { title: "Refresh agent catalog" }, textContent: "Refresh" });
  const textarea = new FakeTextAreaElement("textarea", {
    attributes: { "data-i18n-value": "Use this field to capture the operator-facing explanation for the rule." },
    value: "Use this field to capture the operator-facing explanation for the rule.",
  });
  body.appendChild(button);
  body.appendChild(title);
  body.appendChild(refresh);
  body.appendChild(textarea);

  const { api } = loadI18n({ language: "zh", body });

  assert.equal(api.getLanguage(), "zh");
  assert.equal(global.document.documentElement.lang, "zh-CN");
  assert.equal(global.document.title, "AgentGuard 前端预览");
  assert.equal(button.textContent, "English");
  assert.equal(title.textContent, "首页");
  assert.equal(refresh.getAttribute("title"), "刷新智能体目录");
  assert.equal(textarea.value, "用这个字段记录面向运维人员的规则说明。");
});

test("i18n translates home page text nodes in Chinese mode", () => {
  const body = new FakeElement("body");
  const button = new FakeButtonElement("button", { id: "sidebar-language-toggle", textContent: "中文" });
  const pluginCardCopy = new FakeElement("p");
  pluginCardCopy.appendChild(new FakeTextNode("Enable remote or local plugins for the selected agent, including optional built-in policy and safety flows."));
  const ctaLabel = new FakeElement("span");
  ctaLabel.appendChild(new FakeTextNode("Start WITH Agent Selection"));
  body.appendChild(button);
  body.appendChild(pluginCardCopy);
  body.appendChild(ctaLabel);

  loadI18n({ language: "zh", body });

  assert.equal(pluginCardCopy.textContent, "为所选智能体启用远程或本地插件，包括可选的内置策略与安全流程。");
  assert.equal(ctaLabel.textContent, "从智能体选择开始");
});
