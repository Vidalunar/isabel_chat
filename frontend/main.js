const messagesEl = document.getElementById('messages');
const formEl = document.getElementById('chatForm');
const inputEl = document.getElementById('userInput');
const sourcesEl = document.getElementById('sources');
const backendStatusEl = document.getElementById('backendStatus');

// Extensiones
const exportBtn = document.getElementById('exportPdfBtn');
const stopTtsBtn = document.getElementById('stopTtsBtn');
const exportDlg = document.getElementById('exportDlg');
const confirmExportBtn = document.getElementById('confirmExport');
const cancelExportBtn = document.getElementById('cancelExport');
const schoolNameInput = document.getElementById('schoolName');
const coverTitleInput = document.getElementById('coverTitle');
const studentNameInput = document.getElementById('studentName');

/* =========================================
   1️⃣ Comprobación del backend
========================================= */
async function pingBackend() {
  try {
    const res = await fetch(`${window.BACKEND_URL}/health`);
    const data = await res.json();
    backendStatusEl.textContent = `✔ Conectado (${data.model})`;
    backendStatusEl.style.color = '#3ddc97';
  } catch {
    backendStatusEl.textContent = '✖ Backend no disponible';
    backendStatusEl.style.color = '#e63946';
  }
}

/* =========================================
   2️⃣ Funciones de interfaz
========================================= */
function createTtsControls(text) {
  const wrap = document.createElement('div');
  wrap.className = 'tts-controls';
  const btn = document.createElement('button');
  btn.className = 'tts-btn';
  btn.type = 'button';
  btn.title = 'Escuchar';
  btn.textContent = '🔊 Escuchar';
  btn.addEventListener('click', () => speak(text));
  wrap.appendChild(btn);
  return wrap;
}

function addMessage(role, text) {
  const wrap = document.createElement('div');
  wrap.className = 'message';
  const name = role === 'user' ? 'Tú' : 'Isabel I';
  const roleEl = document.createElement('div');
  roleEl.className = 'role';
  roleEl.textContent = name;

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;

  wrap.appendChild(roleEl);
  wrap.appendChild(bubble);

  // Añadir controles TTS para respuestas de Isabel
  if (role !== 'user') {
    const tts = createTtsControls(text);
    wrap.appendChild(tts);
  }

  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderSources(list) {
  sourcesEl.innerHTML = '';
  if (!list?.length) {
    sourcesEl.innerHTML = '<p class="muted">No hay citas disponibles.</p>';
    return;
  }
  for (const s of list) {
    const item = document.createElement('div');
    item.className = 'source-item';
    item.innerHTML = `<div class="meta">${s.filename} · pág. ${s.page ?? '-'}</div><div class="snippet">${s.text ?? ''}</div>`;
    sourcesEl.appendChild(item);
  }
}

/* =========================================
   3️⃣ Chat principal
========================================= */
formEl.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = inputEl.value.trim();
  if (!q) return;
  addMessage('user', q);
  inputEl.value = '';
  addMessage('assistant', 'Pensando…');
  try {
    const res = await fetch(`${window.BACKEND_URL}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q })
    });
    const data = await res.json();
    const lastBubble = messagesEl.querySelector('.message:last-child .bubble');
    lastBubble.textContent = data.answer;
    // Añadir botón TTS ahora que tenemos el texto final
    messagesEl.querySelector('.message:last-child').appendChild(createTtsControls(data.answer));
    renderSources(data.sources);
  } catch (err) {
    const last = messagesEl.querySelector('.message:last-child .bubble');
    if (last) last.textContent = 'Error al conectar con el servidor.';
  }
});

/* =========================================
   4️⃣ Lectura en voz alta (TTS)
========================================= */
let currentUtterance = null;

function speak(text) {
  if (!('speechSynthesis' in window)) {
    alert('Este navegador no soporta lectura en voz alta.');
    return;
  }
  window.speechSynthesis.cancel();
  currentUtterance = new SpeechSynthesisUtterance(text);
  currentUtterance.lang = 'es-ES';
  currentUtterance.rate = 1.0;
  currentUtterance.pitch = 1.0;
  window.speechSynthesis.speak(currentUtterance);
}

stopTtsBtn.addEventListener('click', () => {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
});

/* =========================================
   5️⃣ Exportar conversación a PDF
========================================= */
function openExportDialog() {
  schoolNameInput.value = window.SCHOOL_NAME || '';
  coverTitleInput.value = 'Isabel I · Conversación guiada';
  studentNameInput.value = '';
  exportDlg.showModal();
}

async function exportConversationPDF() {
  const { jsPDF } = window.jspdf;
  const pdf = new jsPDF({ unit: 'pt', format: 'a4' });
  const pageW = 595, pageH = 842;
  const margin = 40;

  // Portada
  pdf.setFillColor(250, 247, 242);
  pdf.rect(0, 0, pageW, pageH, 'F');
  pdf.setTextColor(17, 24, 39);

  try {
    const logoDataUrl = await loadImageAsDataURL('logo-dsm.jpg');
    const imgW = 260, imgH = 100;
    pdf.addImage(logoDataUrl, 'JPEG', (pageW - imgW) / 2, 90, imgW, imgH);
  } catch (_) {}

  const school = (schoolNameInput.value || window.SCHOOL_NAME || '').trim();
  const title = (coverTitleInput.value || 'Isabel I · Conversación guiada').trim();
  const student = (studentNameInput.value || '').trim();

  pdf.setFont('helvetica', 'bold');
  pdf.setFontSize(20);
  pdf.text(title, pageW / 2, 230, { align: 'center' });
  pdf.setFont('helvetica', 'normal');
  pdf.setFontSize(12);
  if (school) pdf.text(school, pageW / 2, 255, { align: 'center' });
  if (student) pdf.text(student, pageW / 2, 275, { align: 'center' });
  const today = new Date().toLocaleString();
  pdf.setTextColor(100);
  pdf.text(today, pageW / 2, 295, { align: 'center' });

  // Página con conversación
  pdf.addPage();
  let y = margin;
  const msgs = Array.from(messagesEl.querySelectorAll('.message'));
  for (const m of msgs) {
    const role = m.querySelector('.role')?.textContent || '';
    const text = m.querySelector('.bubble')?.textContent || '';

    pdf.setFont('helvetica', 'bold');
    pdf.setTextColor(17, 24, 39);
    pdf.setFontSize(12);
    pdf.text(role + ':', margin, y); y += 16;

    pdf.setFont('helvetica', 'normal');
    pdf.setTextColor(30);
    pdf.setFontSize(11);
    const lines = pdf.splitTextToSize(text, pageW - margin * 2);
    for (const line of lines) {
      if (y > pageH - margin) { pdf.addPage(); y = margin; }
      pdf.text(line, margin, y);
      y += 14;
    }
    y += 10;
    if (y > pageH - margin) { pdf.addPage(); y = margin; }
  }

  // Página de fuentes
  const srcItems = Array.from(sourcesEl.querySelectorAll('.source-item'));
  if (srcItems.length) {
    pdf.addPage(); y = margin;
    pdf.setFont('helvetica', 'bold'); pdf.setFontSize(14);
    pdf.setTextColor(17, 24, 39);
    pdf.text('Fuentes', margin, y); y += 18;
    pdf.setFont('helvetica', 'normal'); pdf.setFontSize(11); pdf.setTextColor(30);
    for (const it of srcItems) {
      const meta = it.querySelector('.meta')?.textContent || '';
      const snip = it.querySelector('.snippet')?.textContent || '';
      const lines = pdf.splitTextToSize(`• ${meta} — ${snip}`, pageW - margin * 2);
      for (const line of lines) {
        if (y > pageH - margin) { pdf.addPage(); y = margin; }
        pdf.text(line, margin, y);
        y += 14;
      }
      y += 6;
    }
  }

  const filename = `isabel-chat_${new Date().toISOString().slice(0,10)}.pdf`;
  pdf.save(filename);
}

function loadImageAsDataURL(path) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0);
      resolve(canvas.toDataURL('image/jpeg'));
    };
    img.onerror = reject;
    img.src = path;
  });
}

/* =========================================
   6️⃣ Eventos del diálogo de exportación
========================================= */
exportBtn.addEventListener('click', () => openExportDialog());
cancelExportBtn.addEventListener('click', () => exportDlg.close());
confirmExportBtn.addEventListener('click', async () => {
  exportDlg.close();
  await exportConversationPDF();
});

/* =========================================
   7️⃣ Inicialización
========================================= */
pingBackend();
