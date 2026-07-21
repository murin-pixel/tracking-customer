const form = document.querySelector("#search-form");
const input = document.querySelector("#order-input");
const carrierSelect = document.querySelector("#carrier-select");
const button = document.querySelector("#search-button");
const errorBox = document.querySelector("#search-error");
const panel = document.querySelector("#result-panel");
const singleResultPanel = document.querySelector("#single-result");
const groupResultPanel = document.querySelector("#group-result");
let activeResult = null;

function setText(selector, value) {
  document.querySelector(selector).textContent = value || "—";
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
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

function renderProofs(urls) {
  const section = document.querySelector("#proof-section");
  const grid = document.querySelector("#proof-grid");
  grid.replaceChildren();
  const safeUrls = (urls || []).map(safeProofUrl).filter(Boolean);
  section.classList.toggle("hidden", safeUrls.length === 0);
  document.querySelector("#proof-count").textContent = `${safeUrls.length} รูป`;
  safeUrls.forEach((url, index) => {
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.className = "proof-card";
    const image = document.createElement("img");
    image.src = url;
    image.alt = `รูปหลักฐาน ${index + 1}`;
    image.loading = "lazy";
    const label = document.createElement("span");
    label.textContent = `เปิดรูปที่ ${index + 1}`;
    link.append(image, label);
    grid.append(link);
  });
}

function proofUrls(result) {
  return [...new Set((result?.proof_urls || []).map(safeProofUrl).filter(Boolean))];
}

function ageText(value) {
  const checked = Date.parse(value || "");
  if (!Number.isFinite(checked)) return "";
  const minutes = Math.max(0, Math.floor((Date.now() - checked) / 60000));
  if (minutes < 1) return "ตรวจเมื่อสักครู่";
  if (minutes < 60) return `ตรวจ ${minutes} นาทีที่แล้ว`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `ตรวจ ${hours} ชั่วโมงที่แล้ว`;
  return `ตรวจ ${Math.floor(hours / 24)} วันที่แล้ว`;
}

async function copyText(value, message) {
  const note = document.querySelector("#result-note");
  if (!value) {
    note.textContent = "ไม่มีข้อมูลสำหรับคัดลอก";
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
    note.textContent = message;
  } catch {
    note.textContent = "คัดลอกไม่สำเร็จ กรุณาลองใหม่";
  }
}

function customerMessage(result) {
  const orderNumber = result.mapping_order_number || result.order_number;
  const status = result.found
    ? `${result.status_th || result.status_en || "ไม่ทราบสถานะ"}${result.status_code ? ` (${result.status_code})` : ""}`
    : "ไม่พบในระบบขนส่ง";
  const delivery = result.delivery_at ? ` เมื่อ ${result.delivery_at}` : "";
  if (result.delivered) {
    return `สถานะการจัดส่ง หมายเลขคำสั่งซื้อ ${orderNumber} จัดส่งสำเร็จแล้ว${delivery}`;
  }
  return `สถานะการจัดส่ง หมายเลขคำสั่งซื้อ ${orderNumber} ${status}${delivery}`;
}

function statusInfo(result) {
  if (result.error) return { text: "ตรวจสอบไม่สำเร็จ", kind: "rejected" };
  if (!result.found) return { text: "ไม่พบข้อมูล", kind: "missing" };
  const text = `${result.status_th || result.status_en || "ไม่ทราบสถานะ"}${result.status_code ? ` (${result.status_code})` : ""}`;
  if (result.delivered) return { text, kind: "delivered" };
  if (["E", "P", "401"].includes(result.status_code)) return { text, kind: "rejected" };
  return { text, kind: "pending" };
}

function groupStatus(summary) {
  if (summary.total && summary.delivered === summary.total) {
    return { text: "ครบทุกพัสดุ", kind: "delivered" };
  }
  if (summary.errors) return { text: "ต้องตรวจสอบ", kind: "rejected" };
  if (summary.missing) return { text: "พบข้อมูลไม่ครบ", kind: "missing" };
  if (summary.delivered) return { text: "จัดส่งบางส่วน", kind: "pending" };
  return { text: "กำลังดำเนินการ", kind: "pending" };
}

function renderGroupedResult(summary, entries) {
  activeResult = null;
  document.querySelector("#result-note").textContent = "";
  singleResultPanel.classList.add("hidden");
  groupResultPanel.classList.remove("hidden");
  panel.classList.remove("hidden");
  setText("#group-order", summary.order_number);
  const summaryParts = [`รวม ${summary.total} พัสดุ`, `จัดส่งสำเร็จ ${summary.delivered}`];
  if (summary.pending) summaryParts.push(`กำลังดำเนินการ ${summary.pending}`);
  if (summary.missing) summaryParts.push(`ไม่พบข้อมูล ${summary.missing}`);
  if (summary.errors) summaryParts.push(`ตรวจสอบไม่สำเร็จ ${summary.errors}`);
  setText("#group-summary", summaryParts.join(" · "));
  const groupPill = document.querySelector("#group-status");
  const groupState = groupStatus(summary);
  groupPill.textContent = groupState.text;
  groupPill.className = `status-pill ${groupState.kind}`;

  const list = document.querySelector("#group-track-list");
  list.replaceChildren();
  entries.forEach((entry) => {
    const result = entry.result || {};
    const state = statusInfo(result);
    const card = document.createElement("article");
    card.className = "group-track-card";

    const header = document.createElement("div");
    header.className = "group-track-header";
    const title = document.createElement("div");
    const tracking = document.createElement("code");
    tracking.textContent = entry.tracking_number || result.order_number || "—";
    const carrier = document.createElement("span");
    carrier.className = "carrier-label";
    carrier.textContent = entry.carrier || "—";
    title.append(tracking, carrier);
    const statePill = document.createElement("span");
    statePill.className = `status-pill ${state.kind}`;
    statePill.textContent = state.text;
    header.append(title, statePill);

    const details = document.createElement("div");
    details.className = "group-track-details";
    const status = document.createElement("span");
    status.textContent = result.error
      ? "เชื่อมต่อผู้ให้บริการไม่สำเร็จ กรุณาลองใหม่"
      : result.status_th || result.status_en || "ยังไม่พบข้อมูล";
    const timing = document.createElement("span");
    timing.textContent = result.delivery_at
      ? `จัดส่งเมื่อ ${result.delivery_at}`
      : result.checked_at
        ? `ตรวจเมื่อ ${result.checked_at}`
        : "ยังไม่มีเวลาตรวจสอบ";
    details.append(status, timing);

    const proofs = proofUrls(result);
    if (proofs.length) {
      const proofSection = document.createElement("section");
      proofSection.className = "group-proof-section";
      const proofTitle = document.createElement("h3");
      proofTitle.textContent = `รูปหลักฐาน (${proofs.length} รูป)`;
      const proofGrid = document.createElement("div");
      proofGrid.className = "group-proof-grid";
      proofs.forEach((proof, index) => {
        const proofLink = document.createElement("a");
        proofLink.href = proof;
        proofLink.target = "_blank";
        proofLink.rel = "noreferrer";
        proofLink.className = "group-proof-image";
        const image = document.createElement("img");
        image.src = proof;
        image.alt = `รูปหลักฐาน ${index + 1} ของ ${entry.tracking_number || result.order_number || "พัสดุ"}`;
        image.loading = "lazy";
        proofLink.append(image);
        proofGrid.append(proofLink);
      });
      proofSection.append(proofTitle, proofGrid);
      card.append(proofSection);
    }
    card.append(header, details);
    list.append(card);
  });
}

function renderResult(result) {
  activeResult = result;
  document.querySelector("#result-note").textContent = "";
  groupResultPanel.classList.add("hidden");
  singleResultPanel.classList.remove("hidden");
  panel.classList.remove("hidden");
  setText("#result-order", result.order_number);
  const status = document.querySelector("#result-status");
  const notFound = document.querySelector("#not-found");
  const details = document.querySelector("#result-details");
  if (!result.found) {
    status.textContent = "ไม่พบข้อมูล";
    status.className = "status-pill missing";
    notFound.classList.remove("hidden");
    details.classList.add("hidden");
    document.querySelector("#proof-section").classList.add("hidden");
    document.querySelector("#result-age").textContent = ageText(result.checked_at);
    return;
  }
  notFound.classList.add("hidden");
  details.classList.remove("hidden");
  const state = statusInfo(result);
  status.textContent = state.text;
  status.className = `status-pill ${state.kind}`;
  setText("#result-driver", result.driver);
  setText("#result-carrier", result.carrier);
  setText("#result-created", result.created_at);
  setText("#result-delivery", result.delivery_at);
  setText("#result-checked", result.checked_at);
  const age = document.querySelector("#result-age");
  age.textContent = ageText(result.checked_at);
  const checked = Date.parse(result.checked_at || "");
  age.classList.toggle(
    "stale",
    !result.delivered && Number.isFinite(checked) && Date.now() - checked > 86400000,
  );
  renderProofs(result.proof_urls);
}

document.querySelector("#copy-result-order").addEventListener("click", () => {
  copyText(
    activeResult?.mapping_order_number || activeResult?.order_number || "",
    "คัดลอกเลขออเดอร์แล้ว",
  );
});
document.querySelector("#copy-result-proofs").addEventListener("click", () => {
  const urls = proofUrls(activeResult);
  copyText(urls.join("\n"), `คัดลอกลิงก์ POD ${urls.length} รูปแล้ว`);
});
document.querySelector("#copy-result-message").addEventListener("click", () => {
  copyText(activeResult ? customerMessage(activeResult) : "", "คัดลอกข้อความแจ้งลูกค้าแล้ว");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.classList.add("hidden");
  panel.classList.add("hidden");
  button.disabled = true;
  button.textContent = "กำลังค้นหา…";
  try {
    const response = await fetch("/api/check", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order: input.value, carrier: carrierSelect.value }),
    });
    if (response.status === 401) {
      window.location.assign("/login");
      return;
    }
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "ตรวจสอบไม่สำเร็จ กรุณาลองใหม่");
    }
    if (payload.group?.multiple && Array.isArray(payload.results)) {
      renderGroupedResult(payload.group, payload.results);
    } else {
      renderResult(payload.result);
    }
    if (payload.sheet_sync?.enabled) {
      document.querySelector("#result-note").textContent = payload.sheet_sync.ok
        ? `บันทึก Google Sheet แล้ว ${payload.sheet_sync.updated_rows} แถว`
        : "ตรวจสำเร็จ แต่บันทึก Google Sheet ไม่สำเร็จ ระบบจะลองใหม่รอบถัดไป";
    }
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showError(error.message || "เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่");
  } finally {
    button.disabled = false;
    button.textContent = "ตรวจสอบสถานะ";
  }
});
