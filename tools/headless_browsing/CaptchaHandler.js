const fs = require("fs");
const path = require("path");
const readline = require("readline");
const StealthBrowser = require("./StealthBrowser");

class CaptchaHandler {
  /**
   * Common captcha signature selector list
   */
  static captchaSelectors = [
    'iframe[src*="challenges.cloudflare.com"]', // Cloudflare Turnstile / 5-second shield
    'iframe[src*="recaptcha"]', // Google reCAPTCHA
    'iframe[src*="hcaptcha"]', // hCaptcha
    ".geetest_holder", // Geetest slider
    "#captcha",
    "#g-recaptcha",
  ];

  /**
   * 1. Automatically detect whether the current page has a human verification challenge
   */
  static async detectCaptcha(page) {
    for (const selector of this.captchaSelectors) {
      const element = await page.$(selector);
      if (element && (await element.isVisible())) {
        return { hasCaptcha: true, detectedType: selector };
      }
    }

    // Extra check on the Cloudflare page title
    const title = await page.title();
    if (title.includes("Just a moment") || title.includes("Attention Required")) {
      return { hasCaptcha: true, detectedType: "Cloudflare Title Challenge" };
    }

    return { hasCaptcha: false, detectedType: null };
  }

  /**
   * 2. When a captcha appears, trigger the interactive fallback flow
   * @param {Page} page the current headless Page
   * @param {BrowserContext} context the current Context
   * @param {string} statePath the save path of the associated Session
   */
  static async handleInteractiveResolve(page, context, statePath) {
    const currentUrl = page.url();
    console.warn(`\n⚠️  [CaptchaHandler] 在页面检测到人机验证: ${currentUrl}`);
    console.log("📸 正在保存现场截图: captcha-alert.png");
    await page.screenshot({ path: "captcha-alert.png", fullPage: true });

    // Save the current progress state
    const tempStatePath = statePath || "./sessions/temp-captcha-state.json";
    await StealthBrowser.saveState(context, tempStatePath);

    console.log("🚨 正在启动可见浏览器窗口以供人工协助过验...");

    // Launch headed mode (headless: false) and load the current Session
    const { browser: interactiveBrowser, context: interactiveContext } = await StealthBrowser.createStealthContext({
      headless: false,
      statePath: tempStatePath,
    });

    const interactivePage = await interactiveContext.newPage();
    await interactivePage.goto(currentUrl, { waitUntil: "domcontentloaded" });

    console.log("\n==================================================");
    console.log("👉 请在刚弹出的浏览器窗口中完成验证码点击或滑动");
    console.log("👉 完成验证后，返回当前终端按下 [Enter] 回车键继续任务");
    console.log("==================================================\n");

    // Block the terminal until Enter is pressed
    await this.promptUserKey();

    // Save the latest credentials after passing the check
    if (statePath) {
      await StealthBrowser.saveState(interactiveContext, statePath);
      console.log(`✅ [CaptchaHandler] 新的 Cookie 凭证已同步更新至: ${statePath}`);
    }

    await interactiveBrowser.close();
    console.log("🔄 人工协助完成，无头浏览器恢复后台静默任务运行...\n");

    // Reload the headless page so it inherits the passed state
    await page.reload({ waitUntil: "networkidle" });
  }

  /**
   * Helper: wait for a keypress in the terminal
   */
  static promptUserKey() {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    return new Promise((resolve) => {
      rl.question("按下 Enter 回车键确认已过验: ", () => {
        rl.close();
        resolve();
      });
    });
  }
}

module.exports = CaptchaHandler;
