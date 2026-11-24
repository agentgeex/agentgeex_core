const { chromium } = require("playwright-extra");
const stealth = require("puppeteer-extra-plugin-stealth")();

// Enable the Stealth plugin
chromium.use(stealth);

class StealthBrowser {
  /**
   * Launch a Context with a fully realistic fingerprint
   */
  static async createStealthContext(options = {}) {
    // Pass the proxy config through (e.g. { server: "http://127.0.0.1:7890" })
    const launchOpts = {
      // Prefer a locally installed real Chrome build; the cleanest fingerprint
      channel: options.channel || "chrome",
      headless: options.headless !== undefined ? options.headless : true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled", // core: disable Chrome's automation-controlled flag
        "--disable-infobars",
        "--window-size=1920,1080",
      ],
    };
    if (options.proxy) {
      launchOpts.proxy = options.proxy;
    }
    const browser = await chromium.launch(launchOpts);

    const contextOpts = {
      viewport: { width: 1920, height: 1080 },
      userAgent: options.userAgent || "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      locale: "zh-CN",
      timezoneId: "Asia/Shanghai",
      deviceScaleFactor: 1,
    };
    // Optional: load existing session state (cookies)
    if (options.statePath) {
      try {
        contextOpts.storageState = options.statePath;
        console.log(`🔑 [StealthBrowser] 已加载会话状态: ${options.statePath}`);
      } catch (e) {
        console.warn(`⚠️ [StealthBrowser] 会话状态加载失败(忽略): ${e.message}`);
      }
    }
    const context = await browser.newContext(contextOpts);

    // Note: no need to write addInitScript calls here to inject fake variables,
    // puppeteer-extra-plugin-stealth applies the correct standards-compliant low-level patches automatically.

    return { browser, context };
  }

  /**
   * Helper: random delay
   */
  static async sleep(minMs = 1000, maxMs = 3000) {
    const delay = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
    await new Promise((resolve) => setTimeout(resolve, delay));
  }

  /**
   * Save the current Context's storage state (cookies/localStorage) to a file
   * so CaptchaHandler can reuse the credentials after a manual pass
   */
  static async saveState(context, statePath) {
    const fs = require("fs");
    const path = require("path");
    const dir = path.dirname(statePath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    await context.storageState({ path: statePath });
    console.log(`💾 [StealthBrowser] 存储状态已保存: ${statePath}`);
  }
}

module.exports = StealthBrowser;
