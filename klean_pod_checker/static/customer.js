const form = document.querySelector("#customer-search-form");
const orderInput = document.querySelector("#customer-order");
const searchButton = document.querySelector("#customer-search-button");
const searchStatus = document.querySelector("#customer-search-status");
const errorBox = document.querySelector("#customer-error");
const resultPanel = document.querySelector("#customer-result");
const foundContent = document.querySelector("#customer-found-content");
const notFound = document.querySelector("#customer-not-found");
const historyCard = document.querySelector("#customer-history-card");
const historyNote = document.querySelector("#customer-history-note");
const timeline = document.querySelector("#customer-timeline");
const warning = document.querySelector("#customer-exception");
const resultSeparator = document.querySelector("#customer-result-separator");
const locationFact = document.querySelector("#customer-location-fact");
const shipmentFacts = document.querySelector(".shipment-facts");

const NOT_FOUND_GUIDANCE = "สอบถามข้อมูลเพิ่มเติม กรุณาติดต่อฝ่ายบริการลูกค้าตามช่องทางที่ท่านสั่งซื้อ";
const LIMITED_HISTORY_NOTE = "ขนส่งส่งกลับมาเฉพาะสถานะล่าสุด ระบบจึงแสดงข้อมูลเท่าที่มี";
const CACHED_STATUS_NOTE = "แสดงสถานะล่าสุดที่ระบบบันทึกไว้ เนื่องจากขนส่งตอบกลับชั่วคราว";
const TRANSIENT_HTTP_STATUSES = new Set([424, 502, 503, 504, 520, 521, 522, 523, 524]);

const STAGES = ["received", "in_transit", "out_for_delivery", "delivered"];
const STAGE_LABELS = {
  received: "รับงานแล้ว",
  in_transit: "กำลังขนส่ง",
  out_for_delivery: "กำลังนำส่ง",
  delivered: "จัดส่งสำเร็จ",
};
const STAGE_ICONS = {
  received: "▤",
  in_transit: "▰",
  out_for_delivery: "⌖",
  delivered: "✓",
  exception: "!",
};

function setText(selector, value) {
  document.querySelector(selector).textContent = value || "—";
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function normalizeDate(value) {
  if (!value) return null;
  const source = String(value).trim();
  if (!source) return null;
  let normalized = source
    .replace(/^(\d{4})\/(\d{1,2})\/(\d{1,2})\s+/, "$1-$2-$3T")
    .replace(/^(\d{4})-(\d{1,2})-(\d{1,2})\s+/, "$1-$2-$3T");
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatFullDate(value) {
  const parsed = normalizeDate(value);
  if (!parsed) return value || "ยังไม่ระบุโดยขนส่ง";
  return new Intl.DateTimeFormat("th-TH", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function sameCalendarDay(left, right) {
  return left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate();
}

function formatTimelineDate(value) {
  const parsed = normalizeDate(value);
  if (!parsed) return { day: value || "ไม่ระบุเวลา", time: "" };
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  let day;
  if (sameCalendarDay(parsed, now)) day = "วันนี้";
  else if (sameCalendarDay(parsed, yesterday)) day = "เมื่อวาน";
  else {
    day = new Intl.DateTimeFormat("th-TH", {
      day: "numeric",
      month: "short",
      year: "2-digit",
    }).format(parsed);
  }
  const time = new Intl.DateTimeFormat("th-TH", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
  return { day, time };
}

function deriveStage(result) {
  if (STAGES.includes(result.stage)) return result.stage;
  const code = String(result.status_code || "").toUpperCase();
  const label = `${result.status_th || ""} ${result.status_en || ""}`.toLowerCase();
  if (result.delivered || ["A", "C", "POD", "PODEX", "400", "401"].includes(code)) return "delivered";
  if (["S", "045", "300"].includes(code) || /ออกนำส่ง|กำลังนำส่ง|กำลังจัดส่งพัสดุ|ระหว่างการนำส่ง|นำจ่าย|out for delivery/.test(label)) return "out_for_delivery";
  if (["109", "200", "SIP-LH"].includes(code) || /ระหว่างขนส่ง|กำลังขนส่ง|ถึงศูนย์|ออกจากศูนย์กระจายสินค้า|ถึงคลังสินค้าปลายทาง|in transit/.test(label)) return "in_transit";
  return "received";
}

function renderProgress(stage) {
  const stageIndex = Math.max(0, STAGES.indexOf(stage));
  const progress = document.querySelector("#tracking-progress");
  const fill = stageIndex === 0 ? 0 : (stageIndex / (STAGES.length - 1)) * 100;
  progress.style.setProperty("--progress-fill", `${fill}%`);
  progress.querySelectorAll("li").forEach((item, index) => {
    item.classList.toggle("complete", index < stageIndex);
    item.classList.toggle("current", index === stageIndex);
    if (index === stageIndex) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
}

function appendTimelineItem(event, index) {
  const item = document.createElement("li");
  item.className = "timeline-item";

  const when = document.createElement("time");
  when.className = "timeline-when";
  if (event.time) when.dateTime = event.time;
  const formatted = formatTimelineDate(event.time);
  const day = document.createElement("span");
  day.textContent = formatted.day;
  const time = document.createElement("span");
  time.textContent = formatted.time;
  when.append(day, time);

  const rail = document.createElement("span");
  rail.className = "timeline-rail";
  rail.setAttribute("aria-hidden", "true");
  const dot = document.createElement("span");
  dot.className = "timeline-dot";
  dot.textContent = STAGE_ICONS[event.stage] || "•";
  rail.append(dot);

  const description = document.createElement("div");
  description.className = "timeline-event";
  description.textContent = event.label;
  if (index === 0 && event.carrier) {
    const source = document.createElement("small");
    source.textContent = `ข้อมูลล่าสุดจาก ${event.carrier}`;
    description.append(source);
  }

  item.append(when, rail, description);
  timeline.append(item);
}

function renderTimeline(result, stage, statusLabel) {
  timeline.replaceChildren();
  const events = [];
  const latestTime = result.delivered
    ? (result.delivery_at || result.updated_at || result.checked_at)
    : (result.updated_at || result.delivery_at || result.checked_at);
  events.push({
    label: statusLabel,
    time: latestTime,
    stage: result.exception ? "exception" : stage,
    carrier: result.carrier,
  });

  const latestDate = normalizeDate(latestTime);
  const createdDate = normalizeDate(result.created_at);
  const distinctCreatedTime = result.created_at
    && (!latestDate || !createdDate || latestDate.getTime() !== createdDate.getTime());
  if (distinctCreatedTime && stage !== "received") {
    events.push({
      label: STAGE_LABELS.received,
      time: result.created_at,
      stage: "received",
      carrier: result.carrier,
    });
  }

  events.forEach(appendTimelineItem);
  if (result.cached) {
    historyNote.textContent = CACHED_STATUS_NOTE;
    historyNote.classList.remove("hidden");
  } else {
    historyNote.textContent = LIMITED_HISTORY_NOTE;
    historyNote.classList.toggle("hidden", events.length > 1);
  }
}

function formatExceptionMessage(statusLabel) {
  if (String(statusLabel || "").includes("เตรียมส่งกลับพัสดุไปยังผู้ส่ง")) {
    return NOT_FOUND_GUIDANCE;
  }
  return `สถานะนี้ต้องติดตามเป็นพิเศษ: ${statusLabel}`;
}

function renderResult(result) {
  resultPanel.classList.remove("hidden", "not-found-mode");
  resultPanel.setAttribute("aria-busy", "false");
  setText("#customer-result-order", result.order_number || result.lookup_order);

  if (!result.found) {
    const trackingNumber = result.order_number || orderInput.value.trim().toUpperCase();
    setText("#customer-result-order", trackingNumber);
    resultSeparator.textContent = "-";
    setText("#customer-carrier-inline", NOT_FOUND_GUIDANCE);
    notFound.classList.remove("hidden");
    foundContent.classList.add("hidden");
    historyCard.classList.add("hidden");
    resultPanel.classList.add("not-found-mode");
    searchStatus.textContent = "ไม่พบข้อมูลพัสดุ";
    return;
  }

  const stage = deriveStage(result);
  const statusLabel = result.status_th || result.status_en || STAGE_LABELS[stage];
  resultSeparator.textContent = " · ";
  notFound.classList.add("hidden");
  foundContent.classList.remove("hidden");
  historyCard.classList.remove("hidden");
  setText("#customer-carrier-inline", result.carrier);
  setText("#customer-carrier-result", result.carrier);
  setText("#customer-status", statusLabel);
  locationFact.classList.toggle("hidden", !result.location);
  shipmentFacts.classList.toggle("has-location", Boolean(result.location));
  setText("#customer-location", result.location);

  const dateLabel = document.querySelector("#customer-date-label");
  if (result.delivered) {
    dateLabel.textContent = "จัดส่งเมื่อ";
    setText("#customer-delivery", formatFullDate(result.delivery_at || result.updated_at));
  } else {
    dateLabel.textContent = "อัปเดตล่าสุด";
    setText("#customer-delivery", formatFullDate(result.updated_at || result.delivery_at));
  }

  warning.classList.toggle("hidden", !result.exception);
  warning.textContent = result.exception ? formatExceptionMessage(statusLabel) : "";
  renderProgress(stage);
  renderTimeline(result, stage, statusLabel);
  searchStatus.textContent = `พบพัสดุ สถานะ ${statusLabel}`;
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function readApiPayload(response) {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch (_error) {
    return null;
  }
}

async function requestCustomerStatus(order) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch("/api/customer-check", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ order }),
      });
      const payload = await readApiPayload(response);
      if (response.ok && payload?.result) return payload.result;

      const transient = payload === null || TRANSIENT_HTTP_STATUSES.has(response.status);
      if (attempt === 0 && transient) {
        searchButton.textContent = "กำลังลองอีกครั้ง…";
        searchStatus.textContent = "ระบบขนส่งตอบกลับช้า กำลังลองเชื่อมต่ออีกครั้ง";
        await wait(800);
        continue;
      }
      throw new Error(payload?.error || "ระบบขนส่งตอบกลับช้า กรุณาลองใหม่อีกครั้ง");
    } catch (error) {
      if (attempt === 0 && error instanceof TypeError) {
        searchButton.textContent = "กำลังลองอีกครั้ง…";
        searchStatus.textContent = "การเชื่อมต่อสะดุด กำลังลองอีกครั้ง";
        await wait(800);
        continue;
      }
      throw error;
    }
  }
  throw new Error("เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.classList.add("hidden");
  resultPanel.classList.add("hidden");
  resultPanel.setAttribute("aria-busy", "true");
  searchButton.disabled = true;
  searchButton.textContent = "กำลังตรวจสอบ…";
  searchStatus.textContent = "กำลังตรวจสอบสถานะพัสดุ";
  try {
    const result = await requestCustomerStatus(orderInput.value.trim());
    renderResult(result);
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    resultPanel.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "nearest" });
  } catch (error) {
    resultPanel.setAttribute("aria-busy", "false");
    showError(error.message || "เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่");
    searchStatus.textContent = "ตรวจสอบสถานะไม่สำเร็จ";
  } finally {
    searchButton.disabled = false;
    searchButton.textContent = "ตรวจสอบ";
  }
});
