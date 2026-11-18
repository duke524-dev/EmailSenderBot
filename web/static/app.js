const form = document.getElementById("compose");
const statusEl = document.getElementById("status");
const sendBtn = document.getElementById("send");
const stopBtn = document.getElementById("stop");
const testBtn = document.getElementById("test-login");
const progressEl = document.getElementById("progress");
const progressFill = document.getElementById("progress-fill");
const progressLabel = document.getElementById("progress-label");
const progressCount = document.getElementById("progress-count");
const progressTrack = progressEl.querySelector(".progress-track");
const logView = document.getElementById("log-view");
const refreshLogsBtn = document.getElementById("refresh-logs");
const listFileInput = document.getElementById("list-file");
const uploadStatus = document.getElementById("upload-status");
const toInput = document.getElementById("to");
const templateGrid = document.getElementById("template-grid");
const templateModal = document.getElementById("template-modal");
const templatePreview = document.getElementById("template-preview");
const previewTitle = document.getElementById("preview-title");
const previewBlurb = document.getElementById("preview-blurb");
const useTemplateBtn = document.getElementById("use-template");
const useTemplateTextBtn = document.getElementById("use-template-text");
const templateTextPreview = document.getElementById("template-text-preview");
const subjectInput = document.getElementById("subject");
const bodyInput = document.getElementById("body");
const loadTemplateBtn = document.getElementById("load-template");

let idleTimer = null;
let sendAbort = null;
let lastSendProgress = { sent: 0, failed: 0, planned: 0 };
const clientLogs = [];
let selectedTemplateId = "";
let selectedTemplateName = "";
let selectedTemplateHtml = "";
let selectedTemplateText = "";
let selectedTemplateSubject = "";
let templatesCache = [];
let previewRequestId = 0;
let previewMode = "html";

function nowStamp() {
  return new Date().toLocaleTimeString();
}

function renderLogs(serverLines = []) {
  const blocks = [];
  if (clientLogs.length) {
    blocks.push("Client");
    blocks.push(...clientLogs.slice(-40));
  }
  if (serverLines.length) {
    if (blocks.length) {
      blocks.push("");
    }
    blocks.push("Server");
    blocks.push(...serverLines.slice(-80));
  }
  logView.textContent = blocks.length ? blocks.join("\n") : "Waiting for activity…";
  logView.scrollTop = logView.scrollHeight;
}

function pushLog(message) {
  clientLogs.push(`[${nowStamp()}] ${message}`);
  if (clientLogs.length > 100) {
    clientLogs.splice(0, clientLogs.length - 100);
  }
  renderLogs();
}

function showServerLogs(lines) {
  if (Array.isArray(lines) && lines.length) {
    renderLogs(lines);
  }
}

async function fetchServerLogs() {
  try {
    const response = await fetch("/api/logs");
    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (_error) {
      pushLog(`Could not read /api/logs (${response.status}): ${text.slice(0, 300)}`);
      return;
    }
    showServerLogs(data.lines || []);
  } catch (error) {
    pushLog(`Could not load server logs: ${error.message || error}`);
  }
}

function setStatus(message, kind) {
  statusEl.textContent = message;
  statusEl.classList.remove("is-ok", "is-error");
  if (kind) {
    statusEl.classList.add(kind);
  }
}

function clearIdleTimer() {
  if (idleTimer) {
    clearInterval(idleTimer);
    idleTimer = null;
  }
}

function showProgress() {
  progressEl.hidden = false;
  progressEl.classList.remove("is-waiting", "is-done", "is-error");
}

function hideProgress() {
  clearIdleTimer();
  progressEl.hidden = true;
}

function updateProgress({ done = 0, planned = 0, label = "", state = "" } = {}) {
  const total = Math.max(planned, 1);
  const pct = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
  progressFill.style.width = `${pct}%`;
  progressTrack.setAttribute("aria-valuenow", String(pct));
  progressCount.textContent = `${done} / ${planned}`;
  if (label) {
    progressLabel.textContent = label;
  }
  progressEl.classList.remove("is-waiting", "is-done", "is-error");
  if (state) {
    progressEl.classList.add(state);
  }
}

function startIdleCountdown(seconds, email) {
  clearIdleTimer();
  let remaining = seconds;
  updateProgress({
    done: Number(progressCount.textContent.split("/")[0].trim()) || 0,
    planned: Number(progressCount.textContent.split("/")[1].trim()) || 0,
    label: `Waiting ${remaining}s before ${email}`,
    state: "is-waiting",
  });
  idleTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearIdleTimer();
      progressLabel.textContent = `Sending to ${email}…`;
      progressEl.classList.remove("is-waiting");
      return;
    }
    progressLabel.textContent = `Waiting ${remaining}s before ${email}`;
  }, 1000);
}

function selectedFormat() {
  const checked = document.querySelector('input[name="body_format"]:checked');
  return checked ? checked.value : "auto";
}

function randomStyleEnabled() {
  const toggle = document.getElementById("random_style");
  return Boolean(toggle && toggle.checked);
}

function syncBodyFormatUi() {
  const format = selectedFormat();
  const body = document.getElementById("body");
  const hint = document.getElementById("body-hint");
  const randomOn = randomStyleEnabled();
  const formatInputs = document.querySelectorAll('input[name="body_format"]');
  body.classList.toggle("is-html", format === "html" || format === "auto" || randomOn);
  body.classList.toggle("is-random", randomOn);
  body.required = !randomOn;
  formatInputs.forEach((input) => {
    input.disabled = randomOn;
  });
  if (randomOn) {
    body.placeholder = "Optional preview only — each recipient gets a random hiring HTML style.";
    hint.textContent =
      "Random style is on: each bulk recipient gets a different HireLens hiring template. Subject is shared; the content box is ignored.";
    return;
  }
  if (format === "html") {
    body.placeholder = "<h1>Hello</h1>\n<p>Write HTML email content here.</p>";
    hint.textContent =
      "HTML mode: paste markup such as <h1>, <p>, and <a href>. Recipients see rendered HTML, not raw tags.";
  } else if (format === "auto") {
    body.placeholder = "Write plain text, or paste HTML like <h1>Hello</h1>";
    hint.textContent =
      "Auto detects tags like <h1> and <p> and sends them as real HTML. Turn on Random style to vary templates per recipient.";
  } else {
    body.placeholder = "Write the email here.";
    hint.textContent =
      "Plain text mode. If you paste HTML tags here, they are still auto-upgraded to rendered HTML.";
  }
}

function payload() {
  return {
    email: document.getElementById("email").value.trim(),
    password: document.getElementById("password").value,
    from_name: document.getElementById("from_name").value.trim(),
    to: document.getElementById("to").value,
    subject: document.getElementById("subject").value.trim(),
    body: document.getElementById("body").value,
    body_format: randomStyleEnabled() ? "html" : selectedFormat(),
    random_style: randomStyleEnabled(),
    physical_address: document.getElementById("physical_address").value.trim(),
    idle_seconds: Number(document.getElementById("idle_seconds").value),
  };
}

function formatHttpError(status, contentType, bodyText, parsed) {
  const parts = [`HTTP ${status}`];
  if (contentType) {
    parts.push(`content-type: ${contentType}`);
  }
  if (parsed && parsed.error) {
    parts.push(parsed.error);
  }
  if (parsed && parsed.detail) {
    parts.push(String(parsed.detail).trim());
  }
  if (!parsed) {
    parts.push(bodyText.trim().slice(0, 1200) || "(empty body)");
  }
  return parts.join("\n");
}

async function readResponsePayload(response) {
  const contentType = response.headers.get("content-type") || "";
  const bodyText = await response.text();
  let parsed = null;
  if (bodyText) {
    try {
      parsed = JSON.parse(bodyText);
    } catch (_error) {
      parsed = null;
    }
  }
  return { contentType, bodyText, parsed };
}

async function postJson(url, data) {
  pushLog(`POST ${url}`);
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  } catch (error) {
    pushLog(`Network error on ${url}: ${error.message || error}`);
    await fetchServerLogs();
    throw new Error(`Network error: ${error.message || error}`);
  }

  const { contentType, bodyText, parsed } = await readResponsePayload(response);
  pushLog(`Response ${response.status} from ${url} (${contentType || "no content-type"})`);
  if (parsed && parsed.logs) {
    showServerLogs(parsed.logs);
  } else {
    await fetchServerLogs();
  }

  if (!parsed) {
    const message = formatHttpError(response.status, contentType, bodyText, null);
    pushLog(message);
    throw new Error(message);
  }
  if (!response.ok || parsed.ok === false) {
    const message = formatHttpError(response.status, contentType, bodyText, parsed);
    pushLog(message);
    throw new Error(parsed.error || message);
  }
  return parsed;
}

function isAbortError(error) {
  return (
    Boolean(error) &&
    (error.name === "AbortError" || /aborted|abort/i.test(String(error.message || error)))
  );
}

async function postSendStream(data, onEvent, signal) {
  pushLog("POST /api/send (stream)");
  let response;
  try {
    response = await fetch("/api/send", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/x-ndjson",
      },
      body: JSON.stringify(data),
      signal,
    });
  } catch (error) {
    if (isAbortError(error) || (signal && signal.aborted)) {
      const stopped = new Error(
        `Stopped. Sent ${lastSendProgress.sent} of ${lastSendProgress.planned || "?"}.`
      );
      stopped.name = "AbortError";
      stopped.stopped = true;
      throw stopped;
    }
    pushLog(`Network error on /api/send: ${error.message || error}`);
    await fetchServerLogs();
    throw new Error(`Network error: ${error.message || error}`);
  }

  const contentType = response.headers.get("content-type") || "";
  pushLog(`Response ${response.status} from /api/send (${contentType || "no content-type"})`);

  if (!response.ok) {
    const { bodyText, parsed } = await readResponsePayload(response);
    if (parsed && parsed.logs) {
      showServerLogs(parsed.logs);
    } else {
      await fetchServerLogs();
    }
    const message = formatHttpError(response.status, contentType, bodyText, parsed);
    pushLog(message);
    throw new Error((parsed && parsed.error) || message);
  }
  if (!response.body) {
    await fetchServerLogs();
    throw new Error("No response stream from server.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalEvent = null;

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) {
          continue;
        }
        let event;
        try {
          event = JSON.parse(trimmed);
        } catch (error) {
          pushLog(`Bad stream line: ${trimmed.slice(0, 400)}`);
          throw new Error(`Bad stream line from server: ${error.message}`);
        }
        if (event.message) {
          pushLog(event.message);
        }
        if (event.logs) {
          showServerLogs(event.logs);
        }
        onEvent(event);
        if (event.type === "done" || event.type === "error") {
          finalEvent = event;
        }
      }
    }
  } catch (error) {
    if (isAbortError(error) || (signal && signal.aborted)) {
      if (finalEvent && (finalEvent.type === "done" || finalEvent.stopped)) {
        return finalEvent;
      }
      const stopped = {
        type: "done",
        ok: true,
        stopped: true,
        sent: lastSendProgress.sent,
        failed: lastSendProgress.failed,
        planned: lastSendProgress.planned,
        message: `Stopped. Sent ${lastSendProgress.sent} of ${lastSendProgress.planned || "?"}.`,
      };
      onEvent(stopped);
      return stopped;
    }
    throw error;
  }

  const leftover = buffer.trim();
  if (leftover) {
    let event;
    try {
      event = JSON.parse(leftover);
    } catch (error) {
      pushLog(`Bad final stream line: ${leftover.slice(0, 400)}`);
      await fetchServerLogs();
      throw new Error(`Bad final stream line from server: ${error.message}`);
    }
    if (event.message) {
      pushLog(event.message);
    }
    if (event.logs) {
      showServerLogs(event.logs);
    }
    onEvent(event);
    if (event.type === "done" || event.type === "error") {
      finalEvent = event;
    }
  }

  if (!finalEvent) {
    await fetchServerLogs();
    throw new Error("Send finished without a final status.");
  }
  if (finalEvent.type === "error" || (finalEvent.ok === false && !finalEvent.sent)) {
    if (finalEvent.detail) {
      pushLog(String(finalEvent.detail).trim());
    }
    throw new Error(finalEvent.error || finalEvent.message || "Send failed.");
  }
  return finalEvent;
}

function setBodyFormat(format) {
  const input = document.querySelector(`input[name="body_format"][value="${format}"]`);
  if (input) {
    input.checked = true;
    syncBodyFormatUi();
  }
}

function openTemplateModal() {
  templateModal.hidden = false;
  document.body.style.overflow = "hidden";
  renderTemplateModal();
  const initial = selectedTemplateId || (templatesCache[0] && templatesCache[0].id) || "";
  if (initial) {
    const match = templatesCache.find((item) => item.id === initial) || templatesCache[0];
    previewTemplate(match.id, match.name, match.blurb);
  } else {
    clearPreview();
  }
  pushLog("Opened template picker modal.");
}

function closeTemplateModal() {
  templateModal.hidden = true;
  document.body.style.overflow = "";
}

function showPreviewMode(mode) {
  previewMode = mode === "text" ? "text" : "html";
  const showText = previewMode === "text";
  templatePreview.hidden = showText;
  templateTextPreview.hidden = !showText;
  useTemplateBtn.classList.toggle("primary", !showText);
  useTemplateBtn.classList.toggle("ghost", showText);
  useTemplateTextBtn.classList.toggle("primary", showText);
  useTemplateTextBtn.classList.toggle("ghost", !showText);
}

function clearPreview() {
  selectedTemplateHtml = "";
  selectedTemplateText = "";
  selectedTemplateSubject = "";
  useTemplateBtn.disabled = true;
  useTemplateTextBtn.disabled = true;
  previewTitle.textContent = "Preview";
  previewBlurb.textContent = "Select a template to preview it here.";
  templatePreview.removeAttribute("srcdoc");
  templatePreview.src = "about:blank";
  templateTextPreview.textContent = "";
  showPreviewMode("html");
}

function markPreviewSelection(templateId) {
  templateGrid.querySelectorAll(".template-card").forEach((card) => {
    card.classList.toggle("is-active", card.dataset.id === templateId);
  });
}

function renderTemplateModal() {
  templateGrid.innerHTML = "";
  if (!templatesCache.length) {
    templateGrid.innerHTML = `<p class="hint">No built-in templates found.</p>`;
    clearPreview();
    return;
  }
  templatesCache.forEach((template) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "template-card";
    if (template.id === selectedTemplateId) {
      button.classList.add("is-active");
    }
    button.dataset.id = template.id;
    button.innerHTML = `<strong>${template.name}</strong><span>${template.blurb}</span>`;
    button.addEventListener("click", () => {
      previewTemplate(template.id, template.name, template.blurb);
    });
    templateGrid.appendChild(button);
  });
}

async function previewTemplate(templateId, templateName = "", templateBlurb = "") {
  if (!templateId) {
    return;
  }
  const requestId = ++previewRequestId;
  selectedTemplateId = templateId;
  selectedTemplateName = templateName || templateId;
  markPreviewSelection(templateId);
  useTemplateBtn.disabled = true;
  useTemplateTextBtn.disabled = true;
  previewTitle.textContent = selectedTemplateName;
  previewBlurb.textContent = "Loading preview…";
  templatePreview.removeAttribute("srcdoc");
  templatePreview.src = "about:blank";
  templateTextPreview.textContent = "";
  showPreviewMode(selectedFormat() === "plain" ? "text" : "html");
  pushLog(`Previewing template: ${templateId}`);
  try {
    const response = await fetch(`/api/templates/${encodeURIComponent(templateId)}`);
    const { contentType, bodyText, parsed } = await readResponsePayload(response);
    if (requestId !== previewRequestId) {
      return;
    }
    if (!parsed || !response.ok || parsed.ok === false) {
      throw new Error((parsed && parsed.error) || formatHttpError(response.status, contentType, bodyText, parsed));
    }
    const html = parsed.html || "";
    const text = parsed.text || "";
    if (!html) {
      throw new Error("Template returned empty HTML.");
    }
    if (!text) {
      throw new Error("Template returned empty plain text.");
    }
    selectedTemplateHtml = html;
    selectedTemplateText = text;
    selectedTemplateSubject = parsed.subject || "";
    selectedTemplateName = parsed.name || templateName || templateId;
    previewTitle.textContent = selectedTemplateName;
    previewBlurb.textContent =
      (templateBlurb || parsed.blurb || "Preview ready.") +
      " Use HTML for styled mail, or plain text for a text-only send.";
    templatePreview.srcdoc = html;
    templateTextPreview.textContent = text;
    useTemplateBtn.disabled = false;
    useTemplateTextBtn.disabled = false;
  } catch (error) {
    if (requestId !== previewRequestId) {
      return;
    }
    clearPreview();
    previewTitle.textContent = templateName || templateId;
    previewBlurb.textContent = error.message || String(error);
    setStatus(error.message || String(error), "is-error");
    pushLog(error.message || String(error));
  }
}

function usePreviewedTemplate(format = "html") {
  const asPlain = format === "plain" || format === "text";
  const content = asPlain ? selectedTemplateText : selectedTemplateHtml;
  if (!content) {
    setStatus("Select a template to preview first.", "is-error");
    return;
  }
  const contentBox = document.getElementById("body");
  if (selectedTemplateSubject) {
    subjectInput.value = selectedTemplateSubject;
  }
  contentBox.value = content;
  contentBox.dispatchEvent(new Event("input", { bubbles: true }));
  setBodyFormat(asPlain ? "plain" : "html");
  closeTemplateModal();
  contentBox.focus();
  contentBox.scrollIntoView({ behavior: "smooth", block: "center" });
  const kind = asPlain ? "plain text" : "HTML";
  const message = `Loaded ${selectedTemplateName} ${kind} into the Email content box.`;
  setStatus(message, "is-ok");
  pushLog(message);
}

async function loadTemplates() {
  try {
    const response = await fetch("/api/templates");
    const data = await response.json();
    templatesCache = data.templates || [];
    pushLog(`Ready: ${templatesCache.length} built-in hiring templates.`);
  } catch (error) {
    templatesCache = [];
    pushLog(`Could not load templates: ${error.message || error}`);
  }
}

async function loadDefaults() {
  try {
    const response = await fetch("/api/defaults");
    const data = await response.json();
    if (data.email) {
      document.getElementById("email").value = data.email;
    }
    if (data.from_name) {
      document.getElementById("from_name").value = data.from_name;
    }
    if (data.physical_address) {
      document.getElementById("physical_address").value = data.physical_address;
    }
    if (typeof data.idle_seconds === "number") {
      document.getElementById("idle_seconds").value = String(data.idle_seconds);
    }
    if (data.password_saved) {
      document.getElementById("password").placeholder = "Saved in .env — enter to override";
      document.getElementById("password").required = false;
    }
    pushLog("Loaded mailbox defaults.");
    await fetchServerLogs();
  } catch (error) {
    pushLog(`Could not load defaults: ${error.message || error}`);
  }
}

testBtn.addEventListener("click", async () => {
  hideProgress();
  setStatus("Checking login…");
  testBtn.disabled = true;
  try {
    const data = payload();
    if (!data.email) {
      throw new Error("Email address is required.");
    }
    if (!data.password && document.getElementById("password").required) {
      throw new Error("Password is required.");
    }
    const result = await postJson("/api/test-login", {
      email: data.email,
      password: data.password,
      from_name: data.from_name,
    });
    setStatus(result.message || "Login ok.", "is-ok");
  } catch (error) {
    setStatus(error.message || String(error), "is-error");
  } finally {
    testBtn.disabled = false;
  }
});

function setSendingUi(isSending) {
  sendBtn.disabled = isSending;
  testBtn.disabled = isSending;
  stopBtn.hidden = !isSending;
  stopBtn.disabled = !isSending;
}

stopBtn.addEventListener("click", () => {
  if (!sendAbort) {
    return;
  }
  stopBtn.disabled = true;
  clearIdleTimer();
  setStatus("Stopping…");
  progressLabel.textContent = "Stopping after the current email…";
  pushLog("Stop requested. Finishing the current email, then ending the run.");
  sendAbort.abort();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearIdleTimer();
  if (sendAbort) {
    sendAbort.abort();
  }
  sendAbort = new AbortController();
  lastSendProgress = { sent: 0, failed: 0, planned: 0 };
  setStatus("Sending…");
  showProgress();
  updateProgress({ done: 0, planned: 0, label: "Connecting…", state: "" });
  setSendingUi(true);
  try {
    const data = payload();
    if (Number.isNaN(data.idle_seconds) || data.idle_seconds < 0) {
      throw new Error("Idle time must be 0 or more seconds.");
    }
    const result = await postSendStream(
      data,
      (event) => {
        if (typeof event.sent === "number") {
          lastSendProgress.sent = event.sent;
        }
        if (typeof event.failed === "number") {
          lastSendProgress.failed = event.failed;
        }
        if (typeof event.planned === "number") {
          lastSendProgress.planned = event.planned;
        }
        if (event.type === "start") {
          updateProgress({
            done: 0,
            planned: event.planned,
            label: event.message || "Starting…",
          });
          return;
        }
        if (event.type === "waiting") {
          updateProgress({
            done: event.done,
            planned: event.planned,
            label: event.message,
            state: "is-waiting",
          });
          startIdleCountdown(event.idle_seconds, event.email);
          return;
        }
        if (event.type === "sending") {
          clearIdleTimer();
          updateProgress({
            done: event.done,
            planned: event.planned,
            label: event.message || `Sending to ${event.email}…`,
          });
          return;
        }
        if (event.type === "sent" || event.type === "failed") {
          clearIdleTimer();
          updateProgress({
            done: event.done,
            planned: event.planned,
            label: event.message,
            state: event.type === "failed" ? "is-error" : "",
          });
          return;
        }
        if (event.type === "done") {
          clearIdleTimer();
          updateProgress({
            done: event.sent + event.failed,
            planned: event.planned,
            label: event.message || "Done",
            state: event.stopped ? "is-done" : event.failed ? "is-error" : "is-done",
          });
        }
        if (event.type === "error") {
          clearIdleTimer();
          progressEl.classList.add("is-error");
          updateProgress({
            done: event.done || 0,
            planned: event.planned || 0,
            label: event.message || event.error || "Error",
            state: "is-error",
          });
        }
      },
      sendAbort.signal
    );

    const lines = [];
    if (result.stopped) {
      lines.push(result.message || `Stopped. Sent ${result.sent} of ${result.planned}.`);
    } else {
      lines.push(`Sent ${result.sent} of ${result.planned}.`);
    }
    if (result.random_style) {
      lines.push("Random HTML style was used per recipient.");
    }
    if (result.idle_seconds) {
      lines.push(`Idle between emails: ${result.idle_seconds}s.`);
    }
    if (result.failed) {
      lines.push(`Failed ${result.failed}.`);
    }
    if (result.errors && result.errors.length) {
      lines.push(...result.errors.slice(0, 8));
    }
    setStatus(lines.join("\n"), result.failed && !result.stopped ? "is-error" : "is-ok");
  } catch (error) {
    clearIdleTimer();
    if (error && error.stopped) {
      updateProgress({
        done: lastSendProgress.sent + lastSendProgress.failed,
        planned: lastSendProgress.planned,
        label: error.message,
        state: "is-done",
      });
      setStatus(error.message || "Stopped.", "is-ok");
    } else {
      progressEl.classList.add("is-error");
      await fetchServerLogs();
      setStatus(error.message || String(error), "is-error");
    }
  } finally {
    sendAbort = null;
    setSendingUi(false);
  }
});

refreshLogsBtn.addEventListener("click", () => {
  fetchServerLogs();
});

function setUploadStatus(message, kind) {
  uploadStatus.hidden = !message;
  uploadStatus.textContent = message || "";
  uploadStatus.classList.toggle("is-error", kind === "is-error");
}

function mergeToText(existing, incoming) {
  const lines = [];
  const seen = new Set();
  for (const block of [existing, incoming]) {
    for (const line of String(block || "").split(/\r?\n/)) {
      const value = line.trim();
      if (!value) {
        continue;
      }
      const key = value.toLowerCase();
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      lines.push(value);
    }
  }
  return lines.join("\n");
}

listFileInput.addEventListener("change", async () => {
  const file = listFileInput.files && listFileInput.files[0];
  if (!file) {
    return;
  }
  setUploadStatus(`Reading ${file.name}…`);
  pushLog(`Upload list file: ${file.name}`);
  try {
    const content = await file.text();
    const result = await postJson("/api/parse-list", {
      filename: file.name,
      content,
    });
    toInput.value = mergeToText(toInput.value, result.to_text || "");
    const message = `${result.message} Added into To.`;
    setUploadStatus(message);
    setStatus(message, "is-ok");
    pushLog(message);
  } catch (error) {
    const message = error.message || String(error);
    setUploadStatus(message, "is-error");
    setStatus(message, "is-error");
  } finally {
    listFileInput.value = "";
  }
});

loadTemplateBtn.addEventListener("click", async () => {
  if (!templatesCache.length) {
    await loadTemplates();
  }
  openTemplateModal();
});

useTemplateBtn.addEventListener("click", () => {
  showPreviewMode("html");
  usePreviewedTemplate("html");
});

useTemplateTextBtn.addEventListener("click", () => {
  showPreviewMode("text");
  usePreviewedTemplate("plain");
});

useTemplateBtn.addEventListener("mouseenter", () => {
  if (!useTemplateBtn.disabled) {
    showPreviewMode("html");
  }
});

useTemplateTextBtn.addEventListener("mouseenter", () => {
  if (!useTemplateTextBtn.disabled) {
    showPreviewMode("text");
  }
});

templateModal.querySelectorAll("[data-close-modal]").forEach((el) => {
  el.addEventListener("click", () => closeTemplateModal());
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !templateModal.hidden) {
    closeTemplateModal();
  }
});

document.querySelectorAll('input[name="body_format"]').forEach((input) => {
  input.addEventListener("change", syncBodyFormatUi);
});
document.getElementById("random_style").addEventListener("change", () => {
  if (randomStyleEnabled()) {
    setBodyFormat("html");
    const subject = document.getElementById("subject");
    if (!subject.value.trim() && templatesCache[0] && templatesCache[0].subject) {
      subject.value = templatesCache[0].subject;
    }
  }
  syncBodyFormatUi();
  pushLog(
    randomStyleEnabled()
      ? "Random style on: each recipient gets a random hiring template."
      : "Random style off: using the Email content box."
  );
});
syncBodyFormatUi();
loadDefaults();
loadTemplates();
