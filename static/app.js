"use strict";

let templates = [];
let selectedTemplate = null;

const templateListEl = document.getElementById("template-list");
const fieldsFormEl = document.getElementById("fields-form");
const commandInputEl = document.getElementById("command-input");
const talkButtonEl = document.getElementById("talk-button");
const voiceStatusEl = document.getElementById("voice-status");
const titleInputEl = document.getElementById("title-input");
const historyListEl = document.getElementById("history-list");

const templateDialog = document.getElementById("template-dialog");
const templateForm = document.getElementById("template-form");
const templateDialogTitle = document.getElementById("template-dialog-title");
const templateNameEl = document.getElementById("template-name");
const templateCategoryEl = document.getElementById("template-category");
const templateBodyEl = document.getElementById("template-body");

async function apiGet(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}
async function apiSend(url, method, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

async function loadTemplates() {
  templates = await apiGet("/api/templates");
  renderTemplateList();
}

function renderTemplateList() {
  templateListEl.innerHTML = "";
  for (const t of templates) {
    const li = document.createElement("li");
    li.textContent = `[${t.category}] ${t.name}`;
    li.dataset.id = t.id;
    if (selectedTemplate && selectedTemplate.id === t.id) li.classList.add("selected");
    li.addEventListener("click", () => selectTemplate(t.id));
    templateListEl.appendChild(li);
  }
}

function selectTemplate(id) {
  selectedTemplate = templates.find((t) => t.id === id) || null;
  renderTemplateList();
  renderFieldsForm();
}

function renderFieldsForm() {
  fieldsFormEl.innerHTML = "";
  if (!selectedTemplate) return;
  for (const name of selectedTemplate.placeholders) {
    const label = document.createElement("label");
    label.textContent = `${name}:`;
    const input = document.createElement("input");
    input.type = "text";
    input.dataset.field = name;
    label.appendChild(document.createElement("br"));
    label.appendChild(input);
    fieldsFormEl.appendChild(label);
  }
}

function getFieldValues() {
  const values = {};
  fieldsFormEl.querySelectorAll("input[data-field]").forEach((input) => {
    values[input.dataset.field] = input.value;
  });
  return values;
}

function setFieldValue(name, value) {
  const input = fieldsFormEl.querySelector(`input[data-field="${CSS.escape(name)}"]`);
  if (input) input.value = value;
}

let dialogMode = "create";

document.getElementById("btn-new-template").addEventListener("click", () => {
  dialogMode = "create";
  templateDialogTitle.textContent = "নতুন টেমপ্লেট";
  templateNameEl.value = "";
  templateCategoryEl.value = "নোটিশ";
  templateBodyEl.value = "";
  templateDialog.showModal();
});

document.getElementById("btn-edit-template").addEventListener("click", () => {
  if (!selectedTemplate) { alert("প্রথমে একটি টেমপ্লেট নির্বাচন করুন।"); return; }
  dialogMode = "edit";
  templateDialogTitle.textContent = "টেমপ্লেট সম্পাদনা";
  templateNameEl.value = selectedTemplate.name;
  templateCategoryEl.value = selectedTemplate.category;
  templateBodyEl.value = selectedTemplate.body;
  templateDialog.showModal();
});

document.getElementById("btn-delete-template").addEventListener("click", async () => {
  if (!selectedTemplate) return;
  if (!confirm("এই টেমপ্লেটটি মুছে ফেলতে চান?")) return;
  await apiSend(`/api/templates/${selectedTemplate.id}`, "DELETE", {});
  selectedTemplate = null;
  await loadTemplates();
  renderFieldsForm();
});

document.getElementById("template-cancel").addEventListener("click", () => templateDialog.close());

templateForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    name: templateNameEl.value.trim(),
    category: templateCategoryEl.value,
    body: templateBodyEl.value,
  };
  if (!payload.name || !payload.body.trim()) {
    alert("নাম ও টেমপ্লেট লেখা আবশ্যক।");
    return;
  }
  try {
    if (dialogMode === "create") {
      await apiSend("/api/templates", "POST", payload);
    } else {
      await apiSend(`/api/templates/${selectedTemplate.id}`, "PUT", payload);
    }
    templateDialog.close();
    await loadTemplates();
  } catch (err) {
    alert("ত্রুটি: " + err.message);
  }
});

commandInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") applyCommandText(commandInputEl.value.trim());
});

async function applyCommandText(text) {
  if (!text) return;
  const knownPlaceholders = selectedTemplate ? selectedTemplate.placeholders : [];
  const result = await apiSend("/api/parse-command", "POST", { text, placeholders: knownPlaceholders });

  if (result.category && (!selectedTemplate || selectedTemplate.category !== result.category)) {
    const match = templates.find((t) => t.category === result.category);
    if (match) {
      selectTemplate(match.id);
    } else {
      voiceStatusEl.textContent = `"${result.category}" ধরনের কোনো টেমপ্লেট পাওয়া যায়নি। আগে একটি টেমপ্লেট তৈরি করুন।`;
      return;
    }
  }

  for (const [name, value] of Object.entries(result.field_values)) {
    setFieldValue(name, value);
  }
  if (!titleInputEl.value.trim() && Object.values(result.field_values).length) {
    titleInputEl.value = Object.values(result.field_values)[0].slice(0, 50);
  }
}

const SpeechRecognitionClass = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;
let isRecording = false;

if (!SpeechRecognitionClass) {
  talkButtonEl.disabled = true;
  talkButtonEl.textContent = "🎤 এই ব্রাউজারে ভয়েস সমর্থিত নয়";
  voiceStatusEl.textContent = "টাইপ করে কমান্ড দিন। (Chrome ব্রাউজারে ভয়েস কাজ করে।)";
} else {
  recognizer = new SpeechRecognitionClass();
  recognizer.continuous = false;
  recognizer.interimResults = false;
  recognizer.lang = "bn-BD";

  recognizer.addEventListener("result", (event) => {
    const transcript = event.results[0][0].transcript;
    commandInputEl.value = transcript;
    voiceStatusEl.textContent = `শোনা গেছে: "${transcript}"`;
    applyCommandText(transcript);
  });

  recognizer.addEventListener("error", (event) => {
    voiceStatusEl.textContent = "শোনা যায়নি (" + event.error + ")। আবার চেষ্টা করুন বা টাইপ করুন।";
  });

  recognizer.addEventListener("end", () => {
    isRecording = false;
    talkButtonEl.classList.remove("recording");
    talkButtonEl.textContent = "🎤 চেপে ধরে বলুন";
  });

  const startRecording = (e) => {
    e.preventDefault();
    if (isRecording) return;
    isRecording = true;
    talkButtonEl.classList.add("recording");
    talkButtonEl.textContent = "🔴 শুনছি...";
    voiceStatusEl.textContent = "রেকর্ড হচ্ছে...";
    try {
      recognizer.start();
    } catch (err) {}
  };

  const stopRecording = (e) => {
    e.preventDefault();
    if (!isRecording) return;
    recognizer.stop();
  };

  talkButtonEl.addEventListener("mousedown", startRecording);
  talkButtonEl.addEventListener("mouseup", stopRecording);
  talkButtonEl.addEventListener("mouseleave", (e) => { if (isRecording) stopRecording(e); });
  talkButtonEl.addEventListener("touchstart", startRecording, { passive: false });
  talkButtonEl.addEventListener("touchend", stopRecording, { passive: false });
}

document.getElementById("btn-generate").addEventListener("click", async () => {
  if (!selectedTemplate) { alert("প্রথমে একটি টেমপ্লেট নির্বাচন করুন।"); return; }
  const title = titleInputEl.value.trim();
  if (!title) { alert("ফাইলের শিরোনাম দিন।"); return; }

  try {
    await apiSend("/api/generate", "POST", {
      template_id: selectedTemplate.id,
      title,
      field_values: getFieldValues(),
    });
    await loadHistory();
    speakFeedback("ডকুমেন্ট তৈরি হয়েছে");
    alert("ডকুমেন্ট তৈরি হয়েছে। নিচের তালিকা থেকে ডাউনলোড করুন।");
  } catch (err) {
    alert("ত্রুটি: " + err.message);
  }
});

function speakFeedback(text) {
  if (!("speechSynthesis" in window)) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "bn-BD";
  window.speechSynthesis.speak(utterance);
}

async function loadHistory() {
  const docs = await apiGet("/api/documents");
  historyListEl.innerHTML = "";
  for (const d of docs) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = `${d.created_at} — ${d.title}`;
    const link = document.createElement("a");
    link.textContent = "ডাউনলোড";
    link.href = `/api/documents/${d.id}/download`;
    li.appendChild(label);
    li.appendChild(link);
    historyListEl.appendChild(li);
  }
}

(async function init() {
  await loadTemplates();
  await loadHistory();
})();
