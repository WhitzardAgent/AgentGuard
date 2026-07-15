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

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === "id") {
      this.id = "";
    }
  }

  remove() {
    if (!this.parentElement) {
      return;
    }
    this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
    this.parentElement = null;
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

function createDocument(body, title = "AgentGuard Frontend Preview", options = {}) {
  const documentElement = new FakeElement("html");
  const head = new FakeElement("head");
  documentElement.lang = "en";
  if (options.serverLanguage) {
    documentElement.setAttribute("data-agentguard-server-language", options.serverLanguage);
    documentElement.lang = options.serverLanguage === "zh" ? "zh-CN" : "en";
  }
  documentElement.appendChild(head);
  documentElement.appendChild(body);

  const listeners = {};
  const findById = (node, id) => {
    if (!(node instanceof FakeElement)) {
      return null;
    }
    if (node.id === id) {
      return node;
    }
    for (const child of node.children) {
      const found = findById(child, id);
      if (found) {
        return found;
      }
    }
    return null;
  };

  let cookieValue = String(options.cookie || "");

  return {
    body,
    head,
    title,
    readyState: options.readyState || "complete",
    documentElement,
    get cookie() {
      return cookieValue;
    },
    set cookie(value) {
      cookieValue = String(value || "");
    },
    getElementById(id) {
      return findById(documentElement, id);
    },
    addEventListener(name, handler) {
      listeners[name] = handler;
    },
    createElement(tagName) {
      return new FakeElement(tagName);
    },
    dispatch(name) {
      listeners[name]?.();
    },
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

function loadI18n({ language = null, body, title, storage = null, readyState = "complete", cookie = "", serverLanguage = "" } = {}) {
  global.Node = { TEXT_NODE, ELEMENT_NODE };
  global.NodeFilter = { SHOW_TEXT: 4 };
  global.HTMLElement = FakeElement;
  global.HTMLButtonElement = FakeButtonElement;
  global.HTMLInputElement = FakeInputElement;
  global.HTMLTextAreaElement = FakeTextAreaElement;
  global.MutationObserver = undefined;

  global.localStorage = storage || createStorage(language ? { "agentguard.language": language } : {});
  global.document = createDocument(body, title, { readyState, cookie, serverLanguage });

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
    dispatchDOMContentLoaded() {
      global.document.dispatch("DOMContentLoaded");
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


test("i18n reads the cookie language before localStorage", () => {
  const body = new FakeElement("body");
  const button = new FakeButtonElement("button", { id: "sidebar-language-toggle", textContent: "中文" });
  body.appendChild(button);

  const storage = createStorage({ "agentguard.language": "en" });
  const { api } = loadI18n({ body, storage, cookie: "agentguard.language=zh" });

  assert.equal(api.getLanguage(), "zh");
  assert.equal(global.document.documentElement.lang, "zh-CN");
  assert.equal(button.textContent, "English");
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

test("i18n translates auth text and interpolates countdowns", () => {
  const body = new FakeElement("body");
  const button = new FakeButtonElement("button", { id: "locale-toggle-button", textContent: "中文" });
  const title = new FakeElement("h1");
  const toggle = new FakeButtonElement("button");
  title.appendChild(new FakeTextNode("Sign in to AgentGuard"));
  toggle.appendChild(new FakeTextNode("Create an account"));
  body.appendChild(button);
  body.appendChild(title);
  body.appendChild(toggle);

  const { api } = loadI18n({ language: "zh", body, title: "Sign In - AgentGuard" });

  assert.equal(global.document.title, "登录 - AgentGuard");
  assert.equal(title.textContent, "登录 AgentGuard");
  assert.equal(toggle.textContent, "创建账号");
  assert.equal(api.t("Confirm Password"), "确认密码");
  assert.equal(api.t("Show"), "显示");
  assert.equal(api.t("Hide"), "隐藏");
  assert.equal(api.t("Passwords do not match."), "两次输入的密码不一致。");
  assert.equal(api.t("Code sent. Try again in {seconds} seconds.", { seconds: 30 }), "验证码已发送。请在 30 秒后重试。");
});


test("language chosen on login persists to later pages", () => {
  const loginBody = new FakeElement("body");
  const loginToggle = new FakeButtonElement("button", { id: "locale-toggle-button", textContent: "中文" });
  loginBody.appendChild(loginToggle);

  const sharedStorage = createStorage();
  const { reloadCount } = loadI18n({ body: loginBody, storage: sharedStorage });
  loginToggle.click();

  assert.equal(sharedStorage.getItem("agentguard.language"), "zh");
  assert.equal(reloadCount(), 1);

  const appBody = new FakeElement("body");
  const appToggle = new FakeButtonElement("button", { id: "sidebar-language-toggle", textContent: "中文" });
  const homeTitle = new FakeElement("h1");
  homeTitle.appendChild(new FakeTextNode("Home"));
  appBody.appendChild(appToggle);
  appBody.appendChild(homeTitle);

  loadI18n({ body: appBody, storage: sharedStorage, title: "AgentGuard Frontend Preview" });

  assert.match(global.document.cookie, /agentguard\.language=zh/);
  assert.equal(global.document.documentElement.lang, "zh-CN");
  assert.equal(global.document.title, "AgentGuard 前端预览");
  assert.equal(homeTitle.textContent, "首页");
});


test("i18n hides Chinese pages until DOMContentLoaded translations finish", () => {
  const body = new FakeElement("body");
  const button = new FakeButtonElement("button", { id: "sidebar-language-toggle", textContent: "中文" });
  const title = new FakeElement("h1");
  title.appendChild(new FakeTextNode("Home"));
  body.appendChild(button);
  body.appendChild(title);

  const { dispatchDOMContentLoaded } = loadI18n({ language: "zh", body, readyState: "loading" });

  assert.equal(global.document.documentElement.getAttribute("data-agentguard-i18n-pending"), "zh");
  assert.equal(global.document.getElementById("agentguard-i18n-boot-style")?.textContent, 'html[data-agentguard-i18n-pending="zh"] body { visibility: hidden; }');

  dispatchDOMContentLoaded();

  assert.equal(global.document.documentElement.getAttribute("data-agentguard-i18n-pending"), null);
  assert.equal(global.document.getElementById("agentguard-i18n-boot-style"), null);
  assert.equal(title.textContent, "首页");
  assert.equal(button.textContent, "English");
});


test("i18n skips boot hiding when the server already rendered Chinese", () => {
  const body = new FakeElement("body");
  const button = new FakeButtonElement("button", { id: "sidebar-language-toggle", textContent: "English" });
  const title = new FakeElement("h1");
  title.appendChild(new FakeTextNode("首页"));
  body.appendChild(button);
  body.appendChild(title);

  loadI18n({ body, cookie: "agentguard.language=zh", serverLanguage: "zh", readyState: "loading", title: "AgentGuard 前端预览" });

  assert.equal(global.document.documentElement.getAttribute("data-agentguard-i18n-pending"), null);
  assert.equal(global.document.getElementById("agentguard-i18n-boot-style"), null);
  assert.equal(title.textContent, "首页");
  assert.equal(button.textContent, "English");
});
