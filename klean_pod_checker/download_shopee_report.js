/*
 * Download the current Shopee Sell Report from the dedicated Klean&Kare
 * sub-account session. This profile is intentionally separate from the
 * other Shopee projects on the Pi.
 */
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { chromium } = require("playwright");

const profile = process.env.SHOPEE_PROFILE || "/home/milk/kleanandkare-shopee/session";
const reportsDirectory = process.env.SHOPEE_REPORT_DIRECTORY || "/home/milk/kleanandkare-shopee/sales-reports";
const workDirectory = process.env.SHOPEE_WORK_DIRECTORY || "/home/milk/kleanandkare-shopee/work-session";
const reportManifest = process.env.SHOPEE_REPORT_MANIFEST
  || path.join(reportsDirectory, "latest-report-manifest.json");
const automationStatusFile = path.join(reportsDirectory, "automation-status.json");
const requestedStartDate = (process.env.SHOPEE_REPORT_START || "").trim();
const requestedEndDate = (process.env.SHOPEE_REPORT_END || "").trim();
const downloadExistingRequestedReport = process.env.SHOPEE_DOWNLOAD_EXISTING === "1";
const REPORT_READY_TIMEOUT_MS = requestedStartDate && requestedEndDate
  ? 8 * 60_000
  : 120_000;
const REPORT_READY_POLL_MS = 5_000;
const THAI_MONTHS = [
  "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
  "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
];

function writeAutomationStatus(status, code = "") {
  try {
    fs.mkdirSync(reportsDirectory, { recursive: true });
    const temporary = `${automationStatusFile}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify({
      status,
      code,
      checked_at: new Date().toISOString(),
    }));
    fs.renameSync(temporary, automationStatusFile);
  } catch {
    // The original download result remains authoritative if the marker cannot
    // be written.
  }
}

function prepareWorkProfile() {
  if (!fs.existsSync(profile)) throw new Error(`Shopee session was not found: ${profile}`);
  fs.rmSync(workDirectory, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(workDirectory), { recursive: true });
  fs.cpSync(profile, workDirectory, {
    recursive: true,
    filter: source => !/(^|\/)(Singleton|LOCK|BrowserMetrics|Crashpad|Code Cache|GPUCache|ShaderCache)/.test(source),
  });
}

function exactDownloadButtons(page) {
  return page.locator("button:visible").filter({ hasText: /^ดาวน์โหลด$/ });
}

async function waitForModalDownloadAction(page) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const buttons = exactDownloadButtons(page);
    const count = await buttons.count();
    // The first button is the toolbar control. The second is the action inside
    // Shopee's date-range dialog, confirmed from the visible UI.
    if (count === 2) return buttons;
    await page.waitForTimeout(1_000);
  }
  throw new Error("Shopee's report request dialog did not appear");
}

function parseIsoDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) throw new Error(`Invalid report date: ${value}`);
  return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
}

async function panelForDate(page, date) {
  const panels = [
    page.locator(".eds-daterange-picker-panel__body-left"),
    page.locator(".eds-daterange-picker-panel__body-right"),
  ];
  const expectedMonth = THAI_MONTHS[date.month - 1];
  for (const panel of panels) {
    if (await panel.count() !== 1) continue;
    const header = await panel.locator(".eds-picker-header").innerText();
    if (header.includes(expectedMonth) && header.includes(String(date.year))) return panel;
  }
  throw new Error(`Requested month is not visible: ${date.year}-${date.month}`);
}

async function selectReportRange(page, startValue, endValue) {
  if (!startValue && !endValue) return;
  if (!startValue || !endValue) throw new Error("Both SHOPEE_REPORT_START and SHOPEE_REPORT_END are required");
  const start = parseIsoDate(startValue);
  const end = parseIsoDate(endValue);
  const picker = page.locator(".eds-date-picker .eds-selector");
  if (await picker.count() !== 1) throw new Error("Shopee report date picker was not found");
  await picker.click();

  let panel = await panelForDate(page, start);
  let day = panel.getByText(String(start.day), { exact: true });
  if (await day.count() !== 1) throw new Error(`Start date was not found: ${startValue}`);
  await day.click();

  panel = await panelForDate(page, end);
  day = panel.getByText(String(end.day), { exact: true });
  if (await day.count() !== 1) throw new Error(`End date was not found: ${endValue}`);
  await day.click();

  const selected = (await picker.innerText()).trim();
  const expected = `${startValue.replaceAll("-", "/")} – ${endValue.replaceAll("-", "/")}`;
  if (selected !== expected) throw new Error(`Shopee selected ${selected}, expected ${expected}`);
}

async function requestFreshReport(page) {
  const buttons = exactDownloadButtons(page);
  const count = await buttons.count();
  if (count !== 1) throw new Error(`Expected one initial download button, found ${count}`);
  await buttons.evaluate(button => button.click());
  const modalButtons = await waitForModalDownloadAction(page);
  await selectReportRange(page, requestedStartDate, requestedEndDate);
  await modalButtons.nth(1).evaluate(button => button.click());
}

async function newestCompletedReportButton(page) {
  await page.waitForTimeout(2_000);
  let buttons = exactDownloadButtons(page);
  const previousCount = await buttons.count();
  if (previousCount < 2) {
    throw new Error("Shopee has no completed report available to download");
  }

  const deadline = Date.now() + REPORT_READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await page.waitForTimeout(REPORT_READY_POLL_MS);
    buttons = exactDownloadButtons(page);
    const count = await buttons.count();
    // The report history is newest-first. A new exact Download button means
    // the report just requested above has finished generating.
    if (count > previousCount) return buttons;
  }

  if (requestedStartDate && requestedEndDate) {
    throw new Error(
      `Shopee did not finish the requested ${requestedStartDate} to ${requestedEndDate} report in time`,
    );
  }

  // A routine refresh can safely reuse the most recent completed report and
  // pick up the newly generated one on the next hourly run.
  return exactDownloadButtons(page);
}

function requestedReportFilename() {
  if (!requestedStartDate || !requestedEndDate) return "";
  return `Order.all.${requestedStartDate.replaceAll("-", "")}_${requestedEndDate.replaceAll("-", "")}.zip`;
}

async function existingRequestedReportButton(page) {
  const history = page.getByText("ประวัติการดาวน์โหลด", { exact: true });
  if (await history.count() !== 1) throw new Error("Shopee report history button was not found");
  await history.click();

  const expectedName = requestedReportFilename();
  const matchingNames = page.getByText(expectedName, { exact: true });
  await matchingNames.first().waitFor({ state: "visible", timeout: 30_000 });
  const downloadButton = matchingNames.first().locator(
    'xpath=following::button[normalize-space()="ดาวน์โหลด"][1]',
  );
  await downloadButton.waitFor({ state: "visible", timeout: 30_000 });
  return downloadButton;
}

function spreadsheetFiles(directory) {
  const found = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) found.push(...spreadsheetFiles(entryPath));
    if (entry.isFile() && /\.xlsx$/i.test(entry.name)) found.push(entryPath);
  }
  return found;
}

async function saveReportDownload(download) {
  const originalName = download.suggestedFilename();
  const stamp = Date.now();
  const archivePath = path.join(
    reportsDirectory,
    `Order.all.${stamp}.${originalName}`.replace(/[^\w.-]+/g, "_"),
  );
  await download.saveAs(archivePath);
  if (fs.statSync(archivePath).size < 1_000) {
    throw new Error("Downloaded Shopee report is unexpectedly small");
  }

  let reports;
  if (/\.zip$/i.test(originalName)) {
    const extractDirectory = fs.mkdtempSync(path.join(reportsDirectory, ".extract-"));
    try {
      execFileSync("/usr/bin/unzip", ["-qq", "-o", archivePath, "-d", extractDirectory]);
      const extracted = spreadsheetFiles(extractDirectory);
      if (!extracted.length) throw new Error("Shopee report archive does not contain an XLSX file");
      reports = extracted.map((source, index) => {
        const target = path.join(reportsDirectory, `Order.all.${stamp}.part-${index + 1}.xlsx`);
        fs.renameSync(source, target);
        return target;
      });
    } finally {
      fs.rmSync(extractDirectory, { recursive: true, force: true });
      fs.rmSync(archivePath, { force: true });
    }
  } else if (/\.xlsx$/i.test(originalName)) {
    const target = path.join(reportsDirectory, `Order.all.${stamp}.xlsx`);
    fs.renameSync(archivePath, target);
    reports = [target];
  } else {
    throw new Error(`Unsupported Shopee report format: ${originalName}`);
  }

  fs.writeFileSync(reportManifest, JSON.stringify({
    created_at: new Date().toISOString(),
    reports,
  }));
  return reports;
}

async function main() {
  prepareWorkProfile();
  fs.mkdirSync(reportsDirectory, { recursive: true });

  const context = await chromium.launchPersistentContext(workDirectory, {
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: "th-TH",
    timezoneId: "Asia/Bangkok",
    acceptDownloads: true,
  });
  const page = context.pages()[0] || await context.newPage();
  try {
    await page.goto("https://seller.shopee.co.th/portal/sale/order", { waitUntil: "domcontentloaded", timeout: 45_000 });
    await page.waitForTimeout(5_000);
    if (!/seller\.shopee\.co\.th\/portal\/sale\/order/.test(page.url())) {
      throw new Error("Shopee session expired; sign in again through the dedicated Klean&Kare browser");
    }

    let reportButton;
    if (downloadExistingRequestedReport) {
      if (!requestedStartDate || !requestedEndDate) {
        throw new Error("Existing report download requires SHOPEE_REPORT_START and SHOPEE_REPORT_END");
      }
      reportButton = await existingRequestedReportButton(page);
    } else {
      await requestFreshReport(page);
      const reportButtons = await newestCompletedReportButton(page);
      const availableCount = await reportButtons.count();
      if (availableCount < 2) throw new Error("Shopee has no completed report available to download");
      // The toolbar button is first; Shopee orders report-history rows newest-first.
      reportButton = reportButtons.nth(1);
    }

    const downloadPromise = page.waitForEvent("download", { timeout: 60_000 });
    // Shopee now requires a trusted user gesture before it starts the file
    // download. Calling HTMLElement.click() through evaluate() creates an
    // untrusted DOM event and leaves the bot waiting until timeout.
    await reportButton.click();
    const reports = await saveReportDownload(await downloadPromise);
    console.log(JSON.stringify({ reports, parts: reports.length }));
  } finally {
    await context.close();
    fs.rmSync(workDirectory, { recursive: true, force: true });
  }
}

main()
  .then(() => {
    writeAutomationStatus("ok");
  })
  .catch(error => {
    const message = String(error?.message || error || "");
    writeAutomationStatus(
      "error",
      /session expired/i.test(message) ? "session_expired" : "download_failed",
    );
    console.error(error.stack || error);
    process.exit(1);
  });
