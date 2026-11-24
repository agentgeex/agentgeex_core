const StealthBrowser = require("./StealthBrowser");

(async () => {
  console.log("正在启动拟人化无头浏览器...");
  const { browser, context } = await StealthBrowser.createStealthContext({ headless: true });
  const page = await context.newPage();

  try {
    console.log("正在访问 Bot 检测服务 (bot.sannysoft.com)...");
    await page.goto("https://bot.sannysoft.com", { waitUntil: "networkidle" });

    // Wait a random 2-3 seconds, mimicking a human pausing to look
    await StealthBrowser.sleep(2000, 3000);

    // Screenshot and save the detection result
    await page.screenshot({ path: "stealth-result.png", fullPage: true });
    console.log("检测完成！结果截图已保存至: stealth-result.png");
  } catch (err) {
    console.error("测试运行异常:", err.message);
  } finally {
    await browser.close();
  }
})();
