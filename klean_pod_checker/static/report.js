const rawData = document.querySelector("#report-data")?.textContent || "[]";
const reportItems = JSON.parse(rawData);
const results = new Map(reportItems.map((item) => [item.order_number, item]));
const rows = [...document.querySelectorAll("#report-table tbody tr[data-order]")];
const rowByOrder = new Map(rows.map((row) => [row.dataset.order, row]));
const dialog = document.querySelector("#order-dialog");
const searchInput = document.querySelector("#report-search");
const filterButtons = [...document.querySelectorAll("[data-filter]")];
const carrierButtons = [...document.querySelectorAll("button[data-carrier]")];
const exportButton = document.querySelector("#export-visible");
let activeFilter = "actionable";
let activeCarrier = "all";
let activeOrder = "";

function bucketFor(result) {
  if (result.error) return "error";
  if (!result.found) return "missing";
  if (result.delivered) return "delivered";
  if (["E", "P", "401"].includes(result.status_code)) return "rejected";
  return "pending";
}

function statusText(result) {
  if (result.error) return "ตรวจสอบผิดพลาด";
  if (!result.found) return "ไม่พบในระบบขนส่ง";
  const label = result.status_th || result.status_en || "ไม่ทราบสถานะ";
  return result.status_code ? `${label} (${result.status_code})` : label;
}

function statusClass(bucket) {
  if (bucket === "delivered") return "ok";
  if (bucket === "pending") return "wait";
  if (bucket === "missing") return "missing";
  return "bad";
}

function isActionable(result) {
  return !result.delivered;
}

function isStale(result) {
  if (result.delivered || !result.checked_at) return false;
  const checked = Date.parse(result.checked_at);
  return Number.isFinite(checked) && Date.now() - checked > 24 * 60 * 60 * 1000;
}

function ageText(value) {
  const checked = Date.parse(value || "");
  if (!Number.isFinite(checked)) return "ยังไม่ทราบเวลาตรวจ";
  const minutes = Math.max(0, Math.floor((Date.now() - checked) / 60000));
  if (minutes < 1) return "ตรวจเมื่อสักครู่";
  if (minutes < 60) return `ตรวจ ${minutes} นาทีที่แล้ว`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `ตรวจ ${hours} ชั่วโมงที่แล้ว`;
  return `ตรวจ ${Math.floor(hours / 24)} วันที่แล้ว`;
}

function formatDate(value) {
  const parsed = Date.parse(value || "");
  if (!Number.isFinite(parsed)) return value || "—";
  return new Intl.DateTimeFormat("th-TH", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(parsed));
}

function safeProofUrl(value) {
  try {
    const url = new URL(value, window.location.origin);
    const localKex = url.origin === window.location.origin && url.pathname.startsWith("/proof/kex/");
    const skyfrog = url.protocol === "https:" && ["skyfrog.net", "www.skyfrog.net"].includes(url.hostname);
    if (!localKex && !skyfrog) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}

function proofEntries(result) {
  const safeUrls = [...new Set((result.proof_urls || []).map(safeProofUrl).filter(Boolean))];
  const needsSignatureFilter = result.carrier === "skyfrog" && !result.proof_urls_filtered;
  const urls = needsSignatureFilter ? safeUrls.slice(2) : safeUrls;
  const firstNumber = Number(result.proof_first_number)
    || (needsSignatureFilter ? 3 : 1);
  return urls.map((url, index) => ({ url, number: firstNumber + index }));
}

function buildProofLink(url, number, large = false) {
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.className = large ? "dialog-proof" : "proof-thumb";
  link.title = `เปิดรูปหลักฐาน ${number}`;
  const image = document.createElement("img");
  image.src = url;
  image.alt = `รูปหลักฐาน ${number}`;
  image.loading = "lazy";
  image.decoding = "async";
  const label = document.createElement("span");
  label.textContent = large ? `เปิดรูป ${number}` : String(number);
  link.append(image, label);
  return link;
}

function renderTableProofs(cell, result) {
  const proofs = proofEntries(result);
  cell.replaceChildren();
  if (!proofs.length) {
    cell.textContent = "—";
    return;
  }
  const list = document.createElement("div");
  list.className = "proof-list";
  proofs.forEach((proof) => list.append(buildProofLink(proof.url, proof.number)));
  cell.append(list);
}

function updateAge(row, result) {
  const age = row.querySelector(".age");
  age.textContent = ageText(result.checked_at);
  age.classList.toggle("stale", isStale(result));
}

function updateRow(result) {
  const row = rowByOrder.get(result.order_number);
  if (!row) return;
  const bucket = bucketFor(result);
  row.dataset.bucket = bucket;
  row.dataset.rowCarrier = result.carrier || row.dataset.rowCarrier || "";
  row.dataset.checked = result.checked_at || "";
  row.dataset.search = [result.order_number, result.driver, result.group_name]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("th");
  row.className = `row-${bucket}`;
  const status = row.querySelector(".row-status");
  status.className = `row-status ${statusClass(bucket)}`;
  status.replaceChildren(document.createTextNode(statusText(result)));
  const age = document.createElement("span");
  age.className = "age";
  status.append(age);
  row.querySelector(".row-driver").textContent = result.driver || "—";
  row.querySelector(".row-delivery").textContent = result.delivery_at || "—";
  renderTableProofs(row.querySelector(".proofs"), result);
  updateAge(row, result);
}

function matchesFilter(result, filter) {
  const bucket = bucketFor(result);
  if (filter === "all") return true;
  if (filter === "actionable") return isActionable(result);
  return bucket === filter;
}

function matchesCarrier(result, carrier) {
  return carrier === "all" || result.carrier === carrier;
}

function applyFilters() {
  const query = (searchInput.value || "").trim().toLocaleLowerCase("th");
  let visible = 0;
  rows.forEach((row) => {
    const result = results.get(row.dataset.order);
    const matchesSearch = !query || row.dataset.search.includes(query);
    const show = Boolean(
      result
      && matchesSearch
      && matchesFilter(result, activeFilter)
      && matchesCarrier(result, activeCarrier)
    );
    row.hidden = !show;
    if (show) visible += 1;
  });
  document.querySelector("#visible-count").textContent = `แสดง ${visible} จาก ${rows.length} รายการ`;
  document.querySelector("#empty-table").hidden = visible !== 0;
  exportButton.disabled = visible === 0;
  exportButton.textContent = visible ? `Export CSV (${visible})` : "Export CSV";
  updateSummaryCounts();
}

function updateSummaryCounts() {
  const counts = { all: results.size, actionable: 0, pending: 0, delivered: 0, missing: 0, rejected: 0 };
  let stale = 0;
  results.forEach((result) => {
    const bucket = bucketFor(result);
    if (isActionable(result)) counts.actionable += 1;
    if (bucket in counts) counts[bucket] += 1;
    if (isStale(result)) stale += 1;
  });
  const labels = {
    actionable: "ต้องติดตาม",
    all: "ทั้งหมด",
    pending: "กำลังดำเนินการ",
    delivered: "สำเร็จ",
    missing: "ไม่พบ",
    rejected: "ไม่สำเร็จ",
  };
  filterButtons.forEach((button) => {
    button.textContent = `${labels[button.dataset.filter]} (${counts[button.dataset.filter]})`;
  });
  const carrierCounts = { all: results.size, skyfrog: 0, kex: 0, interexpress: 0 };
  results.forEach((result) => {
    if (result.carrier in carrierCounts) carrierCounts[result.carrier] += 1;
  });
  const carrierLabels = {
    all: "ทั้งหมด",
    skyfrog: "KLEAN&KARE",
    kex: "KEX",
    interexpress: "InterExpress",
  };
  carrierButtons.forEach((button) => {
    button.textContent = `${carrierLabels[button.dataset.carrier]} (${carrierCounts[button.dataset.carrier]})`;
  });
  const detail = [];
  if (counts.missing) detail.push(`ไม่พบ ${counts.missing}`);
  if (stale) detail.push(`ไม่ได้อัปเดตเกิน 24 ชม. ${stale}`);
  document.querySelector("#attention-banner strong").textContent = `มี ${counts.actionable} รายการที่ต้องติดตาม`;
  document.querySelector("#attention-detail").textContent = detail.length
    ? detail.join(" · ")
    : "รายการทั้งหมดได้รับการตรวจภายใน 24 ชั่วโมง";
}

function setFilter(filter) {
  activeFilter = filter;
  filterButtons.forEach((button) => button.classList.toggle("active", button.dataset.filter === filter));
  applyFilters();
}

function setCarrier(carrier) {
  activeCarrier = carrier;
  carrierButtons.forEach((button) => button.classList.toggle("active", button.dataset.carrier === carrier));
  applyFilters();
}

function carrierText(carrier) {
  return {
    skyfrog: "KLEAN&KARE",
    kex: "KEX",
    interexpress: "InterExpress",
  }[carrier] || carrier || "—";
}

function csvCell(value) {
  let text = Array.isArray(value) ? value.join(", ") : String(value ?? "");
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function exportVisibleRows() {
  const visibleResults = rows
    .filter((row) => !row.hidden)
    .map((row) => results.get(row.dataset.order))
    .filter(Boolean);
  if (!visibleResults.length) return;
  const headers = [
    "แถวในชีต",
    "ผู้ขนส่ง",
    "เลขออเดอร์/พัสดุ",
    "วันที่จากเลขงาน",
    "สถานะ",
    "รหัสสถานะ",
    "คนขับ",
    "วันสร้างงาน",
    "วันส่ง",
    "ตรวจล่าสุด",
    "จำนวนรูปหลักฐาน",
  ];
  const data = visibleResults.map((result) => [
    result.sheet_rows || [],
    carrierText(result.carrier),
    result.order_number,
    result.order_date,
    statusText(result),
    result.status_code,
    result.driver,
    result.created_at,
    result.delivery_at,
    result.checked_at,
    proofEntries(result).length,
  ]);
  const csv = [headers, ...data].map((record) => record.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const today = new Date().toISOString().slice(0, 10);
  link.href = url;
  link.download = `bedee-delivery-${today}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function setDialogText(selector, value) {
  document.querySelector(selector).textContent = value || "—";
}

function renderDialog(result) {
  activeOrder = result.order_number;
  const bucket = bucketFor(result);
  setDialogText("#dialog-order", result.order_number);
  setDialogText("#dialog-status", statusText(result));
  document.querySelector("#dialog-status").className = `dialog-status ${statusClass(bucket)}`;
  setDialogText("#dialog-driver", result.driver);
  setDialogText("#dialog-group", result.group_name);
  setDialogText("#dialog-created", result.created_at);
  setDialogText("#dialog-delivery", result.delivery_at);
  setDialogText("#dialog-checked", `${formatDate(result.checked_at)} · ${ageText(result.checked_at)}`);
  const grid = document.querySelector("#dialog-proof-grid");
  grid.replaceChildren();
  const proofs = proofEntries(result);
  if (!proofs.length) {
    grid.textContent = "ยังไม่มีรูปหลักฐาน";
  } else {
    proofs.forEach((proof) => grid.append(buildProofLink(proof.url, proof.number, true)));
  }
  document.querySelector("#dialog-note").textContent = isStale(result)
    ? "รายการนี้ไม่ได้รับการอัปเดตเกิน 24 ชั่วโมง แนะนำให้กดตรวจใหม่"
    : "";
}

function openDialog(orderNumber) {
  const result = results.get(orderNumber);
  if (!result) return;
  renderDialog(result);
  if (!dialog.open) dialog.showModal();
}

async function refreshOrder(orderNumber, trigger) {
  const original = trigger.textContent;
  trigger.disabled = true;
  trigger.textContent = "กำลังตรวจ…";
  document.querySelector("#dialog-note").textContent = "กำลังดึงข้อมูลสดจากระบบขนส่ง";
  try {
    const response = await fetch("/api/check", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order: orderNumber }),
    });
    if (response.status === 401) {
      window.location.assign("/login");
      return;
    }
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "ตรวจสอบไม่สำเร็จ");
    const previous = results.get(orderNumber);
    const result = {
      ...payload.result,
      carrier: previous?.carrier || "",
      proof_urls_filtered: false,
      proof_first_number: previous?.carrier === "skyfrog" ? 3 : 1,
    };
    result.bucket = bucketFor(result);
    results.set(result.order_number, result);
    updateRow(result);
    renderDialog(result);
    applyFilters();
    if (payload.sheet_sync?.enabled) {
      document.querySelector("#dialog-note").textContent = payload.sheet_sync.ok
        ? `บันทึก Google Sheet แล้ว ${payload.sheet_sync.updated_rows} แถว`
        : "ตรวจข้อมูลสำเร็จ แต่บันทึก Google Sheet ไม่สำเร็จ ระบบจะลองใหม่รอบถัดไป";
    } else {
      document.querySelector("#dialog-note").textContent = "อัปเดตข้อมูลสดเรียบร้อยแล้ว";
    }
  } catch (error) {
    document.querySelector("#dialog-note").textContent = error.message || "เชื่อมต่อไม่สำเร็จ กรุณาลองใหม่";
  } finally {
    trigger.disabled = false;
    trigger.textContent = original;
  }
}

async function copyText(value, successMessage) {
  if (!value) {
    document.querySelector("#dialog-note").textContent = "ไม่มีข้อมูลสำหรับคัดลอก";
    return;
  }
  await navigator.clipboard.writeText(value);
  document.querySelector("#dialog-note").textContent = successMessage;
}

function customerMessage(result) {
  const status = statusText(result);
  const delivery = result.delivery_at ? ` เมื่อ ${result.delivery_at}` : "";
  if (result.delivered) {
    return `สถานะการจัดส่ง หมายเลขคำสั่งซื้อ ${result.order_number} จัดส่งสำเร็จแล้ว${delivery}`;
  }
  return `สถานะการจัดส่ง หมายเลขคำสั่งซื้อ ${result.order_number} ${status}${delivery}`;
}

filterButtons.forEach((button) => button.addEventListener("click", () => setFilter(button.dataset.filter)));
carrierButtons.forEach((button) => button.addEventListener("click", () => setCarrier(button.dataset.carrier)));
exportButton.addEventListener("click", exportVisibleRows);
searchInput.addEventListener("input", applyFilters);
document.querySelector("[data-filter-jump]")?.addEventListener("click", () => setFilter("actionable"));
document.querySelector("#report-table tbody").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const order = button.dataset.order;
  if (button.dataset.action === "view") openDialog(order);
  if (button.dataset.action === "copy-order") {
    await copyText(order, `คัดลอก ${order} แล้ว`);
    button.textContent = "คัดลอกแล้ว";
    window.setTimeout(() => { button.textContent = "คัดลอก"; }, 1200);
  }
  if (button.dataset.action === "refresh") {
    openDialog(order);
    await refreshOrder(order, button);
  }
});

document.querySelector("[data-dialog-close]").addEventListener("click", () => dialog.close());
document.querySelector("#dialog-refresh").addEventListener("click", (event) => refreshOrder(activeOrder, event.currentTarget));
document.querySelector("#copy-order").addEventListener("click", () => copyText(activeOrder, `คัดลอก ${activeOrder} แล้ว`));
document.querySelector("#copy-proofs").addEventListener("click", () => {
  const urls = proofEntries(results.get(activeOrder) || {}).map((proof) => proof.url);
  return copyText(urls.join("\n"), `คัดลอกลิงก์ POD ${urls.length} รูปแล้ว`);
});
document.querySelector("#copy-message").addEventListener("click", () => {
  const result = results.get(activeOrder);
  return copyText(result ? customerMessage(result) : "", "คัดลอกข้อความแจ้งลูกค้าแล้ว");
});
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});

rows.forEach((row) => {
  const result = results.get(row.dataset.order);
  if (result) updateAge(row, result);
});
setFilter("actionable");
