const OUTPUT_HEADERS = ["สถานะล่าสุด", "ตรวจเมื่อ", "รูปหลักฐาน"];
const MAPPING_SHEET_NAME = "Mapping Order";
const MAPPING_ORDER_COLUMN = 1;
const MAPPING_TRACKING_COLUMN = 2;

function doPost(event) {
  try {
    const payload = JSON.parse(event.postData.contents || "{}");
    const expectedSecret = PropertiesService.getScriptProperties().getProperty("WEBHOOK_SECRET");
    if (!expectedSecret || payload.secret !== expectedSecret) {
      return jsonResponse({ ok: false, error: "unauthorized" });
    }

    const spreadsheet = SpreadsheetApp.openById(String(payload.sheet_id || ""));
    if (String(payload.action || "") === "replace_mapping_order") {
      const updated = replaceMappingOrder(spreadsheet, payload);
      return jsonResponse({
        ok: true,
        action: "replace_mapping_order",
        mapping_updated: updated,
      });
    }
    const sheetId = Number(payload.sheet_gid);
    const sheet = spreadsheet.getSheets().find((item) => item.getSheetId() === sheetId);
    if (!sheet) {
      return jsonResponse({ ok: false, error: "sheet_not_found" });
    }

    const columns = ensureHeaders(sheet);
    const updates = Array.isArray(payload.updates) ? payload.updates : [];
    return jsonResponse({ ok: true, updated: applyUpdates(sheet, columns, updates) });
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error && error.message ? error.message : error) });
  }
}

function replaceMappingOrder(spreadsheet, payload) {
  const requestedName = String(payload.sheet_name || MAPPING_SHEET_NAME);
  if (requestedName !== MAPPING_SHEET_NAME) {
    throw new Error("invalid_mapping_sheet");
  }
  const sheet = spreadsheet.getSheetByName(MAPPING_SHEET_NAME);
  if (!sheet) {
    throw new Error("mapping_sheet_not_found");
  }
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (rows.length > 50000) {
    throw new Error("mapping_rows_too_large");
  }
  const cleanRows = rows
    .filter((item) => Array.isArray(item) && item.length >= 2)
    .map((item) => [String(item[0] || "").trim(), String(item[1] || "").trim()])
    .filter((item) => item[0] && item[1]);

  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const requiredRows = Math.max(2, cleanRows.length + 1);
    if (sheet.getMaxRows() < requiredRows) {
      sheet.insertRowsAfter(sheet.getMaxRows(), requiredRows - sheet.getMaxRows());
    }
    sheet.getRange(1, MAPPING_ORDER_COLUMN).setValue("หมายเลขคำสั่งซื้อ");
    sheet.getRange(1, MAPPING_TRACKING_COLUMN).setValue("หมายเลขติดตามพัสดุ");
    const existingRows = Math.max(0, sheet.getMaxRows() - 1);
    if (existingRows) {
      sheet.getRange(2, MAPPING_ORDER_COLUMN, existingRows, 1).clearContent();
      sheet.getRange(2, MAPPING_TRACKING_COLUMN, existingRows, 1).clearContent();
    }
    if (cleanRows.length) {
      sheet
        .getRange(2, MAPPING_ORDER_COLUMN, cleanRows.length, 1)
        .setNumberFormat("@")
        .setValues(cleanRows.map((item) => [item[0]]));
      sheet
        .getRange(2, MAPPING_TRACKING_COLUMN, cleanRows.length, 1)
        .setNumberFormat("@")
        .setValues(cleanRows.map((item) => [item[1]]));
    }
    const extraRows = sheet.getMaxRows() - requiredRows;
    if (extraRows > 0) {
      sheet.deleteRows(requiredRows + 1, extraRows);
    }
    SpreadsheetApp.flush();
    return cleanRows.length;
  } finally {
    lock.releaseLock();
  }
}

function ensureHeaders(sheet) {
  const lastColumn = Math.max(1, sheet.getLastColumn());
  const currentHeaders = sheet.getRange(1, 1, 1, lastColumn).getDisplayValues()[0];
  const found = OUTPUT_HEADERS.map((header) => currentHeaders.indexOf(header) + 1);

  // Reuse headers when columns were moved (for example, after deleting N and O).
  if (found.every((column) => column > 0)) {
    return { status: found[0], checked: found[1], proof: found[2] };
  }

  // On a fresh sheet append after the existing data, with N as the earliest
  // output column. Copy the formatting from the column immediately before it.
  const startColumn = Math.max(14, lastColumn + 1);
  const headers = sheet.getRange(1, startColumn, 1, OUTPUT_HEADERS.length);
  sheet
    .getRange(1, Math.max(1, startColumn - 1), 1, 1)
    .copyTo(headers, SpreadsheetApp.CopyPasteType.PASTE_FORMAT, false);
  headers.setValues([OUTPUT_HEADERS]);
  sheet.setColumnWidth(startColumn, 180);
  sheet.setColumnWidth(startColumn + 1, 145);
  sheet.setColumnWidth(startColumn + 2, 120);
  return { status: startColumn, checked: startColumn + 1, proof: startColumn + 2 };
}

function applyUpdates(sheet, columns, updates) {
  const validUpdates = updates
    .map((item) => ({ item: item || {}, row: Number(item && item.row) }))
    .filter(({ row }) => Number.isInteger(row) && row >= 2 && row <= sheet.getMaxRows());
  if (!validUpdates.length) return 0;

  // Read and write each destination column in one batch. This keeps a full
  // scheduled update comfortably within the webhook timeout.
  const finalRow = Math.max(...validUpdates.map(({ row }) => row));
  const count = finalRow - 1;
  const trackingValues = sheet.getRange(2, 7, count, 1).getDisplayValues();
  const statusRange = sheet.getRange(2, columns.status, count, 1);
  const checkedRange = sheet.getRange(2, columns.checked, count, 1);
  const proofRange = sheet.getRange(2, columns.proof, count, 1);
  const statusValues = statusRange.getValues();
  const checkedValues = checkedRange.getValues();
  const proofFormulas = proofRange.getFormulas();

  let updated = 0;
  validUpdates.forEach(({ item, row }) => {
    const index = row - 2;
    const tracking = String(trackingValues[index][0] || "").toUpperCase();
    const orderNumber = String(item.order_number || "").toUpperCase();
    if (!orderNumber || !tracking.includes(orderNumber)) return;

    statusValues[index][0] = String(item.status || "");
    checkedValues[index][0] = item.checked_at ? new Date(item.checked_at) : new Date();
    proofFormulas[index][0] = buildProofFormula(item.proof_urls);
    updated += 1;
  });

  statusRange.setValues(statusValues);
  checkedRange.setValues(checkedValues).setNumberFormat("dd/MM/yyyy HH:mm");
  proofRange.setFormulas(proofFormulas).setWrap(true);
  SpreadsheetApp.flush();
  return updated;
}

function buildProofFormula(values) {
  const urls = Array.isArray(values)
    ? [...new Set(values.map(String).filter((value) => /^https:\/\//i.test(value)))]
    : [];
  if (!urls.length) return '="-"';
  // A Google Sheets cell can render one image. Use the first proof as its
  // thumbnail and make it clickable; the CS dashboard retains every proof.
  const url = urls[0].replace(/"/g, '""');
  return `=HYPERLINK("${url}",IMAGE("${url}"))`;
}

function jsonResponse(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
