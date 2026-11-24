const StealthBrowser = require("./StealthBrowser");
const CaptchaHandler = require("./CaptchaHandler");

(async () => {
  const sessionPath = "./sessions/github-session.json";

  // 1. Start headless in the background
  const { browser, context } = await StealthBrowser.createStealthContext({
    headless: true,
    statePath: sessionPath,
  });
  const page = await context.newPage();

  console.log("正在执行后台无头抓取/发布任务...");
  await page.goto("https://example.com/protected-page", { waitUntil: "domcontentloaded" });

  // 2. Modular call: one-shot detection with automatic intervention
  const checkResult = await CaptchaHandler.detectCaptcha(page);
  if (checkResult.hasCaptcha) {
    console.log(`匹配到特征: ${checkResult.detectedType}`);
    // Trigger the interactive fallback
    await CaptchaHandler.handleInteractiveResolve(page, context, sessionPath);
  }

  // 3. Continue with the core business logic
  console.log("🎉 业务逻辑执行完毕，当前页面标题:", await page.title());

  await browser.close();
})();
