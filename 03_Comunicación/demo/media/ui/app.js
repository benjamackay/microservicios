const host = location.hostname || "localhost";
const API_GATEWAY = location.port === "8007" ? "" : `http://${host}:8007`;
const API = { gateway: API_GATEWAY };
const $ = (selector) => document.querySelector(selector);
const state = { users: [], channels: [], user: localStorage.getItem("media-user") || "", channel: localStorage.getItem("media-channel") || "", filter: "ALL", messages: [], media: [], jobs: [], polls: [], summaries: [], search: "" };
const palette = ["#f4ddcd", "#deebe4", "#e2e5fa", "#f3e7b8", "#e8dcef", "#d8eaf0"];

async function api(base, path, options = {}) {
  const response = await fetch(`${base}${path}`, options);
  if (!response.ok) {
    let detail = `Error ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch { /* empty body */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response;
}
async function graphql(query, variables = {}) {
  const result = await api(API.gateway, "/api/v1/graphql", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables })
  });
  if (result.errors?.length) {
    const detail = result.errors.map((error) => error.extensions?.detail || error.message).join(" · ");
    throw new Error(detail);
  }
  return result.data || {};
}
const auth = () => ({ "X-User-Id": state.user });
const esc = (value = "") => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
function initials(name = "?") { return name.trim().split(/\s+/).slice(0, 2).map((x) => x[0] || "").join("").toUpperCase(); }
function userById(id) { return state.users.find((user) => user.id === id) || { display_name: "Usuario" }; }
function toast(message, error = false) {
  const el = $("#toast"); el.textContent = message; el.style.background = error ? "#9a4c48" : "#202d43"; el.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove("show"), 2800);
}
function setUser(id) {
  state.user = id; localStorage.setItem("media-user", id);
  const name = userById(id).display_name || "Usuario";
  $("#railAvatar").textContent = initials(name); $("#profileAvatar").textContent = initials(name);
}
function renderUsers() {
  const select = $("#userSelect"); select.replaceChildren();
  state.users.forEach((user) => { const option = document.createElement("option"); option.value = user.id; option.textContent = user.display_name; select.append(option); });
  if (state.users.length) { const id = state.users.some((x) => x.id === state.user) ? state.user : state.users[0].id; select.value = id; setUser(id); }
}
function renderChannels() {
  const root = $("#channelList"); root.replaceChildren();
  state.channels.forEach((channel) => {
    const button = document.createElement("button"); button.className = `channel-button${channel.id === state.channel ? " active" : ""}`;
    button.innerHTML = `<span class="channel-hash">#</span><span>${esc(channel.name)}</span>${channel.id === state.channel ? '<i class="channel-unread"></i>' : ""}`;
    button.addEventListener("click", () => selectChannel(channel.id)); root.append(button);
  });
  const mobile = $("#mobileChannel"); mobile.replaceChildren();
  state.channels.forEach((channel) => { const option = document.createElement("option"); option.value = channel.id; option.textContent = `# ${channel.name}`; mobile.append(option); });
  if (state.channel) mobile.value = state.channel;
  const current = state.channels.find((x) => x.id === state.channel);
  $("#channelName").textContent = current?.name || "Selecciona un canal";
  $("#channelSubtitle").textContent = current ? "Conversación del equipo · Los archivos viven en Media" : "Crea o selecciona un canal para empezar";
  $("#composerChannel").textContent = `# ${current?.name || "canal"}`;
  $("#messageText").disabled = !current; $("#sendButton").disabled = !current;
  $("#createPollButton").disabled = !current; $("#requestSummaryButton").disabled = !current;
}
function dateLabel(iso) {
  const date = new Date(iso); const today = new Date();
  if (date.toDateString() === today.toDateString()) return "Hoy";
  return date.toLocaleDateString("es-CL", { weekday: "long", day: "numeric", month: "long" });
}
function messageUrls(content) {
  return [...new Set((content.match(/https?:\/\/[^\s<>"']+/g) || []).map((raw) => {
    const value = raw.replace(/[.,;:!?)}\]]+$/, "");
    try {
      const parsed = new URL(value);
      parsed.hash = "";
      return parsed.href;
    } catch { return value; }
  }))].slice(0, 10);
}
function linkPreviewCard(message, url) {
  const job = state.jobs.find((item) => item.message_id === message.id && item.url === url);
  const ready = job?.status === "ready", failed = job?.status === "failed", preview = job?.preview || {};
  let hostname = url; try { hostname = new URL(url).hostname; } catch { /* Keep the URL. */ }
  const title = ready ? preview.title : failed ? "No se pudo generar la vista previa" : "Preparando vista previa…";
  const description = ready ? preview.description : failed ? (job.last_error || "El enlace sigue disponible para abrirlo.") : "El enlace ya está en el chat. Media está obteniendo sus metadatos en segundo plano.";
  const target = ready ? (preview.final_url || preview.url || url) : url;
  return `<div class="inline-preview ${ready ? "preview-ready" : failed ? "preview-failed" : "preview-pending"}">${ready && preview.image_url ? `<img class="preview-image" src="${esc(preview.image_url)}" alt="Vista previa de ${esc(title)}">` : `<span class="preview-icon">${failed ? "!" : "↗"}</span>`}<div class="preview-copy"><span class="preview-state">${ready ? "VISTA PREVIA" : failed ? "PREVIEW NO DISPONIBLE" : "VISTA PREVIA · EN PROCESO"}</span><strong>${esc(title)}</strong><p>${esc(description || hostname)}</p><a href="${esc(target)}" target="_blank" rel="noreferrer">${esc(hostname)} ↗</a></div></div>`;
}
function fileChatEntry(item) {
  const author = userById(item.uploader_id), name = author.display_name || "Usuario";
  const color = palette[Math.abs([...name].reduce((sum, char) => sum + char.charCodeAt(0), 0)) % palette.length];
  const icon = item.mime_type?.startsWith("image/") ? "▧" : item.mime_type === "application/pdf" ? "PDF" : "▤";
  return `<article class="message shared-file"><div class="message-avatar" style="background:${color}">${esc(initials(name))}</div><div class="message-main"><div class="message-meta"><span class="message-name">${esc(name)}</span><time class="message-time">${new Date(item.created_at).toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit" })}</time></div><p class="message-text">compartió un archivo</p><div class="chat-file-card"><span class="chat-file-icon ${item.category === "IMAGE" ? "image-file" : item.mime_type === "application/pdf" ? "pdf-file" : ""}">${icon}</span><div class="chat-file-info"><strong title="${esc(item.filename)}">${esc(item.filename)}</strong><span>${prettySize(item.size_bytes)} · ${esc(item.mime_type)}</span></div><button class="chat-file-download" data-download="${esc(item.id)}" title="Descargar ${esc(item.filename)}">↓</button></div></div></article>`;
}
function pollIdFromMessage(message) { return message.content.match(/Poll ID:\s*([\da-f-]{36})/i)?.[1] || ""; }
function pollCard(poll) {
  if (!poll) return `<section class="poll-card poll-loading"><div class="poll-card-heading"><span>▤</span><div><strong>Encuesta</strong><small>Conectando con el servicio de encuestas…</small></div></div></section>`;
  const closed = poll.status !== "open", voted = localStorage.getItem(`media-voted:${poll.poll_id}:${state.user}`) === "1";
  const percent = (count) => poll.total_votes ? Math.round(count / poll.total_votes * 100) : 0;
  const status = poll.status === "open" ? `Abierta · cierra ${new Date(poll.closes_at).toLocaleString("es-CL", { hour: "2-digit", minute: "2-digit" })}` : poll.status === "closed" ? "Encuesta cerrada" : poll.status === "pending_announcement" ? "Publicando encuesta…" : "No se pudo publicar";
  const options = poll.options.map((option) => {
    const p = percent(option.vote_count);
    return `<div class="poll-option"><div class="poll-option-line"><span>${esc(option.text)}</span><b>${option.vote_count} <small>(${p}%)</small></b></div><div class="poll-result-track"><i style="width:${p}%"></i></div>${poll.status === "open" && !voted ? `<button class="poll-vote" data-poll="${poll.poll_id}" data-option="${option.option_id}">Votar</button>` : ""}</div>`;
  }).join("");
  const close = poll.status === "open" && poll.author_id === state.user ? `<button class="poll-close" data-close-poll="${poll.poll_id}">Cerrar encuesta</button>` : "";
  return `<section class="poll-card"><div class="poll-card-heading"><span>▤</span><div><strong>${esc(poll.question)}</strong><small>${esc(status)} · ${poll.total_votes} votos</small></div></div><div class="poll-options">${options}</div>${close}</section>`;
}
function summaryCard(summary) {
  const done = summary.messageState === "completada", failed = summary.messageState === "fallida";
  const label = done ? "RESUMEN LISTO" : failed ? "NO SE PUDO GENERAR" : summary.messageState === "procesando" ? "ANALIZANDO MENSAJES…" : "RESUMEN EN COLA";
  const body = done ? esc(summary.content || "").replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>") : failed ? esc(summary.error || "El servicio no pudo completar el resumen.") : "Se está preparando un resumen de los mensajes del canal. Se actualizará aquí automáticamente.";
  return `<article class="message summary-message"><div class="message-avatar summary-avatar">✦</div><div class="message-main"><div class="message-meta"><span class="message-name">Resumen del canal</span><time class="message-time">${new Date(summary.created_at).toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit" })}</time></div><section class="summary-card ${done ? "summary-ready" : failed ? "summary-failed" : "summary-pending"}"><span class="summary-state">${label}</span><div class="summary-content">${body}</div>${done && summary.structured ? `<div class="summary-tags">${summary.structured.decisions?.length ? `<span>${summary.structured.decisions.length} decisiones</span>` : ""}${summary.structured.events?.length ? `<span>${summary.structured.events.length} eventos</span>` : ""}${summary.structured.tasks?.length ? `<span>${summary.structured.tasks.length} tareas</span>` : ""}</div>` : ""}</section></div></article>`;
}
// Renderiza mensajes y adjuntos juntos, como una sola cronología del canal.
function renderMessages() {
  const root = $("#messages"), channel = state.channels.find((x) => x.id === state.channel);
  if (!channel) { root.innerHTML = '<div class="welcome"><div class="welcome-icon">✳</div><h2>Tu equipo, en un mismo lugar.</h2><p>Selecciona un canal para ver los mensajes y compartir archivos.</p></div>'; return; }
  const query = state.search.toLowerCase();
  const entries = [...state.messages.map((item) => ({ kind: "message", item, at: item.created_at })), ...state.media.filter((item) => item.category !== "LINK").map((item) => ({ kind: "file", item, at: item.created_at })), ...state.summaries.map((item) => ({ kind: "summary", item, at: item.created_at }))]
    .sort((a, b) => new Date(a.at) - new Date(b.at))
    .filter(({ kind, item }) => !query || (kind === "message" ? item.content.toLowerCase().includes(query) : kind === "file" ? item.filename.toLowerCase().includes(query) : (item.content || item.error || "Resumen").toLowerCase().includes(query)));
  if (!entries.length) { root.innerHTML = `<div class="welcome"><div class="welcome-icon">#</div><h2>${query ? "Sin coincidencias" : `Este es el comienzo de #${esc(channel.name)}`}</h2><p>${query ? "Prueba con otro término." : "Envía un mensaje o comparte el primer archivo del canal."}</p></div>`; return; }
  let lastDay = "";
  root.innerHTML = entries.map(({ kind, item }) => {
    const day = dateLabel(item.created_at); let html = "";
    if (day !== lastDay) { html += `<div class="day-divider"><span>${esc(day)}</span></div>`; lastDay = day; }
    if (kind === "file") return html + fileChatEntry(item);
    if (kind === "summary") return html + summaryCard(item);
    const message = item, author = userById(message.author_id), name = author.display_name || "Usuario";
    const color = palette[Math.abs([...name].reduce((sum, char) => sum + char.charCodeAt(0), 0)) % palette.length];
    const content = esc(message.content).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noreferrer">$1</a>');
    const previews = messageUrls(message.content).map((url) => linkPreviewCard(message, url)).join("");
    const pollId = pollIdFromMessage(message), poll = pollId && state.polls.find((entry) => entry.poll_id === pollId);
    return html + `<article class="message"><div class="message-avatar" style="background:${color}">${esc(initials(name))}</div><div class="message-main"><div class="message-meta"><span class="message-name">${esc(name)}</span><time class="message-time">${new Date(message.created_at).toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit" })}</time></div><p class="message-text">${content}</p>${pollId ? pollCard(poll) : ""}${previews}</div></article>`;
  }).join("");
  root.scrollTop = root.scrollHeight;
}
function prettySize(value = 0) { if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(0)} KB`; return `${(value / 1048576).toFixed(1)} MB`; }
function iconFor(item) { return item.category === "LINK" ? "↗" : item.mime_type?.startsWith("image/") ? "▧" : item.mime_type === "application/pdf" ? "PDF" : "▤"; }
function filteredMedia() { return state.filter === "ALL" ? state.media : state.media.filter((item) => item.category === state.filter); }
function renderMedia() {
  const root = $("#mediaGrid"), items = filteredMedia();
  if (!state.channel) { root.innerHTML = '<div class="media-empty"><span>▧</span><p>Selecciona un canal para ver sus archivos.</p></div>'; return; }
  if (!items.length) { root.innerHTML = `<div class="media-empty"><span>${state.filter === "LINK" ? "↗" : "▧"}</span><p>${state.filter === "ALL" ? "Los archivos y enlaces compartidos aparecerán aquí." : "Todavía no hay elementos en esta categoría."}</p></div>`; return; }
  root.innerHTML = items.map((item) => {
    if (item.category === "LINK") return `<article class="media-card"><a class="media-thumb link" href="${esc(item.url)}" target="_blank" rel="noreferrer">${item.preview?.image_url ? `<img src="${esc(item.preview.image_url)}" alt="">` : iconFor(item)}</a><div class="media-info"><strong title="${esc(item.preview?.title || item.url)}">${esc(item.preview?.title || new URL(item.url).hostname)}</strong><span>Enlace · ${esc(userById(item.uploader_id || "").display_name || "Vista previa")}</span></div><div class="media-card-actions"><button data-url="${esc(item.url)}" class="view-link">Ver preview</button></div></article>`;
    const thumb = item.mime_type?.startsWith("image/") ? `<img data-image="${esc(item.id)}" alt="${esc(item.filename)}">` : iconFor(item);
    return `<article class="media-card"><div class="media-thumb ${item.mime_type === "application/pdf" ? "pdf" : ""}" data-open="${esc(item.id)}">${thumb}</div><div class="media-info"><strong title="${esc(item.filename)}">${esc(item.filename)}</strong><span>${prettySize(item.size_bytes)} · ${esc(item.category)}</span></div><div class="media-card-actions"><button data-download="${esc(item.id)}">Descargar</button>${item.uploader_id === state.user ? `<button class="delete-file" data-delete="${esc(item.id)}">Borrar</button>` : ""}</div></article>`;
  }).join("");
  items.filter((x) => x.category !== "LINK" && x.mime_type?.startsWith("image/")).forEach(async (item) => {
    try { const response = await fetch(`${API.gateway}/api/v1/media/attachments/${item.id}/download`, { headers: auth() }); if (!response.ok) return; const img = document.querySelector(`[data-image="${CSS.escape(item.id)}"]`); if (img) img.src = URL.createObjectURL(await response.blob()); } catch { /* Keep file icon if preview is unavailable. */ }
  });
}
function renderQuota(data = null) {
  if (!data) { $("#quotaLabel").textContent = "—"; $("#quotaCaption").textContent = "Selecciona un canal para ver la cuota"; $("#quotaBar").style.width = "0%"; return; }
  const used = data.used_bytes || 0, limit = data.limit_bytes || 1, pct = Math.min(100, used / limit * 100);
  $("#quotaLabel").textContent = `${prettySize(used)} / ${prettySize(limit)}`; $("#quotaCaption").textContent = `${prettySize(data.available_bytes)} disponibles · ${data.file_count} archivos`;
  $("#quotaBar").style.width = `${pct}%`; $("#quotaBar").style.background = pct > 90 ? "#e78878" : "";
}
async function loadAttachments() {
  const items = []; let offset = 0;
  while (true) {
    const data = await graphql(`query($channel: ID!, $user: ID!, $input: GalleryInput) {
      listAttachments(channel_id: $channel, user_id: $user, input: $input) {
        items { id channel_id category created_at uploader_id filename mime_type size_bytes url preview { url final_url title description } }
        limit offset
      }
    }`, { channel: state.channel, user: state.user, input: { limit: 100, offset } });
    const rows = data.listAttachments?.items || [];
    items.push(...rows);
    if (rows.length < 100) return items;
    offset += rows.length;
  }
}
async function loadLinkJobs() {
  const items = []; let offset = 0;
  while (true) {
    const page = await api(API.gateway, `/api/v1/media/links/jobs?channel_id=${encodeURIComponent(state.channel)}&limit=100&offset=${offset}`, { headers: auth() });
    items.push(...page);
    if (page.length < 100) return items;
    offset += page.length;
  }
}
async function loadChannelData() {
  if (!state.channel || !state.user) return;
  try {
    const [messageData, media, quotaData, summaryData] = await Promise.all([
      graphql(`query($channel: ID!) { listMessages(channel_id: $channel) { id channel_id author_id content created_at } }`, { channel: state.channel }),
      loadAttachments(),
      graphql(`query($channel: ID!, $user: ID!) { getQuota(channel_id: $channel, user_id: $user) { channel_id used_bytes limit_bytes available_bytes file_count max_files max_file_bytes } }`, { channel: state.channel, user: state.user }),
      graphql(`query($channel: ID!) { listSummaries(channel_id: $channel, limit: 100, offset: 0) { id channel_id requested_by messageState timeInterval { start end } content structured { overview decisions events { description when } tasks { description assignee } } error created_at updated_at } }`, { channel: state.channel }).catch(() => ({ listSummaries: [] }))
    ]);
    const jobs = await loadLinkJobs().catch(() => []);
    const messages = messageData.listMessages || [], quota = quotaData.getQuota;
    const pollIds = [...new Set(messages.map(pollIdFromMessage).filter(Boolean))];
    const polls = await Promise.all(pollIds.map(async (id) => {
      try { return (await graphql(`query($id: ID!) { getPoll(id: $id) { poll_id channel_id author_id question options { option_id position text vote_count } status duration_minutes closes_at created_at closed_at total_votes } }`, { id })).getPoll; }
      catch { return null; }
    }));
    state.messages = messages; state.media = media; state.jobs = jobs; state.summaries = summaryData.listSummaries || []; state.polls = polls.filter(Boolean); renderMessages(); renderMedia(); renderQuota(quota);
  } catch (error) { toast(error.message, true); }
}
async function selectChannel(id) {
  state.channel = id; localStorage.setItem("media-channel", id); renderChannels(); renderMessages(); renderMedia(); renderQuota(); await loadChannelData();
}
async function loadContext() {
  try {
    const [userData, channelData] = await Promise.all([
      graphql(`query { listUsers { id display_name } }`),
      graphql(`query { listChannels { id name } }`)
    ]);
    const users = userData.listUsers || [], channels = channelData.listChannels || [];
    state.users = users; state.channels = channels; renderUsers();
    const selected = state.channels.some((x) => x.id === state.channel) ? state.channel : state.channels[0]?.id || "";
    if (selected) await selectChannel(selected); else { renderChannels(); renderMessages(); renderMedia(); }
  } catch (error) { toast(`No pude conectar con Users o Channels: ${error.message}`, true); }
}
function openModal({ title, hint, placeholder, submitLabel, onSubmit }) {
  const root = $("#modalRoot"); root.innerHTML = `<div class="modal-backdrop"><form class="modal"><h2>${esc(title)}</h2><p>${esc(hint)}</p><input required maxlength="80" placeholder="${esc(placeholder)}" autofocus><div class="modal-actions"><button type="button" class="cancel">Cancelar</button><button class="primary" type="submit">${esc(submitLabel)}</button></div></form></div>`;
  const backdrop = root.querySelector(".modal-backdrop"), form = root.querySelector("form"); form.querySelector("input").focus();
  root.querySelector(".cancel").onclick = () => root.replaceChildren(); backdrop.onclick = (event) => { if (event.target === backdrop) root.replaceChildren(); };
  form.onsubmit = async (event) => { event.preventDefault(); const value = form.querySelector("input").value.trim(); if (!value) return; try { await onSubmit(value); root.replaceChildren(); } catch (error) { toast(error.message, true); } };
}
function createPoll() {
  if (!state.channel) return;
  const root = $("#modalRoot");
  root.innerHTML = `<div class="modal-backdrop"><form class="modal poll-modal"><h2>Crear una encuesta</h2><p>La encuesta se anunciará en el canal y las respuestas quedarán visibles en el chat.</p><label>Pregunta<textarea name="question" required maxlength="240" placeholder="¿Qué decisión tomamos?"></textarea></label><label>Alternativas <small>(una por línea, mínimo 2)</small><textarea name="options" required placeholder="Opción A&#10;Opción B"></textarea></label><label>Duración <select name="duration"><option value="5">5 minutos</option><option value="15">15 minutos</option><option value="60">1 hora</option><option value="1440">24 horas</option></select></label><div class="modal-actions"><button type="button" class="cancel">Cancelar</button><button class="primary" type="submit">Publicar encuesta</button></div></form></div>`;
  const backdrop = root.querySelector(".modal-backdrop"), form = root.querySelector("form"); form.querySelector('[name="question"]').focus();
  root.querySelector(".cancel").onclick = () => root.replaceChildren(); backdrop.onclick = (event) => { if (event.target === backdrop) root.replaceChildren(); };
  form.onsubmit = async (event) => {
    event.preventDefault();
    const question = form.elements.question.value.trim(), options = form.elements.options.value.split("\n").map((value) => value.trim()).filter(Boolean), duration_minutes = Number(form.elements.duration.value);
    if (options.length < 2 || options.length > 10) { toast("Agrega entre 2 y 10 alternativas", true); return; }
    if (new Set(options.map((value) => value.toLocaleLowerCase())).size !== options.length) { toast("Las alternativas no pueden repetirse", true); return; }
    try {
      await graphql(`mutation($input: PollInput!, $key: String!) { createPoll(input: $input, idempotency_key: $key) { poll_id status } }`, {
        input: { channel_id: state.channel, author_id: state.user, question, options, duration_minutes },
        key: crypto.randomUUID()
      });
      root.replaceChildren(); toast("Encuesta publicada en el canal"); await loadChannelData();
    } catch (error) { toast(error.message, true); }
  };
}
async function requestSummary() {
  if (!state.channel) return;
  const button = $("#requestSummaryButton"); button.disabled = true;
  try {
    await graphql(`mutation($channel: ID!, $input: SummaryInput) { requestSummary(channel_id: $channel, input: $input) { summary_id messageState } }`, {
      channel: state.channel, input: { user_id: state.user }
    });
    toast("Resumen solicitado; aparecerá en el chat cuando esté listo"); await loadChannelData();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}
async function votePoll(pollId, optionId) {
  try {
    await graphql(`mutation($id: ID!, $input: VoteInput!) { votePoll(id: $id, input: $input) { poll_id } }`, {
      id: pollId, input: { voter_id: state.user, option_id: optionId }
    });
    localStorage.setItem(`media-voted:${pollId}:${state.user}`, "1"); toast("Tu voto quedó registrado"); await loadChannelData();
  } catch (error) { toast(error.message, true); await loadChannelData(); }
}
async function closePoll(pollId) {
  if (!confirm("¿Cerrar esta encuesta y publicar sus resultados?")) return;
  try { await graphql(`mutation($id: ID!, $input: ClosePollInput!) { closePoll(id: $id, input: $input) { poll_id status } }`, { id: pollId, input: { author_id: state.user } }); toast("Encuesta cerrada"); await loadChannelData(); }
  catch (error) { toast(error.message, true); }
}
function createChannel() { openModal({ title: "Crea un canal", hint: "Dale un nombre que ayude al equipo a encontrar la conversación.", placeholder: "ej. diseño-producto", submitLabel: "Crear canal", onSubmit: async (name) => { const data = await graphql(`mutation($input: ChannelInput!) { createChannel(input: $input) { id name } }`, { input: { name } }); const channel = data.createChannel; state.channels.push(channel); renderChannels(); await selectChannel(channel.id); toast(`Canal #${channel.name} creado`); } }); }
function createUser() { openModal({ title: "Añade un perfil de prueba", hint: "El perfil aparecerá en el selector de usuario de la barra lateral.", placeholder: "Tu nombre", submitLabel: "Crear perfil", onSubmit: async (display_name) => { const data = await graphql(`mutation($input: UserInput!) { createUser(input: $input) { id display_name } }`, { input: { display_name } }); const user = data.createUser; state.users.push(user); renderUsers(); $("#userSelect").value = user.id; setUser(user.id); await loadChannelData(); toast(`Listo, ${display_name}`); } }); }
async function sendMessage() {
  const input = $("#messageText"), content = input.value.trim(); if (!content || !state.channel) return;
  $("#sendButton").disabled = true;
  try { await graphql(`mutation($channel: ID!, $input: MessageInput!) { sendMessage(channel_id: $channel, input: $input) { id } }`, { channel: state.channel, input: { author_id: state.user, content } }); input.value = ""; input.style.height = "56px"; await loadChannelData(); }
  catch (error) { toast(error.message, true); } finally { $("#sendButton").disabled = false; input.focus(); }
}
async function uploadFile(file) {
  if (!file || !state.channel) { if (!state.channel) toast("Selecciona un canal primero", true); return; }
  const form = new FormData(); form.append("file", file);
  try { await api(API.gateway, `/api/v1/media/channels/${state.channel}/attachments`, { method: "POST", headers: auth(), body: form }); toast(`${file.name} se compartió en #${state.channels.find((x) => x.id === state.channel)?.name}`); await loadChannelData(); }
  catch (error) { toast(error.message, true); }
}
async function downloadFile(id) {
  try { const item = state.media.find((x) => x.id === id); const response = await fetch(`${API.gateway}/api/v1/media/attachments/${id}/download`, { headers: auth() }); if (!response.ok) throw new Error("No se pudo descargar el archivo"); const url = URL.createObjectURL(await response.blob()); const a = document.createElement("a"); a.href = url; a.download = item.filename; a.click(); URL.revokeObjectURL(url); }
  catch (error) { toast(error.message, true); }
}
async function deleteFile(id) {
  const item = state.media.find((x) => x.id === id); if (!item || !confirm(`¿Borrar ${item.filename}?`)) return;
  try { await api(API.gateway, `/api/v1/media/attachments/${id}`, { method: "DELETE", headers: auth() }); toast("Archivo eliminado; cuota liberada"); await loadChannelData(); }
  catch (error) { toast(error.message, true); }
}

$("#userSelect").addEventListener("change", async (event) => { setUser(event.target.value); await loadChannelData(); });
$("#mobileChannel").addEventListener("change", (event) => selectChannel(event.target.value));
$("#mobileProfile").onclick = async () => { if (!state.users.length) return; const current = state.users.findIndex((x) => x.id === state.user); const next = state.users[(current + 1) % state.users.length]; $("#userSelect").value = next.id; setUser(next.id); await loadChannelData(); toast(`Viendo como ${next.display_name}`); };
$("#addChannel").onclick = $("#emptyChannel").onclick = $("#newChannel").onclick = createChannel;
$("#addUser").onclick = createUser;
$("#createPollButton").onclick = createPoll;
$("#requestSummaryButton").onclick = requestSummary;
$("#composer").addEventListener("submit", (event) => { event.preventDefault(); sendMessage(); });
$("#messageText").addEventListener("input", (event) => { event.target.style.height = "56px"; event.target.style.height = `${Math.min(event.target.scrollHeight, 150)}px`; });
$("#messageText").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); } });
$("#attachButton").onclick = $("#uploadWide").onclick = () => $("#fileInput").click();
$("#fileInput").addEventListener("change", (event) => { uploadFile(event.target.files[0]); event.target.value = ""; });
$("#detailsToggle").onclick = () => $("#detailsPanel").classList.toggle("open");
$("#closeDetails").onclick = () => $("#detailsPanel").classList.remove("open");
$("#showMedia").onclick = () => { $("#detailsPanel").classList.add("open"); document.querySelectorAll(".rail-button").forEach((x) => x.classList.remove("active")); $("#showMedia").classList.add("active"); };
document.querySelectorAll(".filter-tab").forEach((button) => button.addEventListener("click", () => { document.querySelectorAll(".filter-tab").forEach((x) => x.classList.remove("active")); button.classList.add("active"); state.filter = button.dataset.filter; renderMedia(); }));
$("#mediaGrid").addEventListener("click", (event) => { const download = event.target.closest("[data-download]"); if (download) downloadFile(download.dataset.download); const del = event.target.closest("[data-delete]"); if (del) deleteFile(del.dataset.delete); const open = event.target.closest("[data-open]"); if (open) downloadFile(open.dataset.open); const link = event.target.closest(".view-link"); if (link) { window.open(link.dataset.url, "_blank", "noopener,noreferrer"); } });
$("#messages").addEventListener("click", (event) => { const download = event.target.closest("[data-download]"); if (download) downloadFile(download.dataset.download); const vote = event.target.closest("[data-option]"); if (vote) votePoll(vote.dataset.poll, vote.dataset.option); const close = event.target.closest("[data-close-poll]"); if (close) closePoll(close.dataset.closePoll); });
$("#search").addEventListener("input", (event) => { state.search = event.target.value.trim(); renderMessages(); });
document.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#search").focus(); } });

loadContext();
setInterval(() => { if (state.channel && !document.hidden) loadChannelData(); }, 5000);
