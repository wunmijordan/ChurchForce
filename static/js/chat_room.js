document.addEventListener("DOMContentLoaded", () => {

  // ─── DOM refs ────────────────────────────────────────────────
  const chatContainer     = document.getElementById("chatMessagesContainer");
  const stickyDateHeader  = document.getElementById("stickyDateHeader");
  const chatInput         = document.getElementById("chatInput");
  const sendButton        = document.getElementById("sendButton");
  const replyPreview      = document.getElementById("replyPreview");
  const replyPreviewText  = document.getElementById("replyPreviewText");
  const cancelReply       = document.getElementById("cancelReply");
  const openGuestBtn      = document.getElementById("openUserGuestPopup");
  const guestPopup        = document.getElementById("userGuestPopup");
  const popupBody         = document.getElementById("popupBody");
  const popupSearch       = document.getElementById("popupSearch");
  const popupClose        = document.getElementById("popupClose");
  const optionsPanel      = document.getElementById("chatOptionsPanel");
  const scrollToBottomBtn = document.getElementById("scrollToBottomBtn");
  const mentionDropdown   = document.getElementById("mentionDropdown");
  const wrapper           = document.getElementById("activeUserListWrapper");
  const inner             = document.getElementById("activeUserList");
  const pinnedPreviewEl   = document.getElementById("pinnedPreview");
  const chatInputFooter   = document.getElementById("chatInputFooter");

  if (!chatContainer) return;

  // ─── Template globals ────────────────────────────────────────
  const _USERS         = (typeof USERS            !== "undefined" ? USERS            : []);
  const _USER_GUESTS   = (typeof USER_GUESTS       !== "undefined" ? USER_GUESTS       : []);
  const _UNASSIGNED    = (typeof UNASSIGNED_GUESTS !== "undefined" ? UNASSIGNED_GUESTS : []);
  const _LAST_MSGS     = (typeof LAST_MESSAGES     !== "undefined" ? LAST_MESSAGES     : []);
  const _CUR_USER      = (typeof CURRENT_USER_ID   !== "undefined" ? CURRENT_USER_ID   : null);
  const _CUR_ROOM      = (typeof CURRENT_ROOM_ID   !== "undefined" ? String(CURRENT_ROOM_ID)   : null);
  const _CHAT_ROOMS    = (typeof CHAT_ROOMS        !== "undefined" ? CHAT_ROOMS        : []);
  const _ALL_MEMBERS   = (typeof ALL_MEMBERS       !== "undefined" ? ALL_MEMBERS       : []);
  const _CHURCH_NAME   = (typeof CHURCH_NAME       !== "undefined" ? CHURCH_NAME       : "ChurchForce");
  const _CHURCH_SLOGAN = (typeof CHURCH_SLOGAN     !== "undefined" ? CHURCH_SLOGAN     : "");
  const _TIME_FORMAT   = (typeof CHURCH_TIME_FORMAT !== "undefined" ? CHURCH_TIME_FORMAT : "");
  const _DATE_FORMAT   = (typeof CHURCH_DATE_FORMAT !== "undefined" ? CHURCH_DATE_FORMAT : "");
  const _IS_ADMIN      = !!(typeof IS_ADMIN !== "undefined" && IS_ADMIN) || !!(window.APP_CONFIG?.user?.isAdmin);
  const _CAN_GUESTS    = !!(typeof CAN_VIEW_GUESTS !== "undefined" && CAN_VIEW_GUESTS) || !!(window.APP_CONFIG?.user?.canViewGuests);
  const _CAN_MANAGE_G  = !!(typeof CAN_MANAGE_GUESTS !== "undefined" && CAN_MANAGE_GUESTS) || !!(window.APP_CONFIG?.user?.canManageGuests);
  const _USER_IS_PRIV  = (typeof USER_IS_PRIVILEGED !== "undefined" ? USER_IS_PRIVILEGED : false);
  const _ROOM_SEND_PERMS = (typeof ROOM_SEND_PERMISSIONS !== "undefined" ? ROOM_SEND_PERMISSIONS : {});
  const _ROOM_PRIV       = (typeof ROOM_PRIVILEGED !== "undefined" ? ROOM_PRIVILEGED : {});
  const CHAT_PLAN_SENTINEL = "%%PLAN_CHAT%%";

  const NORMALIZED_CHAT_ROOMS = (_CHAT_ROOMS || []).map(r => ({
    ...r,
    id: String(r.id),
    // Sub-rooms with no parent belong to the general room — normalise to "__general__"
    // so showSubRoomModal's parentKey filter always matches.
    parent_room_id: r.parent_room_id != null
      ? String(r.parent_room_id)
      : (r.room_type === "project" ? "__general__" : null),
    members: Array.isArray(r.members) ? r.members : [],
  }));

  const NORMALIZED_ALL_MEMBERS = Array.isArray(_ALL_MEMBERS)
    ? _ALL_MEMBERS
    : [];
  const DEFAULT_TIER_MEMBERS = (typeof CHAT_DEFAULT_TIER_MEMBERS !== "undefined" && Array.isArray(CHAT_DEFAULT_TIER_MEMBERS))
    ? CHAT_DEFAULT_TIER_MEMBERS
    : [];

  // Build a fast Set of user IDs that belong to a global/default tier
  const _GLOBAL_TIER_IDS = new Set(
    DEFAULT_TIER_MEMBERS.map(m => String(m.user_id || m.id || "")).filter(Boolean)
  );

  const isTouch = "ontouchstart" in window;

  const PIN_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="#f703d7ff" viewBox="0 0 16 16"><path d="M9.828.722a.5.5 0 0 1 .354.146l4.95 4.95a.5.5 0 0 1 0 .707c-.48.48-1.072.588-1.503.588-.177 0-.335-.018-.46-.039l-3.134 3.134a6 6 0 0 1 .16 1.013c.046.702-.032 1.687-.72 2.375a.5.5 0 0 1-.707 0l-2.829-2.828-3.182 3.182c-.195.195-1.219.902-1.414.707s.512-1.22.707-1.414l3.182-3.182-2.828-2.829a.5.5 0 0 1 0-.707c.688-.688 1.673-.767 2.375-.72a6 6 0 0 1 1.013.16l3.134-3.133a3 3 0 0 1-.04-.461c0-.43.108-1.022.589-1.503a.5.5 0 0 1 .353-.146"/></svg>`;

  // ─── State ───────────────────────────────────────────────────
  let activeRoomId   = _CUR_ROOM ? String(_CUR_ROOM) : null;
  // Tracks whether the user has explicitly switched rooms via a button click.
  // On initial load, CURRENT_ROOM_ID may be auto-selected by the server, but the
  // user hasn't chosen it — sub-room lookups should default to the General context.
  let _userActivatedRoom = false;
  let chatSocket     = null;
  let loading        = false;
  let oldestLoaded   = null;
  const LIMIT        = 50;
  let pinnedMessages = [];
  let selectedBubbles = new Set();
  let replyToId      = null;
  let selectedGuest  = null;
  let selectedFile   = null;
  let selectedLP     = null;
  let lpTimer        = null;
  let mentions       = [];
  let filteredUsers  = [];
  let mentionIdx     = 0;
  let stripVisible   = true;
  let autoHideTimer  = null;
  let isTouching     = false;
  let manualClose    = false;
  const UNREAD       = {};
  let _sendQueue     = [];
  const _PC_THREADS  = {};
  const _PC_UNREAD   = {};
  let _pcOpenId      = null;

  const EDIT_WINDOW_MS = 10 * 60 * 1000; // 10 minutes — shared constant

  const usersList = _USERS.map(u => ({
    id: u.id, full_name: u.full_name || u.username || "",
    title: u.title || "", username: u.username || "",
    image: u.image || null,
    color: (u.color && u.color.trim()) ? u.color : "#00aeff71",
  }));

  // ─── Utilities ───────────────────────────────────────────────
  function esc(s) { return String(s || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function htmlEsc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function safeDate(iso) {
    if (!iso) return null;
    const d = new Date(String(iso).replace(/\.\d+/, ""));
    return isNaN(d) ? null : d;
  }
  function dateLabel(d) {
    if (!d) return "";
    const now = new Date();
    const md = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yest = new Date(today); yest.setDate(today.getDate() - 1);
    if (md.getTime() === today.getTime()) return "Today";
    if (md.getTime() === yest.getTime()) return "Yesterday";
    const diff = Math.floor((today - md) / 86400000);
    if (diff < 7) return md.toLocaleDateString(undefined, { weekday: "long" });
    return fmtDateLabel(d.toISOString());
  }
  function fmtTime(iso) {
    const d = safeDate(iso); if (!d) return "";
    const fmt = (typeof CHURCH_TIME_FORMAT !== "undefined") ? CHURCH_TIME_FORMAT : "";
    if (fmt) {
      const pad = n => String(n).padStart(2, "0");
      let h = d.getHours(), m = d.getMinutes(), s = d.getSeconds();
      let ampm = h >= 12 ? "PM" : "AM";
      let h12 = h % 12 || 12;
      return fmt
        .replace("%H", pad(h)).replace("%I", pad(h12))
        .replace("%M", pad(m)).replace("%S", pad(s))
        .replace("%p", ampm).replace("%P", ampm.toLowerCase());
    }
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  function fmtDateLabel(iso) {
    const d = safeDate(iso); if (!d) return "";
    const fmt = (typeof CHURCH_DATE_FORMAT !== "undefined") ? CHURCH_DATE_FORMAT : "";
    if (fmt) {
      const pad = n => String(n).padStart(2, "0");
      const MONTHS_FULL = ["January","February","March","April","May","June","July","August","September","October","November","December"];
      const MONTHS_ABB  = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
      return fmt
        .replace("%d", pad(d.getDate())).replace("%-d", String(d.getDate()))
        .replace("%m", pad(d.getMonth()+1)).replace("%-m", String(d.getMonth()+1))
        .replace("%Y", d.getFullYear()).replace("%y", String(d.getFullYear()).slice(-2))
        .replace("%b", MONTHS_ABB[d.getMonth()]).replace("%B", MONTHS_FULL[d.getMonth()]);
    }
    return d.toLocaleDateString();
  }
  function fmtCopy(iso) {
    const d = new Date(iso);
    const mo = ["Jan.","Feb.","Mar.","Apr.","May","Jun.","Jul.","Aug.","Sept.","Oct.","Nov.","Dec."];
    return mo[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear() + " - " + fmtTime(iso);
  }
  function fmtGuestDate(s) {
    return s ? new Date(s).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
  }
  // Tabler "bible" SVG — used by formatMsg to render verse inserts
  const _BIBLE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="display:inline;vertical-align:-2px;margin-right:3px;flex-shrink:0;"><path d="M12 6.5a5.5 5.5 0 0 1 4-1.5h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-3.5"/><path d="M12 6.5a5.5 5.5 0 0 0-4-1.5H5a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h3.5"/><path d="M12 6.5V19"/><path d="M9 10h1"/><path d="M9 13h1"/></svg>';

  // ── Plain-text preview of a raw message (strips SVG/HTML from bible verses) ──
  function msgPlainPreview(rawMsg, maxLen) {
    if (!rawMsg) return "(Attachment)";

    // ── Plan discussion sentinel — render as readable plan card summary ──
    if (rawMsg.startsWith(CHAT_PLAN_SENTINEL)) {
      try {
        const rest    = rawMsg.slice(CHAT_PLAN_SENTINEL.length);
        const sepIdx  = rest.indexOf("\n\n");
        const metaRaw = sepIdx !== -1 ? rest.slice(0, sepIdx) : rest;
        const bodyTxt = sepIdx !== -1 ? rest.slice(sepIdx + 2).trim() : "";
        const meta    = JSON.parse(metaRaw);
        const ref     = meta.passage_reference || meta.ref || "";
        const plan    = meta.plan_title        || meta.title || "";
        const label   = meta.is_admin_post ? "started a Discourse" : "asked a Question";
        // e.g. "📖 Galatians 3 — Weekly Bible Reading Plan: What stood out to you?"
        const parts   = ["📖", ref, plan ? `— ${plan}` : ""].filter(Boolean).join(" ");
        const preview = bodyTxt ? `${parts}: ${bodyTxt}` : parts;
        return maxLen ? preview.slice(0, maxLen) : preview;
      } catch(_e) {
        return "📖 Bible Reading Plan";
      }
    }

    // Strip 📖 verse lines to "📖 Reference (TRANS)" — no raw HTML in preview
    let plain = rawMsg.replace(
      /^📖\s+(.+?)\s+\(\w+\):[\s\S]*/gm,
      (_, ref) => "📖 " + ref.trim()
    );
    // Strip any residual HTML tags (safety net)
    plain = plain.replace(/<[^>]+>/g, "");
    // Collapse whitespace
    plain = plain.replace(/\s+/g, " ").trim();
    return maxLen ? plain.slice(0, maxLen) : plain;
  }

  // ── Bible verse block renderer ───────────────────────────────────────────
  // Pattern: 📖 Reference (TRANS): "verse text"
  // Uses 📖 emoji — no SVG injection, clean and simple.
  // Handles multiple bible lines in one message.
  function renderBibleVerseBlock(line) {
    const m = line.match(/^📖\s+(.+?)\s+\((\w+)\):\s*[\u201c"\u201d]?(.+?)[\u201c"\u201d]?\s*$/s);
    if (!m) return null;
    const [, ref, trans, verse] = m;
    return (
      `<div class="bible-verse-block">` +
        `<div class="bible-verse-ref">` +
          `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 6.5a5.5 5.5 0 0 1 4-1.5h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-3.5"/>
            <path d="M12 6.5a5.5 5.5 0 0 0-4-1.5H5a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h3.5"/>
            <path d="M12 6.5V19"/><path d="M9 10h1"/><path d="M9 13h1"/>
          </svg>` +
          `${htmlEsc(ref)} <span class="bible-verse-trans">(${htmlEsc(trans)})</span>` +
        `</div>` +
        `<div class="bible-verse-text">${htmlEsc(verse.trim())}</div>` +
      `</div>`
    );
  }

  function formatMsg(text) {
    if (!text) return "";
    // Split into lines; try bible block on each 📖 line
    const lines = text.split("\n");
    const parts  = [];
    let hasBible = false;
    lines.forEach(line => {
      if (/^📖\s/.test(line)) {
        const block = renderBibleVerseBlock(line);
        if (block) { parts.push(block); hasBible = true; return; }
      }
      // Standard markdown on non-bible lines
      if (!line.trim()) { parts.push("<br>"); return; }
      let out = htmlEsc(line)
        .replace(/\*\*(.*?)\*\*/g, "<b>$1</b>")
        .replace(/\*(.*?)\*/g,     "<i>$1</i>")
        .replace(/_(.*?)_/g,       "<u>$1</u>")
        .replace(/==(.+?)==/g,     '<span class="highlight">$1</span>');
      parts.push(`<span>${out}</span>`);
    });
    if (hasBible && parts.every(p => p.startsWith("<div class=\"bible") || p === "<br>")) {
      return parts.join("");
    }
    return parts.join("<br>");
  }

  function linkifyText(text) {
    if (!text) return "";
    return text.replace(/\b(https?:\/\/[^\s]+|www\.[^\s]+)/gi, m => {
      let url = m; if (!/^https?:\/\//i.test(url)) url = "https://" + url;
      try { const h = new URL(url).hostname.replace(/^www\./, "");
            return `<a href="${url}" target="_blank" rel="noopener noreferrer" class="text-cyan text-decoration-none">${h}</a>`; }
      catch { return m; }
    });
  }
  function _diffHighlight(oldText, newText) {
    const oldW = oldText.split(/(\s+)/);
    const newW = newText.split(/(\s+)/);
    const R = oldW.length, C = newW.length;
    const dp = Array.from({length: R+1}, () => new Array(C+1).fill(0));
    for (let i=R-1;i>=0;i--) for(let j=C-1;j>=0;j--)
      dp[i][j] = oldW[i]===newW[j] ? 1+dp[i+1][j+1] : Math.max(dp[i+1][j],dp[i][j+1]);
    const parts = []; let i=0, j=0;
    while (j < C) {
      if (i < R && oldW[i] === newW[j]) { parts.push(newW[j]); i++; j++; }
      else { parts.push(`<mark class="edit-diff">${newW[j]}</mark>`); j++;
             if(i<R && dp[i][j]===dp[i+1]?.[j]) i++;
           }
    }
    return linkifyText(formatMsg(parts.join("")));
  }
  function renderWithMentions(msg, list) {
    if (!list || !list.length || !msg) return msg || "";
    let out = msg;
    list.forEach(m => {
      const tp = m.title ? m.title + " " : "", name = m.name || m.full_name || "", color = m.color || "#00aeffff";
      out = out.replace(new RegExp("@" + esc(tp + name), "gi"), `<span class="mention" style="color:${color};">@${tp}${name}</span>`);
    });
    return out;
  }
  function detectMentions(text) {
    if (!text) return [];
    const found = [];
    usersList.forEach(u => {
      const fn = esc(u.full_name || u.username), t = u.title ? esc(u.title) : "";
      const pat = t ? `@(?:${t}\\s+)?${fn}` : `@${fn}`;
      if (new RegExp(pat, "i").test(text) && !found.some(m => m.id === u.id))
        found.push({ id: u.id, title: u.title, name: u.full_name, color: u.color });
    });
    return found;
  }
  function popAnim(el) {
    el.animate([{ transform:"scale(1)", boxShadow:"0 0 0 rgba(0,255,120,0)" },
                { transform:"scale(1.18)", boxShadow:"0 0 14px rgba(0,255,120,0.6)" },
                { transform:"scale(1)", boxShadow:"0 0 0 rgba(0,255,120,0)" }], { duration:480, easing:"ease-out" });
    if (navigator.vibrate) navigator.vibrate(20);
  }
  function getCsrf() {
    // Prefer the template-injected token (always correct for this session/tenant)
    if (window.CF_CSRF) return window.CF_CSRF;
    return document.cookie.match(/csrftoken=([^;]+)/)?.[1]
      || document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
  }
  function workforceUrl(path) {
    // WORKFORCE_BASE is set inline in chat_room.html from a concrete Django-resolved
    // URL, so it always includes the church slug prefix on root-domain routing.
    const base = window.WORKFORCE_BASE
      || (document.getElementById("chatUrlSubRoom")?.dataset?.url || "/workforce/create_sub_room/")
           .replace(/create_sub_room\/?$/, "");
    return base + path.replace(/^\/+/, "");
  }

  let _floatingReactionPicker = null;

  function reactionSvgForChat(key, w, h) {
    const raw = (window.CHURCHFORCE_REACTION_MAP && window.CHURCHFORCE_REACTION_MAP[key]) || "";
    if (!raw) return "";
    return raw
      .replace(/width="20" height="20"/gi, `width="${w}" height="${h}"`)
      .replace(/width='20' height='20'/gi, `width='${w}' height='${h}'`);
  }

  // Reaction order defined in reactions.js — we respect that fixed order for the
  // summary display so the first-3 icons never shuffle as reactions change.
  const _REACTION_KEY_ORDER = (window.CHURCHFORCE_REACTIONS || []).map(r => r.key);

  function buildChatReactionSummaryHTML(summary, total, messageId) {
    const s = summary || {};
    const allEntries = Object.entries(s).filter(([, c]) => Number(c) > 0);
    const n = total != null ? Number(total) : allEntries.reduce((a, [, c]) => a + Number(c || 0), 0);
    if (!allEntries.length && !n) return "";

    // Sort by fixed canonical order (reactions.js order), then alphabetically for unknowns
    const sorted = allEntries.slice().sort(([a], [b]) => {
      const ia = _REACTION_KEY_ORDER.indexOf(a), ib = _REACTION_KEY_ORDER.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });

    const first3 = sorted.slice(0, 3);

    const icons = first3.map(([reaction]) => {
      const svg = reactionSvgForChat(reaction, 12, 12);
      // Fallback to reaction key text if SVG not yet loaded
      return `<span class="chat-reaction-icon-dot">${svg || reaction}</span>`;
    }).join("");

    return `<span class="chat-reaction-icons">${icons}</span><span class="chat-reaction-total">${n > 0 ? n : ""}</span>`;
  }

  function applyChatReactionSummaryToBubble(messageId, summary, total) {
    const host = document.querySelector(
      `.chat-reaction-summary[data-msg-id="${messageId}"]`
    );
    if (host) {
      host.innerHTML = buildChatReactionSummaryHTML(summary, total, messageId);
      // Store the full summary on the host for tray use
      host.dataset.reactionSummary = JSON.stringify(summary || {});
      host.dataset.reactionTotal = String(total || 0);
    }
  }

  // ─── Reaction summary pill click: combined counts + selectable picker ───────
  // Clicking .chat-reaction-summary opens a tray that shows current reaction
  // counts (read) AND lets the user pick/toggle their own reaction (write).
  // This is separate from the corner-button picker which shows no counts.
  let _reactionSummaryTray = null;
  let _reactionSummaryAnchor = null;

  function _closeSummaryTray() {
    _reactionSummaryTray?.remove();
    _reactionSummaryTray = null;
    _reactionSummaryAnchor = null;
  }

  function _openSummaryTray(summaryEl) {
    // Toggle
    if (_reactionSummaryTray && _reactionSummaryAnchor === summaryEl) {
      _closeSummaryTray(); return;
    }
    _closeSummaryTray();
    _reactionSummaryAnchor = summaryEl;

    const mid = summaryEl.dataset.msgId;
    if (!mid) return;

    let summary = {};
    try { summary = JSON.parse(summaryEl.dataset.reactionSummary || "{}"); } catch {}

    const countEntries = Object.entries(summary)
      .filter(([, c]) => Number(c) > 0)
      .sort(([a], [b]) => {
        const ia = _REACTION_KEY_ORDER.indexOf(a), ib = _REACTION_KEY_ORDER.indexOf(b);
        if (ia === -1 && ib === -1) return a.localeCompare(b);
        if (ia === -1) return 1; if (ib === -1) return -1;
        return ia - ib;
      });

    const tray = document.createElement("div");
    _reactionSummaryTray = tray;
    tray.style.cssText = [
      "position:fixed", "z-index:10902", "background:#0b1220",
      "border:1px solid #30363d", "border-radius:16px", "padding:10px",
      "display:flex", "flex-direction:column", "gap:8px", "max-width:300px",
      "box-shadow:0 8px 28px #000c",
    ].join(";");

    // ── Counts section (only if reactions exist) ──
    if (countEntries.length) {
      const countsRow = document.createElement("div");
      countsRow.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;";
      countEntries.forEach(([key, count]) => {
        const label = (window.CHURCHFORCE_REACTIONS || []).find(r => r.key === key)?.label || key;
        const svg = reactionSvgForChat(key, 15, 15);
        const chip = document.createElement("button");
        chip.type = "button";
        chip.title = label.toUpperCase();
        chip.style.cssText = [
          "display:flex", "align-items:center", "gap:4px",
          "background:#1f2937", "border:1px solid #30363d", "border-radius:20px",
          "padding:3px 9px", "cursor:pointer", "transition:border-color .15s,background .15s",
        ].join(";");
        chip.innerHTML = `${svg || ""}<span style="font-size:.72rem;color:#e2e8f0;">${label}</span><span style="font-size:.72rem;color:#94a3b8;font-weight:700;margin-left:2px;">${count}</span>`;
        chip.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          try {
            const results = await postChatReaction([mid], key);
            if (results?.[0]) {
              applyChatReactionSummaryToBubble(mid, results[0].reaction_summary, results[0].total_reactions);
              // Refresh tray with new data
              _closeSummaryTray();
              // Re-open with updated data from the just-updated host
              const updatedHost = document.querySelector(`.chat-reaction-summary[data-msg-id="${mid}"]`);
              if (updatedHost) _openSummaryTray(updatedHost);
            }
          } catch (err) { console.warn(err); }
        });
        countsRow.appendChild(chip);
      });
      tray.appendChild(countsRow);

      // Divider
      const div = document.createElement("hr");
      div.style.cssText = "border:none;border-top:1px solid #1e293b;margin:2px 0;";
      tray.appendChild(div);
    }

    // ── Selectable reaction buttons ──
    const reactRow = document.createElement("div");
    reactRow.style.cssText = "display:flex;flex-wrap:wrap;gap:4px;";
    (window.CHURCHFORCE_REACTIONS || []).forEach(({ key, label }) => {
      const b = document.createElement("button");
      b.type = "button";
      b.title = (label || key).toUpperCase();
      b.style.cssText = [
        "background:none", "border:none", "cursor:pointer",
        "display:flex", "align-items:center", "justify-content:center",
        "padding:3px", "border-radius:8px", "transition:transform .15s,background .12s",
      ].join(";");
      b.innerHTML = reactionSvgForChat(key, 20, 20) || key;
      b.addEventListener("mouseenter", () => { b.style.transform = "scale(1.25)"; b.style.background = "#1f2937"; });
      b.addEventListener("mouseleave", () => { b.style.transform = ""; b.style.background = "none"; });
      b.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        try {
          const results = await postChatReaction([mid], key);
          if (results?.[0]) {
            applyChatReactionSummaryToBubble(mid, results[0].reaction_summary, results[0].total_reactions);
            _closeSummaryTray();
            const updatedHost = document.querySelector(`.chat-reaction-summary[data-msg-id="${mid}"]`);
            if (updatedHost) _openSummaryTray(updatedHost);
          }
        } catch (err) { console.warn(err); }
      });
      reactRow.appendChild(b);
    });
    tray.appendChild(reactRow);

    document.body.appendChild(tray);

    requestAnimationFrame(() => {
      const aRect = summaryEl.getBoundingClientRect();
      const tw = tray.offsetWidth || 260;
      const th = tray.offsetHeight || 80;
      let left = Math.max(8, Math.min(window.innerWidth - tw - 8, aRect.left));
      let top = aRect.top - th - 8;
      if (top < 8) top = aRect.bottom + 8;
      tray.style.left = left + "px";
      tray.style.top = top + "px";
    });
  }

  document.addEventListener("click", (e) => {
    const summaryEl = e.target.closest(".chat-reaction-summary");
    if (summaryEl) {
      e.stopPropagation();
      _openSummaryTray(summaryEl);
      return;
    }
    // Close tray on outside click
    if (_reactionSummaryTray && !_reactionSummaryTray.contains(e.target)) {
      _closeSummaryTray();
    }
  });

  async function postChatReaction(messageIds, reactionKey) {
    const results = [];
    for (const mid of messageIds) {
      const url = workforceUrl(`message/${mid}/react/`);
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify({ reaction: reactionKey }),
      });
      if (!res.ok) throw new Error("react failed");
      const data = await res.json();
      results.push({ mid, ...data });
    }
    return results;
  }

  // Reaction picker: left-side for isMe bubbles, right-side for !isMe bubbles.
  // Titles are the reaction label in UPPERCASE.
  // Clicking the trigger again closes it (toggle). Clicking outside closes it.
  let _reactionPickerAnchor = null;

  function openChatReactionPicker(anchorEl, messageIds, isMe) {
    // Toggle: if picker is open for same anchor, close it
    if (_floatingReactionPicker && _reactionPickerAnchor === anchorEl) {
      _floatingReactionPicker.remove();
      _floatingReactionPicker = null;
      _reactionPickerAnchor = null;
      return;
    }
    _floatingReactionPicker?.remove();
    _reactionPickerAnchor = anchorEl;

    const pop = document.createElement("div");
    pop.id = "chatFloatingReactionPicker";
    pop.style.cssText = [
      "position:fixed",
      "z-index:10900",
      "background: #111827",
      "border-radius:24px",
      "padding:8px",
      "display:flex",
      "flex-wrap:wrap",
      "gap:6px",
      "max-width:300px",
      "box-shadow:0 0 8px #000000d2",
    ].join(";");

    (window.CHURCHFORCE_REACTIONS || []).forEach(({ key, label }) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-sm p-1 lh-1 chat-reaction-picker-btn";
      b.title = (label || key).toUpperCase();
      b.style.cssText = [
        "background:none",
        "border:none",
        "cursor:pointer",
        "display:flex",
        "align-items:center",
        "justify-content:center",
        "transition: transform .15s",
      ].join(";");

      b.innerHTML = reactionSvgForChat(key, 20, 20) || key;

      b.addEventListener("click", async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        try {
          const results = await postChatReaction(messageIds, key);
          // Immediately update the bubble from the HTTP response —
          // don't wait for the WS broadcast which may be delayed or missed.
          if (results && messageIds.length === 1) {
            const r = results[0];
            if (r) applyChatReactionSummaryToBubble(messageIds[0], r.reaction_summary, r.total_reactions);
          }
        } catch (err) {
          console.warn(err);
        }
        _floatingReactionPicker?.remove();
        _floatingReactionPicker = null;
        _reactionPickerAnchor = null;
      });

      pop.appendChild(b);
    });

    document.body.appendChild(pop);
    _floatingReactionPicker = pop;

    requestAnimationFrame(() => {
      const aRect = anchorEl.getBoundingClientRect();
      const pw = pop.offsetWidth || 260;
      const ph = pop.offsetHeight || 60;

      let left;
      if (isMe) {
        // isMe bubbles: picker opens to the LEFT of the anchor
        left = Math.max(8, aRect.left - pw - 8);
      } else {
        // !isMe bubbles: picker opens to the RIGHT of the anchor
        left = Math.min(window.innerWidth - pw - 8, aRect.right + 8);
      }

      const top = Math.max(
        8,
        Math.min(
          window.innerHeight - ph - 8,
          aRect.top + (aRect.height / 2) - (ph / 2)
        )
      );

      pop.style.left = left + "px";
      pop.style.top  = top  + "px";
    });
  } // end openChatReactionPicker

  // Close reaction picker when clicking outside
  document.addEventListener("click", (e) => {
    if (!_floatingReactionPicker) return;
    if (_floatingReactionPicker.contains(e.target)) return;
    if (_reactionPickerAnchor && _reactionPickerAnchor.contains(e.target)) return;
    _floatingReactionPicker.remove();
    _floatingReactionPicker = null;
    _reactionPickerAnchor = null;
  }, true);

  // ─── Custom styled modal helpers ─────────────────────────────
  function showConfirmModal({ title = "Are you sure?", body = "", confirmLabel = "Confirm", confirmClass = "bg-danger-lt", onConfirm }) {
    let m = document.getElementById("_cfConfirmModal");
    if (!m) {
      m = document.createElement("div");
      m.id = "_cfConfirmModal";
      m.className = "fullscreen-modal";
      document.body.appendChild(m);
    }
    m.style.display = "flex";
    m.innerHTML = `<div class="modal-box" style="max-width:380px;text-align:center;border-radius:36px;box-shadow:0 4px 8px #000000d2;">
      <div style="font-size:1.6rem;margin-bottom:8px;">⚠️</div>
      <h3 class="text-white fw-bold mb-2">${title}</h3>
      ${body ? `<div class="alert alert-warning py-2 fst-italic fw-bold mb-3" style="border-radius:24px;box-shadow:inset 0 4px 8px #000000d2;">${body}</div>` : ""}
      <div class="d-flex gap-2">
        <button data-cf-confirm class="btn ${confirmClass} flex-fill" style="box-shadow:0 -4px 8px #000000d2;border-radius:24px;">${confirmLabel}</button>
        <button data-cf-cancel class="btn bg-secondary-lt flex-fill" style="box-shadow:0 -4px 8px #000000d2;border-radius:24px;">Cancel</button>
      </div>
    </div>`;
    const close = () => { m.style.display = "none"; };
    m.querySelector("[data-cf-confirm]").onclick = () => { close(); onConfirm?.(); };
    m.querySelector("[data-cf-cancel]").onclick = close;
    m.onclick = e => { if (e.target === m) close(); };
  }

  function showPromptModal({ title = "Enter a value", defaultValue = "", placeholder = "", onConfirm }) {
    let m = document.getElementById("_cfPromptModal");
    if (!m) {
      m = document.createElement("div");
      m.id = "_cfPromptModal";
      m.className = "fullscreen-modal";
      document.body.appendChild(m);
    }
    m.style.display = "flex";
    m.innerHTML = `<div class="modal-box" style="max-width:380px;border-radius:36px;box-shadow:0 4px 8px #000000d2;">
      <h3 class="text-white fw-bold mb-3">${title}</h3>
      <input id="_cfPromptInput" class="form-control bg-dark text-white mb-3"
             style="box-shadow:inset 0 4px 8px #000000d2;border-radius:24px;"
             placeholder="${placeholder}" value="${(defaultValue||"").replace(/"/g,"&quot;")}">
      <div class="d-flex gap-2">
        <button data-cf-confirm class="btn bg-primary-lt flex-fill" style="box-shadow:0 -4px 8px #000000d2;border-radius:24px;">OK</button>
        <button data-cf-cancel class="btn bg-secondary-lt flex-fill" style="box-shadow:0 -4px 8px #000000d2;border-radius:24px;">Cancel</button>
      </div>
    </div>`;
    const input = m.querySelector("#_cfPromptInput");
    const close = () => { m.style.display = "none"; };
    requestAnimationFrame(() => { input?.focus(); input?.select(); });
    m.querySelector("[data-cf-confirm]").onclick = () => {
      const val = input?.value?.trim() || "";
      close();
      onConfirm?.(val);
    };
    m.querySelector("[data-cf-cancel]").onclick = close;
    m.onclick = e => { if (e.target === m) close(); };
    input?.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); m.querySelector("[data-cf-confirm]")?.click(); }
      if (e.key === "Escape") close();
    });
  }

  // ─── Banner ─────────────────────────────────────
  const banner = document.createElement("div");
  banner.id = "chatRoomBanner";
  banner.className = "chat-room-banner chat-room-banner-gradient";

  const bannerText = _CHURCH_SLOGAN
    ? `${_CHURCH_NAME} ChatRoom — ${_CHURCH_SLOGAN}`
    : `${_CHURCH_NAME} ChatRoom`;

  banner.innerHTML = `
    <div class="banner-inner">
      <span class="banner-text" id="bannerText">
        ${bannerText}
      </span>
    </div>
  `;

  const onlineBadge = document.createElement("span");
  onlineBadge.id = "onlineCountBadge";

  banner.appendChild(onlineBadge);

  const chatOverlay = document.getElementById("chatOverlay");

  if (chatOverlay && banner.parentElement !== chatOverlay) {
    chatOverlay.appendChild(banner);
  }

  if (chatOverlay && pinnedPreviewEl && pinnedPreviewEl.parentElement !== chatOverlay) {
    chatOverlay.appendChild(pinnedPreviewEl);
  }

  banner.addEventListener("click", () => {
    stripVisible = !stripVisible;
    if (stripVisible) {
      wrapper?.classList.add("visible");
      resetAutoHide();
    } else {
      wrapper?.classList.remove("visible");
      clearTimeout(autoHideTimer);
    }
    requestAnimationFrame(updateSubRoomTriggerPos);
  });

  function updateBanner(roomName, colorClass = "") {
    const slogan = _CHURCH_SLOGAN;
    const text = roomName
      ? `${roomName} ChatRoom`
      : (slogan ? `${_CHURCH_NAME} ChatRoom — ${slogan}` : `${_CHURCH_NAME} ChatRoom`);
    banner.className = `chat-room-banner chat-room-banner-gradient ${colorClass || ""}`;
    const bt = document.getElementById("bannerText");
    if (bt) {
      bt.textContent = text;
      bt.style.setProperty("--banner-speed", `${Math.max(6, Math.min(14, text.length / 3))}s`);
    }
  }

  function updateOnlineBadge() {
    const onlineCount = inner
      ? inner.querySelectorAll(".user-card[data-online='true']").length
      : 0;
    onlineBadge.textContent = onlineCount;
    onlineBadge.style.display = onlineCount > 0 ? "block" : "none";
  }

  // ─── User strip ──────────────────────────────────────────────
  function updateSubRoomTriggerPos() {
    const srBtn   = document.getElementById("subRoomTriggerBtn");
    const overlay = document.getElementById("chatOverlay");
    const stripH  = wrapper?.classList.contains("visible") ? (wrapper.offsetHeight || 72) : 0;
    const bannerH = banner ? (banner.offsetHeight || 28) : 28;
    const bannerTop  = stripH + 6;
    const pinnedTop  = bannerTop + bannerH + 4;

    if (overlay) {
      overlay.style.setProperty("--banner-top", bannerTop + "px");
      overlay.style.setProperty("--pinned-top", pinnedTop + "px");
    }

    if (!srBtn) return;
    srBtn.style.top = stripH > 0 ? (stripH + 4) + "px" : "0px";

    if (chatContainer) {
      chatContainer.style.paddingTop = (bannerH + stripH + 12) + "px";
    }
  }

  function resetAutoHide() {
    clearTimeout(autoHideTimer);
    if (!isTouching && !wrapper?.matches(":hover")) {
      autoHideTimer = setTimeout(() => {
        wrapper?.classList.remove("visible");
        stripVisible = false;
        updateSubRoomTriggerPos();
      }, 5000);
    }
  }
  if (wrapper) {
    wrapper.addEventListener("mouseenter", () => clearTimeout(autoHideTimer));
    wrapper.addEventListener("mouseleave", resetAutoHide);
    wrapper.addEventListener("touchstart", () => { isTouching = true; clearTimeout(autoHideTimer); }, { passive: true });
    wrapper.addEventListener("touchend",   () => { isTouching = false; resetAutoHide(); }, { passive: true });
  }

  // ─── buildUserStrip ─────────────────────────────────────────
  function buildUserStrip(roomId = null) {

    if (!inner) return;

    inner.innerHTML = "";

    const normalizedRoomId =
      roomId != null && roomId !== ""
        ? String(roomId)
        : null;

    let members = [];

    if (!normalizedRoomId) {
      members = NORMALIZED_ALL_MEMBERS.length ? NORMALIZED_ALL_MEMBERS : DEFAULT_TIER_MEMBERS;
    } else {
      const roomData = NORMALIZED_CHAT_ROOMS.find(r => r.id === normalizedRoomId);

      if (roomData && Array.isArray(roomData.members) && roomData.members.length) {
        members = roomData.members;
      } else if (roomData) {
        console.warn("[buildUserStrip] room", normalizedRoomId, "has 0 members — using chat default-tier members.");
        members = DEFAULT_TIER_MEMBERS;
      }

      // Always merge in global-tier members
      const memberIdSet = new Set(members.map(m => String(m.user_id || m.id || "")));
      DEFAULT_TIER_MEMBERS.forEach(tm => {
        const uid = String(tm.user_id || tm.id || "");
        if (uid && !memberIdSet.has(uid)) {
          members = [tm, ...members];
          memberIdSet.add(uid);
        }
      });
    }

    // Remove duplicates
    const seen = new Set();
    members = members.filter(m => {
      const id = String(m.user_id || m.id || "");
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });

    if (!members.length) {
      inner.innerHTML = `
        <div class="text-muted small px-3 py-2" style="white-space:nowrap;">
          No members in this room
        </div>
      `;
      updateOnlineBadge();
      return;
    }

    members.forEach(m => {
      const userId   = m.user_id || m.id || "";
      const memberId = m.member_id || m.id || "";
      const name     = m.full_name || m.username || "Unknown";
      const image    = m.image || m.avatar || null;
      const color    = (m.color && String(m.color).trim()) ? m.color : "#374151";
      const isOnline = !!m.is_online;
      const initials = name.split(" ").map(n => n[0] || "").slice(0, 2).join("").toUpperCase() || "?";

      const card = document.createElement("div");
      card.className = "user-card strip-card-layout";
      card.dataset.userId   = userId;
      card.dataset.memberId = memberId;
      card.dataset.online   = isOnline ? "true" : "false";

      const initials2  = m.initials || initials;
      const colorClass = (m.color_class && String(m.color_class).trim() && !m.color_class.startsWith("#")) ? m.color_class : "";
      card.innerHTML = `
        <div class="card shadow-sm border-0 mb-0 ${colorClass} user-card-inner private-chat-trigger"
            data-user-id="${userId}"
            data-member-id="${memberId}"
            data-name="${name.replace(/"/g, "&quot;")}"
            data-room-id="${normalizedRoomId || ""}"
            title="Chat with ${m.title || ""} ${name}">
          <div class="card-body py-2 px-3 d-flex align-items-center">
            <div class="me-2 position-relative" style="flex-shrink:0;">
              <a href="tel:${m.phone_number || ""}" onclick="event.stopPropagation()">
                ${image
                  ? `<span class="avatar rounded strip-avatar" style="background-image:url('${image}');"></span>`
                  : `<span class="avatar d-flex align-items-center justify-content-center rounded text-white fw-bold strip-avatar"
                          style="${color && color.startsWith("#") ? "background:"+color+";" : ""}">
                      ${initials2}
                    </span>`
                }
              </a>
              ${isOnline ? `<span class="online-badge position-absolute top-0 end-0 rounded-circle strip-online-dot"></span>` : ""}
              <span class="pc-unread-badge" data-for-user="${userId}" style="display:none;position:absolute;top:-4px;left:-4px;min-width:14px;height:14px;background:#ef4444;color:#fff;font-size:.55rem;font-weight:700;border-radius:9999px;align-items:center;justify-content:center;padding:0 3px;line-height:1;z-index:2;"></span>
            </div>
            <div class="flex-grow-1 overflow-hidden">
              <div class="fw-bold text-truncate strip-name-label" style="${color && color.startsWith("#") ? "color:"+color+";" : ""}">
                ${m.title || ""} ${name}
              </div>
            </div>
          </div>
        </div>
      `;

      card.querySelector(".private-chat-trigger")?.addEventListener("click", e => {
        e.preventDefault(); e.stopPropagation();
        e._pcHandled = true;
        if (String(userId) === String(_CUR_USER)) return;
        openPrivateChatModal(userId, memberId, name, normalizedRoomId || activeRoomId);
      });

      inner.appendChild(card);
      _pcBadgeUpdate(userId);
    });

    updateOnlineBadge();
  }

  // ─── Private chat ────────────────────────────────────────────
  function _pcKey(userId) { return String(userId); }

  function _pcBadgeUpdate(userId) {
    const n = _PC_UNREAD[_pcKey(userId)] || 0;
    document.querySelectorAll(`.pc-unread-badge[data-for-user="${userId}"]`).forEach(el => {
      el.textContent = n > 0 ? (n > 99 ? "99+" : n) : "";
      el.style.display = n > 0 ? "inline-flex" : "none";
    });
  }
  async function _pcLoadUnreadCounts() {
    try {
      const res = await fetch(workforceUrl("private/unread-counts/"));
      if (!res.ok) return;
      const data = await res.json();
      Object.keys(_PC_UNREAD).forEach(k => delete _PC_UNREAD[k]);
      Object.entries(data.counts || {}).forEach(([userId, count]) => {
        _PC_UNREAD[_pcKey(userId)] = Number(count) || 0;
        _pcBadgeUpdate(userId);
      });
    } catch (_) {}
  }

  function _pcEnsureThread(userId, name) {
    const key = _pcKey(userId);
    if (!_PC_THREADS[key]) _PC_THREADS[key] = { name: name || "", messages: [], seen: new Set() };
    if (!_PC_THREADS[key].seen) _PC_THREADS[key].seen = new Set();
    return _PC_THREADS[key];
  }

  function _pcRenderThread(msgsEl, userId) {
    const thread = _PC_THREADS[_pcKey(userId)];
    if (!thread) return;
    msgsEl.innerHTML = "";
    thread.messages.forEach(m => {
      const div = document.createElement("div");
      div.className = m.isMe ? "text-end my-1" : "text-start my-1";
      div.innerHTML = `<span class="badge px-3 py-2 rounded-3 text-white" style="max-width:85%;word-break:break-word;white-space:normal;background:${m.isMe ? "#166534" : "#374151"};">${m.text}</span>
        <div style="font-size:.6rem;color:#64748b;margin-top:1px;">${m.ts}</div>`;
      msgsEl.appendChild(div);
    });
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  function openPrivateChatModal(targetUserId, targetMemberId, name, roomId) {
    const modalEl = document.getElementById("privateChatModal");
    if (!modalEl) return;

    _pcOpenId = _pcKey(targetUserId);
    _PC_UNREAD[_pcOpenId] = 0;
    _pcBadgeUpdate(targetUserId);
    _pcEnsureThread(targetUserId, name);

    const nameEl = document.getElementById("pcName"); if (nameEl) nameEl.textContent = name || "Member";
    const subEl  = document.getElementById("pcSubtitle"); if (subEl) subEl.textContent = "Private · cross-room";
    const slot   = document.getElementById("pcAvatarSlot");
    if (slot) {
      const initials = (name || "?").split(" ").map(n => n[0] || "").slice(0,2).join("").toUpperCase();
      slot.innerHTML = `<span class="avatar d-flex align-items-center justify-content-center rounded" style="width:40px;height:40px;background:#374151;color:#fff;font-size:1rem;">${initials}</span>`;
    }

    const msgs = document.getElementById("pcMessages");
    if (msgs) {
      msgs.dataset.recipientUserId   = targetUserId || "";
      msgs.dataset.recipientMemberId = targetMemberId || "";
      msgs.dataset.recipientName     = name || "";
      _pcRenderThread(msgs, targetUserId);
      fetch(workforceUrl(`private/${targetUserId}/messages/`))
        .then(res => res.ok ? res.json() : { messages: [] })
        .then(data => {
          const thread = _pcEnsureThread(targetUserId, name);
          thread.messages = []; thread.seen = new Set();
          (data.messages || []).forEach(msg => {
            const msgKey = msg.id ? `db:${msg.id}` : `${msg.sender_id}:${msg.created_at}:${msg.message}`;
            if (thread.seen.has(msgKey)) return;
            thread.seen.add(msgKey);
            thread.messages.push({ id: msg.id, isMe: String(msg.sender_id) === String(_CUR_USER), text: msg.message || "", ts: fmtTime(msg.created_at) });
          });
          if (_pcOpenId === _pcKey(targetUserId)) _pcRenderThread(msgs, targetUserId);
        }).catch(() => {});
    }

    wsSend({ action: "private_request", requester_sender_id: _CUR_USER,
             recipient_member_id: parseInt(targetMemberId || targetUserId, 10) });

    let bsModal = bootstrap.Modal.getInstance(modalEl);
    if (!bsModal) bsModal = new bootstrap.Modal(modalEl, { backdrop: true, keyboard: true });

    modalEl.addEventListener("hidden.bs.modal", () => {
      _pcOpenId = null;
      document.body.classList.remove("modal-open");
      document.body.style.removeProperty("padding-right");
      document.body.style.removeProperty("overflow");
      document.querySelectorAll(".modal-backdrop").forEach(el => el.remove());
    }, { once: true });

    bsModal.show();
    requestAnimationFrame(() => document.getElementById("pcInput")?.focus());
  }

  document.addEventListener("click", e => {
    if (e._pcHandled) return;
    const trigger = e.target.closest(".private-chat-trigger");
    if (!trigger) return;
    e.preventDefault(); e.stopPropagation();
    const userId   = trigger.dataset.userId   || trigger.closest("[data-sender-id]")?.dataset?.senderId || "";
    const memberId = trigger.dataset.memberId || "";
    const name     = trigger.dataset.name || "";
    if (!userId || String(userId) === String(_CUR_USER)) return;
    openPrivateChatModal(userId, memberId, name, activeRoomId);
  });

  const _pcSendBtn = document.getElementById("pcSendBtn");
  if (_pcSendBtn && !_pcSendBtn.dataset.crHandled) {
    _pcSendBtn.dataset.crHandled = "1";
    _pcSendBtn.addEventListener("click", e => {
      e.preventDefault(); e.stopImmediatePropagation();
      const input     = document.getElementById("pcInput");
      const text      = input?.value?.trim();
      const msgs      = document.getElementById("pcMessages");
      const recipUser = msgs?.dataset?.recipientUserId;
      const recipMember = msgs?.dataset?.recipientMemberId;
      const rname     = msgs?.dataset?.recipientName || "";
      if (!text || !recipUser) return;
      const clientId = `pc-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      if (chatSocket && chatSocket.readyState === WebSocket.OPEN) {
        chatSocket.send(JSON.stringify({ action: "private_message", client_id: clientId, recipient_user_id: parseInt(recipUser, 10), recipient: parseInt(recipMember || recipUser, 10), message: text, sender_id: _CUR_USER, sender_name: window.APP_CONFIG?.user?.name || "", sender_title: window.APP_CONFIG?.user?.title || "", created_at: new Date().toISOString() }));
      } else {
        _sendQueue.push(JSON.stringify({ action: "private_message", client_id: clientId, recipient_user_id: parseInt(recipUser, 10), recipient: parseInt(recipMember || recipUser, 10), message: text, sender_id: _CUR_USER, sender_name: window.APP_CONFIG?.user?.name || "", sender_title: window.APP_CONFIG?.user?.title || "", created_at: new Date().toISOString() }));
      }
      const ts = fmtTime(new Date().toISOString());
      const thread = _pcEnsureThread(recipUser, rname);
      thread.seen.add(`client:${clientId}`);
      thread.messages.push({ client_id: clientId, isMe: true, text, ts });
      const div = document.createElement("div"); div.className = "text-end my-1";
      div.innerHTML = `<span class="badge px-3 py-2 rounded-3 text-white" style="max-width:85%;word-break:break-word;white-space:normal;background:#166534;">${text}</span>
        <div style="font-size:.6rem;color:#64748b;margin-top:1px;">${ts}</div>`;
      msgs.appendChild(div); msgs.scrollTop = msgs.scrollHeight;
      if (input) { input.value = ""; input.style.height = "auto"; }
    });
  }

  document.getElementById("pcInput")?.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      _pcSendBtn?.dispatchEvent(new MouseEvent("click", { bubbles: false, cancelable: true }));
    }
  });

  // ─── wsSend ──────────────────────────────────────────────────
  function wsSend(payload) {
    const json = JSON.stringify(payload);
    if (chatSocket && chatSocket.readyState === WebSocket.OPEN) {
      chatSocket.send(json);
    } else {
      _sendQueue.push(json);
    }
  }
  function flushQueue() {
    while (_sendQueue.length && chatSocket?.readyState === WebSocket.OPEN) {
      chatSocket.send(_sendQueue.shift());
    }
  }

  // ─── Room switching ──────────────────────────────────────────
  function showGeneralPanel() {
    document.querySelectorAll(".unit-users").forEach(el => el.classList.add("d-none"));
    document.getElementById("unit-users-general")?.classList.remove("d-none");
  }

  // ─── Auto-scroll + focus ────────────────────────────────────
  // Called after every room switch and initial page load, once messages are in the DOM.
  function scrollToBottomAndFocus() {
    requestAnimationFrame(() => {
      if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
      chatInput?.focus();
    });
  }

  function activateRoom(roomId, roomName, colorClass) {
    _userActivatedRoom = true;
    document.querySelectorAll(".unit-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".unit-users").forEach(el => el.classList.add("d-none"));
    if (roomId) {
      document.querySelectorAll(`.unit-btn[data-room-id="${roomId}"]`).forEach(b => b.classList.add("active"));
      document.getElementById("unit-users-" + roomId)?.classList.remove("d-none");
    } else {
      showGeneralPanel();
    }
    activeRoomId = roomId;
    restoreSubRoomTrigger();
    buildUserStrip(roomId);
    updateBanner(roomName || null, colorClass || null);
    if (wrapper) { wrapper.classList.add("visible"); stripVisible = true; resetAutoHide(); }
    requestAnimationFrame(updateSubRoomTriggerPos);
    reconnectSocket(roomId);
    clearChat();
    loadMoreMessages();
    scrollToBottomAndFocus();
  }

  document.querySelectorAll(".unit-btn[data-room-id]").forEach(btn => {
    btn.addEventListener("click", e => {
      e.preventDefault();
      const rid = String(btn.dataset.roomId);
      if (rid === String(activeRoomId)) {
        document.querySelectorAll(`.unit-btn[data-room-id="${rid}"]`).forEach(b => b.classList.remove("active"));
        activeRoomId = null;
        _userActivatedRoom = true;
        showGeneralPanel();
        updateBanner(null, null);
        restoreSubRoomTrigger();
        buildUserStrip(null);
        if (wrapper) { wrapper.classList.add("visible"); stripVisible = true; resetAutoHide(); }
        requestAnimationFrame(updateSubRoomTriggerPos);
        reconnectSocket(null);
        clearChat(); loadMoreMessages();
        scrollToBottomAndFocus();
        return;
      }
      const name = btn.dataset.roomName || btn.dataset.unitName || "";
      const cc   = btn.dataset.unitColorClass || btn.dataset.colorClass || "";
      activateRoom(rid, name, cc);
      closeMobileMenu();
    });
  });

  document.getElementById("chatroomsToggle")?.addEventListener("click", e => {
    e.preventDefault();
    const c = document.getElementById("chatroomsContainer");
    if (c) c.style.display = c.style.display === "none" ? "" : "none";
  });

  if (activeRoomId) {
    document.querySelectorAll(`.unit-btn[data-room-id="${activeRoomId}"]`).forEach(ab => {
      ab.classList.add("active");
    });
    document.getElementById("unit-users-" + activeRoomId)?.classList.remove("d-none");
  } else {
    showGeneralPanel();
  }
  buildUserStrip(activeRoomId);
  // Initialise banner for the page-load room (activateRoom is not called on fresh load)
  if (activeRoomId) {
    const initRoom = NORMALIZED_CHAT_ROOMS.find(r => r.id === String(activeRoomId));
    updateBanner(initRoom?.name || null, initRoom?.color_class || null);
  } else {
    updateBanner(null, null);  // general room banner (shows church name/slogan)
  }
  if (wrapper) { wrapper.classList.add("visible"); stripVisible = true; resetAutoHide(); }
  requestAnimationFrame(updateSubRoomTriggerPos);
  restoreSubRoomTrigger();

  function closeMobileMenu() {
    if (window.innerWidth > 767) return;
    const cc = document.getElementById("chatroomsContainer");
    if (cc) cc.style.display = "none";
  }
  function clearChat() {
    chatContainer.querySelectorAll(".chat-item, .chat-date-separator").forEach(el => el.remove());
    pinnedMessages = []; renderPinnedPreview(); oldestLoaded = null;
    loading = false;
  }
  document.querySelectorAll(".chat-back-btn").forEach(b => b.addEventListener("click", () => window.history.back()));

  // ─── Sub-room trigger + modal ────────────────────────────────
  const subRoomTriggerBtn = document.getElementById("subRoomTriggerBtn");

  function restoreSubRoomTrigger() {
    const oldBtn = document.getElementById("subRoomTriggerBtn");
    if (!oldBtn) return;
    const newBtn = oldBtn.cloneNode(true);
    oldBtn.parentNode?.replaceChild(newBtn, oldBtn);
    newBtn.style.display = "";
    newBtn.addEventListener("click", e => { e.stopPropagation(); showSubRoomModal(activeRoomId); });
  }

  subRoomTriggerBtn?.addEventListener("click", e => {
    e.stopPropagation();
    showSubRoomModal(activeRoomId);
  });

  function showSubRoomModal(parentRoomId) {
    // If the user hasn't explicitly chosen a room (auto-selected on load),
    // treat it as the General Workforce context regardless of what activeRoomId is.
    const effectiveParentId = _userActivatedRoom ? parentRoomId : null;
    const btn = effectiveParentId ? document.querySelector(`.unit-btn[data-room-id="${effectiveParentId}"]`) : null;
    const parentRoomName = btn?.dataset?.roomName || btn?.dataset?.unitName || (effectiveParentId ? "Room" : "General Workforce");
    const isPrivileged   = effectiveParentId
      ? (_ROOM_PRIV[String(effectiveParentId)] !== undefined ? _ROOM_PRIV[String(effectiveParentId)] : _USER_IS_PRIV)
      : _USER_IS_PRIV;
    const parentKey = effectiveParentId ? String(effectiveParentId) : "__general__";
    const subRooms  = NORMALIZED_CHAT_ROOMS.filter(r => String(r.parent_room_id || "") === parentKey);

    let modal = document.getElementById("subRoomModal");
    if (!modal) {
      modal = document.createElement("div"); modal.id = "subRoomModal"; modal.className = "fullscreen-modal";
      document.body.appendChild(modal);
    }
    modal.style.display = "flex";

    const subRoomRowsHTML = subRooms.map(r => `
      <div class="sr-room-row" data-sr-row-id="${r.id}" style="background:transparent;">
        <div class="d-flex align-items-center gap-2">
          <button class="btn btn-sm text-white text-start sr-open-btn flex-grow-1 p-0 border-0 bg-transparent"
              data-sr-id="${r.id}" data-sr-name="${r.name}"
              style="background:#111827;box-shadow:0 0 8px #000000d2;border-radius:24px;padding:6px 12px;margin-bottom:6px;font-size:.9rem;"># ${r.name}</button>
          ${isPrivileged ? `
            <button class="btn btn-sm btn-icon sr-members-btn p-1" title="Manage Members" data-sr-id="${r.id}" data-sr-name="${r.name}" style="background:none;border:none;color:#64748b;cursor:pointer;">
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-tabler icons-tabler-outline icon-tabler-user-cog">
                <path stroke="none" d="M0 0h24v24H0z" fill="none" />
                <path d="M8 7a4 4 0 1 0 8 0a4 4 0 0 0 -8 0" />
                <path d="M6 21v-2a4 4 0 0 1 4 -4h2.5" />
                <path d="M17.001 19a2 2 0 1 0 4 0a2 2 0 1 0 -4 0" />
                <path d="M19.001 15.5v1.5" />
                <path d="M19.001 21v1.5" />
                <path d="M22.032 17.25l-1.299 .75" />
                <path d="M17.27 20l-1.3 .75" />
                <path d="M15.97 17.25l1.3 .75" />
                <path d="M20.733 20l1.3 .75" />
              </svg>
            </button>
            <button class="btn btn-sm btn-icon sr-edit-btn p-1" title="Rename Sub-Room" data-sr-id="${r.id}" data-sr-name="${r.name}" style="background:none;border:none;color:#64748b;cursor:pointer;">
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"
                    fill="none" stroke="currentColor" stroke-width="2">
                <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
                <path d="M4 20h4l10.5-10.5a2.828 2.828 0 1 0-4-4l-10.5 10.5v4"/>
                <path d="M13.5 6.5l4 4"/>
              </svg>
            </button>
            <button class="btn btn-sm btn-icon sr-delete-btn p-1" title="Delete Sub-Room" data-sr-id="${r.id}" data-sr-name="${r.name}" style="background:none;border:none;color:#ef4444;cursor:pointer;">
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"
                    viewBox="0 0 24 24" fill="currentColor">
                <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
                <path d="M20 6a1 1 0 0 1 .117 1.993l-.117.007h-.081l-.919 11a3 3 0 0 1-2.824 2.995l-.176.005h-8c-1.598 0-2.904-1.249-2.992-2.75l-.005-.167-.923-11.083h-.08a1 1 0 0 1-.117-1.993l.117-.007zm-10 4a1 1 0 0 0-1 1v6a1 1 0 0 0 2 0v-6a1 1 0 0 0-1-1m4 0a1 1 0 0 0-1 1v6a1 1 0 0 0 2 0v-6a1 1 0 0 0-1-1"/>
                <path d="M14 2a2 2 0 0 1 2 2a1 1 0 0 1-1.993.117l-.007-.117h-4l-.007.117a1 1 0 0 1-1.993-.117a2 2 0 0 1 1.85-1.995l.15-.005z"/>
              </svg>
            </button>
          ` : ""}
        </div>
        <div class="sr-mgmt-panel" style="display:none;margin-top:8px;padding:10px 12px;background:#0b1220;border-radius:16px;box-shadow:inset 0 2px 6px #000000d2;"></div>
      </div>`).join("");

    modal.innerHTML = `<div class="modal-box" style="box-shadow:0 4px 8px #000000d2;border-radius:36px;max-height:85vh;overflow-y:auto;">
      <div class="d-flex align-items-center justify-content-between mb-3">
        <h3 class="text-white fw-bold mb-0">Sub-Rooms — ${parentRoomName}</h3>
        <button id="srClose" style="background:none;border:none;color:#94a3b8;cursor:pointer;font-size:1.2rem;">✕</button>
      </div>
      ${subRooms.length
        ? `<div class="mb-3"><div class="small text-muted mb-2">Available Sub-Rooms:</div>${subRoomRowsHTML}</div>`
        : '<div class="text-muted small mb-3">No Sub-Rooms yet.</div>'}
      ${isPrivileged ? `
        <div class="border-top border-secondary pt-3">
          <div class="small text-muted mb-2">Create New Sub-Room:</div>
          <input id="srName" class="form-control mb-2 bg-dark text-white" style="box-shadow:inset 0 4px 8px #000000d2;border-radius:24px;" placeholder="Sub-Room Name *">
          <input id="srDesc" class="form-control mb-2 bg-dark text-white" style="box-shadow:inset 0 4px 8px #000000d2;border-radius:24px;" placeholder="Description (optional)">
          <button id="srCreate" class="btn bg-green-lt w-100" style="box-shadow:0 -4px 8px #000000d2;border-radius:24px;">Create Sub-Room</button>
          <div id="srMsg" class="mt-2 small text-danger"></div>
        </div>` : ""}
    </div>`;

    modal.querySelector("#srClose").onclick = () => modal.style.display = "none";
    modal.onclick = e => { if (e.target === modal) modal.style.display = "none"; };

    modal.querySelectorAll(".sr-open-btn").forEach(b => {
      b.addEventListener("click", () => {
        modal.style.display = "none";
        activateSubRoom(b.dataset.srId, b.dataset.srName, parentRoomId);
      });
    });

    // ── Manage members ──────────────────────────────────────────
    modal.querySelectorAll(".sr-members-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const srId   = btn.dataset.srId;
        const srName = btn.dataset.srName;
        const row    = btn.closest(".sr-room-row");
        if (!row) return;
    
        const panel = row.querySelector(".sr-mgmt-panel");
        if (!panel) return;
    
        // Close any other open panels
        modal.querySelectorAll(".sr-mgmt-panel").forEach(p => {
          if (p !== panel) p.style.display = "none";
        });
    
        panel.style.display = panel.style.display === "block" ? "none" : "block";
        if (panel.style.display === "none") return;

        // Show loading state immediately
        panel.innerHTML = `<div style="font-size:.75rem;color:#64748b;padding:6px 0;">Loading members…</div>`;

        // --- Determine the correct member pool for this sub-room ---
        const ROOMS_LOCAL = (window.CHAT_ROOMS || NORMALIZED_CHAT_ROOMS || []).map(r => ({
          ...r, id: String(r.id),
          parent_room_id: r.parent_room_id != null ? String(r.parent_room_id) : null,
        }));
        const srEntry = ROOMS_LOCAL.find(r => r.id === String(srId));
        const parentId = srEntry?.parent_room_id ?? null;

        // Member pool: parent room's members (minus global), or ALL_MEMBERS for general rooms.
        // We must include previously-added members even if they were removed from the parent pool.
        let memberPool = [];
        if (parentId && parentId !== "__general__") {
          const parentEntry = ROOMS_LOCAL.find(r => r.id === String(parentId));
          memberPool = (parentEntry?.members && parentEntry.members.length)
            ? parentEntry.members
            : NORMALIZED_ALL_MEMBERS.length ? NORMALIZED_ALL_MEMBERS : DEFAULT_TIER_MEMBERS;
        } else {
          memberPool = NORMALIZED_ALL_MEMBERS.length ? NORMALIZED_ALL_MEMBERS : DEFAULT_TIER_MEMBERS;
        }

        const globalIds = new Set(
          (window.CHAT_DEFAULT_TIER_MEMBERS || DEFAULT_TIER_MEMBERS)
            .map(m => String(m.user_id ?? m.id)).filter(Boolean)
        );

        // Fetch current members from server
        let currentMembers = [];
        try {
          const res = await fetch(workforceUrl(`sub_room/${srId}/members/`));
          if (res.ok) {
            const d = await res.json();
            currentMembers = d.members || [];
          }
        } catch (_) {}

        // Also merge members already stored in NORMALIZED_CHAT_ROOMS for this sub-room
        // so previously-added members show up even without a round-trip
        const localEntry = NORMALIZED_CHAT_ROOMS.find(r => r.id === String(srId));
        if (localEntry && Array.isArray(localEntry.members)) {
          localEntry.members.forEach(lm => {
            const uid = String(lm.user_id ?? lm.id ?? "");
            if (uid && !currentMembers.find(x => String(x.user_id ?? x.id) === uid)) {
              currentMembers.push(lm);
            }
          });
        }

        const addedIds = new Set(currentMembers.map(m => String(m.user_id ?? m.id)));

        // Extend member pool with any previously-added members not already in pool
        // so they always appear in the pool for re-adding if removed
        currentMembers.forEach(cm => {
          const uid = String(cm.user_id ?? cm.id ?? "");
          if (uid && !memberPool.find(m => String(m.user_id ?? m.id) === uid)) {
            memberPool = [...memberPool, cm];
          }
        });

        panel.innerHTML = `
          <div style="margin-bottom:6px;font-size:.75rem;font-weight:600;color:#94a3b8;">
            Members of <span style="color:#fff;">#${srName}</span>
          </div>
          <div style="font-size:.68rem;color:#64748b;margin-bottom:4px;">
            Current members
            <span style="color:#475569;">(global-tier always present — not listed)</span>
          </div>
          <div class="_sr-current-list"
              style="display:flex;flex-direction:column;gap:4px;max-height:130px;overflow-y:auto;margin-bottom:10px;"></div>
          <div style="font-size:.68rem;color:#64748b;margin-bottom:4px;margin-top:8px;">
            Add from Room
          </div>
          <input class="_sr-add-search"
                placeholder="Search Member…"
                style="width:100%;background:#0f172a;box-shadow:inset 0 4px 8px #000000d2;border-radius:16px;color:#e2e8f0;font-size:.78rem;padding:5px 10px;margin-bottom:6px;box-sizing:border-box;">
          <div class="_sr-add-list"
              style="display:flex;flex-direction:column;gap:4px;max-height:140px;overflow-y:auto;"></div>
        `;

        function sortByName(a, b) {
          return (a.full_name || a.username || "").toLowerCase()
            .localeCompare((b.full_name || b.username || "").toLowerCase());
        }

        function renderCurrent() {
          const el = panel.querySelector("._sr-current-list");
          if (!el) return;
          el.innerHTML = "";
          const visible = [...currentMembers]
            .filter(m => {
              const uid = String(m.user_id ?? m.id);
              return addedIds.has(uid) && !globalIds.has(uid);
            })
            .sort(sortByName);

          if (!visible.length) {
            el.innerHTML = `<div style="font-size:.72rem;color:#475569;padding:4px 0;">No explicitly-added Members yet.</div>`;
            return;
          }

          visible.forEach(m => {
            const uid  = String(m.user_id ?? m.id);
            const name = m.full_name || m.username || "";
            const row2 = document.createElement("div");
            row2.style.cssText = "display:flex;align-items:center;justify-content:space-between;padding:5px 8px;border-radius:10px;background:#1e293b;";
            row2.innerHTML = `
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="width:26px;height:26px;border-radius:50%;background:${m.color || "#374151"};display:inline-flex;align-items:center;justify-content:center;font-size:.6rem;color:#fff;flex-shrink:0;">
                  ${name.slice(0,2).toUpperCase()}
                </span>
                <span style="font-size:.8rem;color:#e2e8f0;">${m.title || ""} ${name}</span>
              </div>
              <button style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:.9rem;line-height:1;padding:2px 6px;border-radius:8px;" title="Remove" data-uid="${uid}">✕</button>
            `;
            row2.querySelector("button")?.addEventListener("click", async () => {
              try {
                await fetch(workforceUrl(`sub_room/${srId}/remove_member/`), {
                  method: "POST",
                  headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
                  body: JSON.stringify({ user_id: uid }),
                });
              } catch (_) {}
              addedIds.delete(uid);
              currentMembers = currentMembers.filter(x => String(x.user_id ?? x.id) !== uid);
              // Update local NORMALIZED_CHAT_ROOMS
              const entry = NORMALIZED_CHAT_ROOMS.find(r => r.id === String(srId));
              if (entry) entry.members = (entry.members || []).filter(x => String(x.user_id ?? x.id) !== uid);
              // Update CHAT_ROOMS too
              const crEntry = (window.CHAT_ROOMS || []).find(r => String(r.id) === String(srId));
              if (crEntry) crEntry.members = (crEntry.members || []).filter(x => String(x.user_id ?? x.id) !== uid);
              // Refresh user strip if viewing this sub-room
              if (String(activeRoomId) === String(srId)) buildUserStrip(srId);
              renderCurrent();
              renderAdd(panel.querySelector("._sr-add-search")?.value || "");
            });
            el.appendChild(row2);
          });
        }

        function renderAdd(filter) {
          const el = panel.querySelector("._sr-add-list");
          if (!el) return;
          el.innerHTML = "";
          const filtered = memberPool
            .filter(m => {
              const uid  = String(m.user_id ?? m.id);
              const name = (m.full_name || m.username || "").toLowerCase();
              return name.includes(filter.toLowerCase()) && !addedIds.has(uid) && !globalIds.has(uid);
            })
            .sort(sortByName)
            .slice(0, 20);

          if (!filtered.length) {
            el.innerHTML = `<div style="font-size:.72rem;color:#475569;padding:4px 0;">No more Members to add.</div>`;
            return;
          }

          filtered.forEach(m => {
            const uid  = String(m.user_id ?? m.id);
            const name = m.full_name || m.username || "";
            const row2 = document.createElement("div");
            row2.style.cssText = "display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:10px;cursor:pointer;background:#111827;transition:background .15s;";
            row2.innerHTML = `
              <span style="width:26px;height:26px;border-radius:50%;background:${m.color||"#374151"};display:inline-flex;align-items:center;justify-content:center;font-size:.6rem;color:#fff;flex-shrink:0;">${name.slice(0,2).toUpperCase()}</span>
              <span style="font-size:.8rem;color:#e2e8f0;">${m.title||""} ${name}</span>
            `;
            row2.addEventListener("mouseenter", () => { row2.style.background = "#1e293b"; });
            row2.addEventListener("mouseleave", () => { row2.style.background = "#111827"; });
            row2.addEventListener("click", async () => {
              try {
                await fetch(workforceUrl(`sub_room/${srId}/add_member/`), {
                  method: "POST",
                  headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
                  body: JSON.stringify({ user_id: uid }),
                });
              } catch (_) {}
              addedIds.add(uid);
              if (!currentMembers.find(x => String(x.user_id ?? x.id) === uid)) currentMembers.push(m);
              // Update NORMALIZED_CHAT_ROOMS
              const entry = NORMALIZED_CHAT_ROOMS.find(r => r.id === String(srId));
              if (entry) {
                entry.members = entry.members || [];
                if (!entry.members.find(x => String(x.user_id ?? x.id) === uid)) entry.members.push(m);
              }
              // Update window.CHAT_ROOMS too
              const crEntry = (window.CHAT_ROOMS || []).find(r => String(r.id) === String(srId));
              if (crEntry) {
                crEntry.members = crEntry.members || [];
                if (!crEntry.members.find(x => String(x.user_id ?? x.id) === uid)) crEntry.members.push(m);
              }
              // Instantly update user strip if viewing this sub-room
              if (String(activeRoomId) === String(srId)) buildUserStrip(srId);
              renderCurrent();
              renderAdd(panel.querySelector("._sr-add-search")?.value || "");
            });
            el.appendChild(row2);
          });
        }

        renderCurrent();
        renderAdd("");
        panel.querySelector("._sr-add-search")?.addEventListener("input", e => renderAdd(e.target.value));
      });
    });

    // ── Edit (rename) ────────────────────────────────────────────
    modal.querySelectorAll(".sr-edit-btn").forEach(b => {
      b.addEventListener("click", e => {
        e.stopPropagation();
        const srId   = b.dataset.srId;
        const srName = b.dataset.srName;
        showPromptModal({
          title: `Rename sub-room`,
          defaultValue: srName,
          placeholder: "New name…",
          onConfirm: async (newName) => {
            if (!newName || newName.trim() === srName) return;
            try {
              const res = await fetch(workforceUrl(`sub_room/${srId}/edit/`), {
                method:"POST", headers:{"Content-Type":"application/json","X-CSRFToken":getCsrf()},
                body: JSON.stringify({ name: newName.trim() }),
              });
              if (res.ok) {
                const trimmed = newName.trim();
                // Update NORMALIZED_CHAT_ROOMS
                const entry = NORMALIZED_CHAT_ROOMS.find(r => r.id === String(srId));
                if (entry) entry.name = trimmed;
                // Update window.CHAT_ROOMS
                const crEntry = (window.CHAT_ROOMS || []).find(r => String(r.id) === String(srId));
                if (crEntry) crEntry.name = trimmed;
                // Update the button dataset and the row label
                b.dataset.srName = trimmed;
                const rowEl = b.closest(".sr-room-row");
                if (rowEl) {
                  rowEl.querySelector(".sr-open-btn").textContent = `# ${trimmed}`;
                  rowEl.querySelector(".sr-members-btn")?.setAttribute("data-sr-name", trimmed);
                  rowEl.querySelector(".sr-delete-btn")?.setAttribute("data-sr-name", trimmed);
                }
                // If currently in this sub-room, update the banner live
                if (String(activeRoomId) === String(srId)) {
                  const parentBtn = parentRoomId ? document.querySelector(`.unit-btn[data-room-id="${parentRoomId}"]`) : null;
                  const parentName = parentBtn?.dataset?.roomName || parentBtn?.dataset?.unitName || "General";
                  updateBanner(`${parentName} — ${trimmed}`);
                }
              }
            } catch(_) {}
          },
        });
      });
    });

    // ── Delete ───────────────────────────────────────────────────
    modal.querySelectorAll(".sr-delete-btn").forEach(b => {
      b.addEventListener("click", e => {
        e.stopPropagation();
        const srId   = b.dataset.srId;
        const srName = b.dataset.srName;
        showConfirmModal({
          title: `Delete "${srName}"?`,
          body: `This action <strong class="text-danger">CANNOT</strong> be undone!`,
          confirmLabel: "Delete",
          confirmClass: "bg-danger-lt",
          onConfirm: async () => {
            try {
              const res = await fetch(workforceUrl(`sub_room/${srId}/delete/`), {
                method:"POST", headers:{"Content-Type":"application/json","X-CSRFToken":getCsrf()},
              });
              if (res.ok) {
                // Remove only this sub-room from both local arrays
                const idx = NORMALIZED_CHAT_ROOMS.findIndex(r => r.id === String(srId));
                if (idx !== -1) NORMALIZED_CHAT_ROOMS.splice(idx, 1);
                const crIdx = (window.CHAT_ROOMS || []).findIndex(r => String(r.id) === String(srId));
                if (crIdx !== -1) (window.CHAT_ROOMS || []).splice(crIdx, 1);
                // Remove only this row from the modal DOM
                b.closest(".sr-room-row")?.remove();
                // Navigate away if we were viewing the deleted sub-room
                if (String(activeRoomId) === String(srId)) activateRoom(parentRoomId, null, null);
              }
            } catch(_) {}
          },
        });
      });
    });

    // ── Create sub-room ──────────────────────────────────────────
    if (isPrivileged) {
      modal.querySelector("#srCreate")?.addEventListener("click", async () => {
        const name = modal.querySelector("#srName").value.trim(), desc = modal.querySelector("#srDesc").value.trim();
        const msgEl = modal.querySelector("#srMsg");
        if (!name) { msgEl.textContent = "Name is required."; return; }
        try {
          const url = document.getElementById("chatUrlSubRoom")?.dataset?.url || "/workforce/create_sub_room/";
          const res = await fetch(url, { method:"POST", headers:{"Content-Type":"application/json","X-CSRFToken":getCsrf()}, body:JSON.stringify({parent_room_id:effectiveParentId,name,description:desc}) });
          const data = await res.json();
          if (data.id) {
            const serverParent = data.parent_room_id != null ? String(data.parent_room_id) : parentKey;
            const newRoom = { id: String(data.id), name: data.name || name, parent_room_id: serverParent, room_type: "project", members: [] };
            NORMALIZED_CHAT_ROOMS.push(newRoom);
            msgEl.style.color = "#22c55e";
            msgEl.textContent = "Sub-room created!";

            // For general room (no effectiveParentId), use ALL_MEMBERS (all non-global shown for selection).
            // For a specific room, use that room's members minus global tier.
            const memberPool = effectiveParentId
              ? (NORMALIZED_CHAT_ROOMS.find(r => r.id === String(effectiveParentId))?.members || DEFAULT_TIER_MEMBERS)
              : (NORMALIZED_ALL_MEMBERS.length ? NORMALIZED_ALL_MEMBERS : []);

            const addedIds = new Set();
            const memberBox = document.createElement("div");
            memberBox.style.cssText = "margin-top:12px;border-top:1px solid #374151;padding-top:12px;";
            memberBox.innerHTML = `
              <div class="small text-muted mb-2">Add Members to <strong class="text-white">#${name}</strong></div>
              <div class="small text-muted mb-1" style="font-size:.7rem;">Global-tier members are always shown automatically.</div>
              <input id="srMemberSearch" class="form-control mb-2 bg-dark text-white border-secondary" placeholder="Search Members…" style="font-size:.85rem;box-shadow:inset 0 4px 8px #000000d2;border-radius:24px;">
              <div id="srMemberList" style="box-shadow:0 4px 8px #000000d2;border-radius:24px;max-height:200px;overflow-y:auto;display:flex;flex-direction:column;gap:5px;padding:4px 0;"></div>
              <div id="srAddedChips" class="d-flex flex-wrap gap-1 mt-2"></div>
              <button id="srFinish" class="btn bg-success-lt w-100 mt-3" style="box-shadow:inset 0 4px 8px #000000d2;border-radius:24px;">Done</button>
            `;
            modal.querySelector(".modal-box").appendChild(memberBox);
            modal.querySelector("#srName").closest(".border-top").style.display = "none";

            function renderMemberList(filter) {
              const list = modal.querySelector("#srMemberList"); if(!list) return;
              list.innerHTML = "";

              // Exclude global-tier from selectable pool; if that empties the list, show all
              const lc = filter.toLowerCase();
              let results = memberPool
                .filter(m => {
                  const n = (m.full_name || m.username || "").toLowerCase();
                  const uid = String(m.user_id || m.id);
                  return n.includes(lc) && !addedIds.has(uid) && !_GLOBAL_TIER_IDS.has(uid);
                })
                .sort((a, b) => {
                  const na = (a.full_name || a.username || "").toLowerCase();
                  const nb = (b.full_name || b.username || "").toLowerCase();
                  return na.localeCompare(nb);
                })
                .slice(0, 30);

              // Fallback: if global-tier filter zeroed out a non-empty pool, relax it
              if (!results.length && memberPool.length) {
                results = memberPool
                  .filter(m => {
                    const n = (m.full_name || m.username || "").toLowerCase();
                    const uid = String(m.user_id || m.id);
                    return n.includes(lc) && !addedIds.has(uid);
                  })
                  .sort((a, b) => (a.full_name || "").toLowerCase().localeCompare((b.full_name || "").toLowerCase()))
                  .slice(0, 30);
              }

              if (!results.length) {
                list.innerHTML = `<div class="small text-muted px-3 py-2" style="font-style:italic;">${filter ? "No matching members." : "All available members added."}</div>`;
                return;
              }

              results.forEach(m => {
                const uid = String(m.user_id || m.id), n = m.full_name || m.username || "";
                const row2 = document.createElement("div");
                row2.style.cssText = "display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;cursor:pointer;background:#111827;";
                row2.innerHTML = `<span style="width:28px;height:28px;border-radius:50%;background:${m.color||"#374151"};display:inline-flex;align-items:center;justify-content:center;font-size:.7rem;color:#fff;flex-shrink:0;">${n.slice(0,2).toUpperCase()}</span><span class="text-white small">${m.title||""} ${n}</span>`;
                row2.addEventListener("click", async () => {
                  addedIds.add(uid);
                  try { await fetch(workforceUrl(`sub_room/${data.id}/add_member/`), {method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":getCsrf()},body:JSON.stringify({user_id:uid})}); } catch(_){}
                  const srEntry = NORMALIZED_CHAT_ROOMS.find(r => r.id === String(data.id));
                  if (srEntry) { srEntry.members = srEntry.members || []; if(!srEntry.members.find(x=>String(x.user_id??x.id)===uid)) srEntry.members.push(m); }
                  const crEntry2 = (window.CHAT_ROOMS||[]).find(r=>String(r.id)===String(data.id));
                  if (crEntry2) { crEntry2.members = crEntry2.members||[]; if(!crEntry2.members.find(x=>String(x.user_id??x.id)===uid)) crEntry2.members.push(m); }
                  // Instantly update user strip if this new sub-room is active
                  if (String(activeRoomId) === String(data.id)) buildUserStrip(data.id);
                  const chip = document.createElement("span");
                  chip.className="badge rounded-pill bg-green-lt text-white small d-flex align-items-center gap-1";
                  chip.innerHTML=`${m.title||""} ${n} <span style="cursor:pointer;color:#f03e3e;" data-uid="${uid}">✕</span>`;
                  chip.querySelector("span").addEventListener("click",()=>{ addedIds.delete(uid); chip.remove(); renderMemberList(modal.querySelector("#srMemberSearch")?.value||""); });
                  modal.querySelector("#srAddedChips").appendChild(chip);
                  renderMemberList(modal.querySelector("#srMemberSearch")?.value||"");
                });
                list.appendChild(row2);
              });
            }

            // FIX: Render member list immediately on creation so it's visible
            renderMemberList("");
            modal.querySelector("#srMemberSearch").addEventListener("input", e => renderMemberList(e.target.value));
            modal.querySelector("#srFinish").addEventListener("click", () => { modal.style.display = "none"; });
          } else {
            msgEl.textContent = data.error || "Failed.";
          }
        } catch { modal.querySelector("#srMsg").textContent = "Server error."; }
      });
    }
  }

  // ─── activateSubRoom ─────────────────────────────────────────
  let _inSubRoom = false;
  let _subRoomParentId = null;

  function activateSubRoom(subRoomId, subRoomName, parentRoomId) {
    _inSubRoom = true;
    _subRoomParentId = parentRoomId;
    _userActivatedRoom = true;
    if (subRoomSocket) { try { subRoomSocket.close(); } catch(_){} subRoomSocket = null; }
    document.querySelectorAll(".unit-btn").forEach(b => b.classList.remove("active"));
    if (parentRoomId) {
      document.querySelectorAll(`.unit-btn[data-room-id="${parentRoomId}"]`).forEach(b => b.classList.add("active"));
    }
    activeRoomId = String(subRoomId);
    const parentBtn  = parentRoomId ? document.querySelector(`.unit-btn[data-room-id="${parentRoomId}"]`) : null;
    const parentName = parentBtn?.dataset?.roomName || parentBtn?.dataset?.unitName || "General";
    updateBanner(`${parentName} — ${subRoomName}`);
    buildUserStrip(subRoomId);
    if (wrapper) { wrapper.classList.add("visible"); stripVisible = true; resetAutoHide(); }
    requestAnimationFrame(updateSubRoomTriggerPos);
    // Rebuild mention pool for new room
    mentions = [];
    const oldSrBtn = document.getElementById("subRoomTriggerBtn");
    if (oldSrBtn) {
      oldSrBtn.style.display = "";
      const newSrBtn = oldSrBtn.cloneNode(true);
      oldSrBtn.parentNode?.replaceChild(newSrBtn, oldSrBtn);
      newSrBtn.addEventListener("click", e => { e.stopPropagation(); showSubRoomModal(parentRoomId || null); });
    }
    reconnectSocket(subRoomId);
    clearChat();
    loadMoreMessages();
    scrollToBottomAndFocus();
  }

  // ─── Sub-room chat socket (legacy compat) ────────────────────
  let subRoomSocket = null;
  function openSubRoomChat(subRoomId, subRoomName, parentRoomId) { activateSubRoom(subRoomId, subRoomName, parentRoomId); }
  function closeSrChat(modal) { if(modal) modal.style.display="none"; if(subRoomSocket){try{subRoomSocket.close();}catch(_){} subRoomSocket=null;} }
  function srAppendMsg(container, data) {
    const isMe = data.sender_id === _CUR_USER;
    const div = document.createElement("div"); div.style.cssText = `display:flex;justify-content:${isMe?"flex-end":"flex-start"};margin-bottom:8px;`;
    div.innerHTML = `<div style="max-width:80%;background:${isMe?"#1d4ed8":"#1f2937"};border-radius:12px;padding:8px 12px;color:#f1f5f9;font-size:.9rem;">
      ${!isMe?`<div style="font-size:.7rem;color:#94a3b8;margin-bottom:2px;">${data.sender_title||""} ${data.sender_name||""}</div>`:""}
      <div>${(data.message||"").replace(/\n/g,"<br>")}</div>
      <div style="font-size:.65rem;color:#64748b;text-align:right;margin-top:2px;">${fmtTime(data.created_at)}</div>
    </div>`;
    container.appendChild(div); container.scrollTop = container.scrollHeight;
  }

  // ─── WebSocket ───────────────────────────────────────────────
  function reconnectSocket(roomId) {
    manualClose = true; chatSocket?.close(); manualClose = false;
    chatSocket = null; _sendQueue = [];
    connectSocket(roomId);
  }

  function connectSocket(roomId) {
    const proto = location.protocol==="https:"?"wss":"ws";
    const qs    = roomId ? "?room_id=" + encodeURIComponent(roomId) : "";
    chatSocket = new WebSocket(`${proto}://${location.host}/ws/chat/${qs}`);
    window.activeChatSocket = chatSocket;

    chatSocket.onopen = () => { wsSend({ type:"get_unread_counts" }); flushQueue(); };
    chatSocket.onerror = err => console.error("WS error:", err);
    chatSocket.onclose = () => { if (!manualClose) setTimeout(()=>connectSocket(activeRoomId), 3000); };
    chatSocket.onmessage = e => {
      let data; try { data = JSON.parse(e.data); } catch { return; }
      switch (data.type) {
        case "chat_message": appendMessage(data); return;
        case "unread_counts":
          Object.entries(data.counts||{}).forEach(([rid,n])=>{ UNREAD[rid]=n; updateBadge(rid); }); return;
        case "user_online_status": {
          inner?.querySelectorAll(`.user-card[data-user-id="${data.user_id}"]`).forEach(c => { c.dataset.online = data.is_online ? "true" : "false"; });
          document.querySelectorAll(`.user-card[data-user-id="${data.user_id}"]`).forEach(c => { c.dataset.online = data.is_online ? "true" : "false"; });
          updateOnlineBadge(); return;
        }
        case "typing": showTypingWithName(data.sender_id || data.sender_name, data.sender_name || "Someone", data.sender_title || "", data.sender_color || ""); return;
        case "pinned_preview": {
          const msgRoom = data.room_id != null ? String(data.room_id) : null;
          const curRoom = activeRoomId  != null ? String(activeRoomId) : null;
          if (msgRoom !== null && msgRoom !== curRoom) return;   // wrong room — ignore
          pinnedMessages.length = 0;
          (data.messages || []).forEach(m => _addPinned(m));
          renderPinnedPreview();
          return;
        }
        case "message_pinned": {
          const msgRoom = data.room_id != null ? String(data.room_id) : null;
          const curRoom = activeRoomId  != null ? String(activeRoomId) : null;
          if (msgRoom !== null && msgRoom !== curRoom) return;
          const ids = data.message_ids||[], pm = data.pinned||{};
          ids.forEach(id => {
            const node = document.getElementById("chat-bubble-"+id), flags = node?.querySelector(".chat-bubble-flags");
            if (pm[String(id)]||pm[id]) {
              if (flags && !node.querySelector(".pin-flag")) { const s=document.createElement("span");s.className="pin-flag";s.innerHTML=PIN_SVG;flags.appendChild(s); }
              _addPinned({id, message:node?.querySelector(".message-text")?.textContent||"", pinned_by:data.pinned_by, pinned:true, pinned_at:new Date().toISOString()});
            } else { node?.querySelector(".pin-flag")?.remove(); _removePinned(id); }
          }); return;
        }
        case "chat_reaction": {
          applyChatReactionSummaryToBubble(data.message_id, data.reaction_summary, data.total_reactions);
          return;
        }
        case "message_edited": {
          const editedNode = document.getElementById("chat-bubble-"+data.message_id);
          const t = editedNode?.querySelector(".message-text");
          if (t) {
            const nv = data.message || data.new_text || "";
            if (editedNode) editedNode.dataset.rawMessage = nv;
            t.querySelectorAll(".edited-tag").forEach(el => el.remove());
            t.innerHTML = linkifyText(formatMsg(nv)) + `<em class="badge edited-tag">(edited)</em>`;
          }
          return;
        }
        case "message_deleted": {
          const b = document.getElementById("chat-bubble-"+data.message_id), t = b?.querySelector(".message-text");
          if (t) { t.innerHTML=`<em class="text-muted small">Message deleted</em>`; b?.classList.add("deleted-msg"); } return;
        }
        case "private_message": handlePrivateIncoming(data); return;
      }
      if (data.message) appendMessage(data);
    };
  }

  connectSocket(activeRoomId);

  requestAnimationFrame(() => {
    if (!activeRoomId || activeRoomId === "null") { buildUserStrip(null); }
    else { buildUserStrip(String(activeRoomId)); }
    if (stripVisible) wrapper?.classList.add("visible");
  });

  // ─── Typing indicator ────────────────────────────────────────────────────
  // Rendered as a !isMe-style bubble row appended inside #chatMessagesContainer,
  // not in the input footer area.

  const _typingTimers = {};        // sender_id → timer handle
  const _typingNames  = {};        // sender_id → display name
  const _typingColors = {};        // sender_id → hex color (from data.color / ALL_MEMBERS)
  const _typingActive = new Set(); // sender ids currently "typing"

  // Resolve a member's color hex from ALL_MEMBERS cache (same source as bubble avatars)
  function _memberColorHex(senderId) {
    const m = NORMALIZED_ALL_MEMBERS.find(u => String(u.id) === String(senderId));
    return (m && m.color) ? m.color : null;
  }

  // Broadcast our own typing — name comes from APP_CONFIG set by Django
  chatInput?.addEventListener("input", () => {
    const myName  = window.APP_CONFIG?.user?.name  || "Someone";
    const myTitle = window.APP_CONFIG?.user?.title || "";
    wsSend({
      type:         "typing",
      sender_name:  myName,
      sender_title: myTitle,
      sender_id:    String(_CUR_USER || ""),
      room_id:      activeRoomId,
    });
  });

  // Ensure the typing bubble element exists inside the messages container
  function _getOrCreateTypingEl() {
    let el = document.getElementById("chatTypingIndicator");
    if (el) return el;
    el = document.createElement("div");
    el.id = "chatTypingIndicator";
    el.style.cssText = "display:none;justify-content:flex-start;margin-bottom:8px;padding:0 8px;";
    el.innerHTML =
      '<div id="chatTypingBubble" style="max-width:80%;box-shadow:4px -4px 8px #000000d2;background-color:#0c121f;border-radius:1rem;padding:8px 12px;color: #f1f5f9;font-size:.9rem;">' +
        '<div id="chatTypingLabel" style="font-size:.7rem;font-weight:500;margin-bottom:3px;color:#94a3b8;"></div>' +
        '<span class="typing-dots">' +
          '<span>&#x25cf;</span>' +
          '<span>&#x25cf;</span>' +
          '<span>&#x25cf;</span>' +
        '</span>' +
      '</div>';
    if (chatContainer) chatContainer.appendChild(el);
    return el;
  }

  function showTypingWithName(senderId, name, title="", color="") {
    if (senderId && String(senderId) === String(_CUR_USER)) return;
    const displayName = title ? `${title} ${name}` : name;
    if (name && name !== "Someone") _typingNames[senderId] = displayName;
    // Prefer server-resolved color; fall back to ALL_MEMBERS lookup
    if (color) {
      _typingColors[senderId] = color;
    } else if (!_typingColors[senderId]) {
      const hex = _memberColorHex(senderId);
      if (hex) _typingColors[senderId] = hex;
    }
    _typingActive.add(senderId);
    _renderTypingBar();
    clearTimeout(_typingTimers[senderId]);
    _typingTimers[senderId] = setTimeout(() => {
      _typingActive.delete(senderId);
      delete _typingTimers[senderId];
      _renderTypingBar();
    }, 3000);
  }

  function _renderTypingBar() {
    const el = _getOrCreateTypingEl();
    const active = [..._typingActive];
    if (!active.length) {
      el.style.display = "none";
      return;
    }
    const names  = active.map(id => _typingNames[id] || "Someone");
    const label  = names.length === 1
      ? names[0] + " is typing"
      : names.length === 2
        ? names[0] + " and " + names[1] + " are typing"
        : names.slice(0, 2).join(", ") + " and others are typing";

    // Use the first active sender's color for the bubble accent
    const accentColor = _typingColors[active[0]] || "#94a3b8";
    const bubble = document.getElementById("chatTypingBubble");
    const lbl    = document.getElementById("chatTypingLabel");
    if (bubble) {
      bubble.style.background = `linear-gradient(135deg,${accentColor}18 0%,#0c121f 40%)`;
    }
    if (lbl) {
      lbl.style.color   = accentColor;
      lbl.textContent   = label;
    }
    el.style.display = "flex";
    if (chatContainer) { chatContainer.appendChild(el); chatContainer.scrollTop = chatContainer.scrollHeight; }
  }

  // ─── Unread badges ───────────────────────────────────────────
  function updateBadge(roomId) {
    const n = UNREAD[roomId] || 0;
    // Update ALL matching badges (desktop + mobile both use [data-unit-badge])
    document.querySelectorAll(`[data-unit-badge="${roomId}"]`).forEach(b => {
      b.textContent = n;
      b.style.display = n > 0 ? "inline-block" : "none";
    });
    // Update the cumulative mobile total-unread-chatrooms badge
    const total = Object.values(UNREAD).reduce((s, v) => s + (Number(v) || 0), 0);
    const totalBadge = document.getElementById("total-unread-chatrooms");
    if (totalBadge) {
      totalBadge.textContent = total > 0 ? (total > 99 ? "99+" : total) : "";
      totalBadge.style.display = total > 0 ? "inline-flex" : "none";
    }
  }

  // ─── Pinned messages ─────────────────────────────────────────
  function _addPinned(msg) {
    if (pinnedMessages.find(m=>String(m.id)===String(msg.id))) return;
    if (msg.pinned_at) { const pa=new Date(msg.pinned_at); if(isNaN(pa)||Date.now()-pa>14*86400000) return; }
    if (pinnedMessages.length>=3) pinnedMessages.shift();
    pinnedMessages.push(msg); renderPinnedPreview();
  }
  function _removePinned(id) {
    const i = pinnedMessages.findIndex(m=>String(m.id)===String(id));
    if (i!==-1) { pinnedMessages.splice(i,1); renderPinnedPreview(); }
  }
  function renderPinnedPreview() {
    if (!pinnedPreviewEl) return; pinnedPreviewEl.innerHTML = "";
    pinnedMessages.forEach(msg => {
      const outer = document.createElement("div"); outer.className="pinned-outer"; outer.dataset.id=msg.id;
      const icon  = document.createElement("div"); icon.style.cssText="flex-shrink:0;margin-top:3px;"; icon.innerHTML=PIN_SVG;
      const pinner = document.createElement("div"); pinner.className="pinner";
      pinner.textContent = (msg.pinned_by&&msg.pinned_by.id===_CUR_USER)?"You pinned":(msg.pinned_by?`${msg.pinned_by.title||""} ${msg.pinned_by.name||""}`.trim()+" pinned":"Pinned");
      const info = document.createElement("div"); info.className="pinned-info";
      const sender = (`${msg.original_sender_title||msg.sender_title||""} ${msg.original_sender_name||msg.sender_name||"Unknown"}`).trim();
      info.textContent = sender+": "+(msg.message||"(Attachment)").slice(0,120);
      info.addEventListener("click",()=>scrollToMsg(msg.id));
      outer.append(icon,pinner,info); pinnedPreviewEl.appendChild(outer);
    });
  }
  document.getElementById("pinBtn")?.addEventListener("click",()=>{
    if(!selectedBubbles.size) return;
    wsSend({action:"pin",message_ids:[...selectedBubbles],sender_id:_CUR_USER}); clearSelections();
  });
  document.getElementById("chatReactPickerBtn")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const ids = [...selectedBubbles].map(String);
    if (!ids.length) return;
    // Options panel sits on the right side — picker must open LEFT of the trigger.
    // isMe=true uses the left-side branch in openChatReactionPicker.
    openChatReactionPicker(e.currentTarget, ids, true);
  });

  // ─── Options panel ───────────────────────────────────────────
  function updateOptions() {
    if (!optionsPanel) return;
    const has = selectedBubbles.size > 0;
    optionsPanel.classList.toggle("hidden", !has);
    optionsPanel.setAttribute("aria-hidden", has ? "false" : "true");
    if (has) {
      const srBtn = document.getElementById("subRoomTriggerBtn");
      if (srBtn) {
        const r = srBtn.getBoundingClientRect();
        const cr = optionsPanel.offsetParent?.getBoundingClientRect?.() || {top:0,left:0};
        optionsPanel.style.top = (r.bottom - cr.top + 6) + "px";
        optionsPanel.style.right = (cr.right - r.right) + "px";
        optionsPanel.style.transform = "none";
      }
    }
    const editBtn = document.getElementById("editBtn"), delBtn = document.getElementById("deleteBtn");
    if (!editBtn || !delBtn) return;
    const sel = [...document.querySelectorAll(".chat-bubble.selected")];

    // Edit button: ONLY shown when exactly 1 message selected, owned by current user,
    // AND sent within the last 10 minutes. Default is hidden.
    const singleOwn = sel.length === 1 && (() => {
      const bubble = sel[0];
      const item   = bubble.closest(".chat-item");
      // Sender check
      const senderEl = bubble.closest("[data-sender-id]") || item?.querySelector("[data-sender-id]");
      if (String(senderEl?.dataset?.senderId) !== String(_CUR_USER)) return false;
      // Timestamp — try chat-item first, then the bubble's own dataset, then a time element
      const rawTs = item?.dataset?.createdAt
        || bubble.dataset?.createdAt
        || item?.querySelector("time")?.getAttribute("datetime")
        || null;
      const sent = safeDate(rawTs);
      if (!sent) return false; // can't determine time → keep hidden
      return (Date.now() - sent.getTime()) < EDIT_WINDOW_MS;
    })();
    const allOwn = sel.length > 0 && sel.every(el => {
      const senderEl = el.closest("[data-sender-id]") || el.closest(".chat-item")?.querySelector("[data-sender-id]");
      return String(senderEl?.dataset?.senderId) === String(_CUR_USER);
    });
    editBtn.style.display = singleOwn ? "" : "none";
    editBtn.disabled = !singleOwn;
    delBtn.style.display = (allOwn || _IS_ADMIN) ? "" : "none";
  }

  // FIX: Also refresh edit button visibility on a timer when the panel is visible,
  // so the button disappears after the 10-minute window expires without needing re-selection.
  let _editExpireTimer = null;
  function _scheduleEditExpireCheck() {
    clearInterval(_editExpireTimer);
    _editExpireTimer = setInterval(() => {
      if (!selectedBubbles.size) { clearInterval(_editExpireTimer); return; }
      const editBtn = document.getElementById("editBtn");
      if (!editBtn || editBtn.style.display === "none") { clearInterval(_editExpireTimer); return; }
      // Re-run updateOptions to let it re-evaluate the time window
      updateOptions();
    }, 15000); // check every 15 seconds
  }

  function toggleSelect(bubbleEl, msgId) {
    if (selectedBubbles.has(msgId)) { selectedBubbles.delete(msgId); bubbleEl.classList.remove("selected"); }
    else { selectedBubbles.add(msgId); bubbleEl.classList.add("selected"); }
    updateOptions();
    if (selectedBubbles.size > 0) _scheduleEditExpireCheck();
    else clearInterval(_editExpireTimer);
  }
  function clearSelections() {
    document.querySelectorAll(".chat-bubble.selected").forEach(el => el.classList.remove("selected"));
    selectedBubbles.clear();
    updateOptions();
    clearInterval(_editExpireTimer);
  }
  document.addEventListener("click", e => { if (!optionsPanel?.contains(e.target) && !e.target.closest(".chat-bubble")) clearSelections(); });

  // ─── File icons ──────────────────────────────────────────────
  function fileIcon(ext,type) {
    const a=`xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"`;
    if(ext==="pdf"||(type||"").includes("pdf")) return `<svg ${a} style="color:#e03e3e"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M5 12v-7a2 2 0 0 1 2-2h7l5 5v4"/><path d="M5 18h1.5a1.5 1.5 0 0 0 0-3h-1.5v6"/><path d="M17 18h2"/><path d="M20 15h-3v6"/><path d="M11 15v6h1a2 2 0 0 0 2-2v-2a2 2 0 0 0-2-2h-1z"/></svg>`;
    if(["doc","docx"].includes(ext)) return `<svg ${a} style="color:#2b579a"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M5 12v-7a2 2 0 0 1 2-2h7l5 5v4"/><path d="M5 15v6h1a2 2 0 0 0 2-2v-2a2 2 0 0 0-2-2h-1z"/></svg>`;
    if(["xls","xlsx","csv"].includes(ext)) return `<svg ${a} style="color:#217346"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M5 12v-7a2 2 0 0 1 2-2h7l5 5v4"/><path d="M4 15l4 6"/><path d="M4 21l4-6"/><path d="M11 15v6h3"/></svg>`;
    return `<svg ${a} style="color:#94a3b8"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M5 12v-7a2 2 0 0 1 2-2h7l5 5v4"/><path d="M9 17h6"/><path d="M9 20h3"/></svg>`;
  }
  function renderFileBubble(file) {
    if(!file) return "";
    const name=file.name||"file", type=(file.type||"").toLowerCase(), ext=name.split(".").pop().toLowerCase()||"", url=file.display_url||file.url, sz=file.size?`${(file.size/1024).toFixed(1)} KB`:"";
    const c="file-bubble-wrap";
    if(type.startsWith("image/")) return `<div class="mb-2 p-2 ${c}"><a href="${url}" target="_blank"><img src="${url}" alt="${name}" style="max-width:200px;max-height:200px;object-fit:cover;border-radius:8px;display:block;"></a><div class="mt-1 text-muted text-center" style="font-size:.65rem;">${name}${sz?" · "+sz:""}</div></div>`;
    if(type.startsWith("video/")) return `<div class="mb-2 p-2 ${c}"><video src="${url}" controls style="max-width:200px;max-height:150px;border-radius:8px;"></video><div class="mt-1 text-muted" style="font-size:.65rem;">${name}</div></div>`;
    if(type.startsWith("audio/")) return `<div class="mb-2 p-2 ${c}"><audio src="${url}" controls style="width:200px;border-radius:8px;"></audio><div class="mt-1 text-muted" style="font-size:.65rem;">${name}</div></div>`;
    return `<div class="mb-2 p-2 ${c}"><a href="${url}" target="_blank" class="d-flex flex-column align-items-center" style="text-decoration:none;">${fileIcon(ext,type)}<div class="small text-white mt-1">${ext.toUpperCase()}</div></a><div class="mt-1 text-muted text-center" style="font-size:.65rem;">${name}${sz?" · "+sz:""}</div></div>`;
  }

  // ─── renderMessage ───────────────────────────────────────────
  function renderMessage(data) {
    const isMe=String(data.sender_id)===String(_CUR_USER), isDeleted=!!data.is_deleted, isFwd=!!data.forwarded_from;
    const el=document.createElement("div");
    el.dataset.createdAt=data.created_at; el.className="chat-item"+(isDeleted?" deleted-msg":"");
    el.id="chat-bubble-"+data.id; el.dataset.senderId=data.sender_id;
    el.dataset.senderName=data.sender_name||""; el.dataset.senderTitle=data.sender_title||"";
    el.dataset.rawMessage=data.message||"";
    if(data.guest) el.dataset.guest=JSON.stringify(data.guest);
    if(data.file)  el.dataset.file=JSON.stringify(data.file);
    if(data.link_preview) el.dataset.linkPreview=JSON.stringify(data.link_preview);

    let replyHTML="";
    if(data.parent){
      const p=data.parent, ro=String(p.sender_id)===String(_CUR_USER)?"You":`${p.sender_title||""} ${p.sender_name||""}`.trim();
      const _replyColor=(()=>{
        if(String(p.sender_id)===String(_CUR_USER)) return "#475569";
        const m=NORMALIZED_ALL_MEMBERS.find(u=>String(u.id)===String(p.sender_id));
        return (m&&m.color)?m.color:"#475569";
      })();
      // Plan discussion parent — render a mini plan card instead of raw text
      if((p.message||"").startsWith(CHAT_PLAN_SENTINEL)){
        let _miniMeta={}, _miniBody="";
        try{
          const _rest=p.message.slice(CHAT_PLAN_SENTINEL.length);
          const _sep=_rest.indexOf("\n\n");
          _miniMeta=JSON.parse(_sep!==-1?_rest.slice(0,_sep):_rest);
          _miniBody=_sep!==-1?_rest.slice(_sep+2).trim():"";
        }catch(_e){}
        const _ref  = htmlEsc(_miniMeta.passage_reference||_miniMeta.ref||"");
        const _plan = htmlEsc(_miniMeta.plan_title||_miniMeta.title||"");
        const _lbl  = _miniMeta.is_admin_post?"started a Discourse":"asked a Question";
        replyHTML=`<div class="chat-reply-preview fs-7 mb-1 p-1 rounded chat-reply-block" data-parent-id="${p.id}" style="border-left:3px solid ${_replyColor};padding-left:6px !important;">
          <small style="color:${_replyColor};font-weight:500;">${htmlEsc(ro)}</small>
          <span class="ms-1 text-muted" style="font-size:.68rem;font-style:italic;"> ${_lbl}</span><br>
          <span class="text-secondary" style="font-size:.72rem;">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 6.5a5.5 5.5 0 0 1 4-1.5h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-3.5"/>
              <path d="M12 6.5a5.5 5.5 0 0 0-4-1.5H5a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h3.5"/>
              <path d="M12 6.5V19"/><path d="M9 10h1"/><path d="M9 13h1"/>
            </svg>
            ${_ref}${_plan?" · "+_plan:""}
          </span>
          ${_miniBody?`<br><span class="text-muted" style="font-size:.7rem;">${htmlEsc(_miniBody.slice(0,80))}${_miniBody.length>80?"…":""}</span>`:""}
        </div>`;
      } else {
        replyHTML=`<div class="chat-reply-preview fs-7 mb-1 p-1 rounded chat-reply-block" data-parent-id="${p.id}" style="border-left:3px solid ${_replyColor};padding-left:6px !important;"><small style="color:${_replyColor};font-weight:500;">${ro}</small><br><span class="text-secondary">${msgPlainPreview(p.message, 80)}</span></div>`;
      }
    }
    let fwdHTML = "";
    if (isFwd) {
      let fromLabel = "";
      if (data.forwarded_from_room_name) { fromLabel = data.forwarded_from_room_name; }
      else if (data.forwarded_from?.room_name) { fromLabel = data.forwarded_from.room_name; }
      else if (data.forwarded_from_room_id) {
        const fr = NORMALIZED_CHAT_ROOMS.find(r => String(r.id) === String(data.forwarded_from_room_id));
        fromLabel = fr ? fr.name : "another room";
      } else { fromLabel = "General Workforce"; }
      fwdHTML = `<div class="chat-fwd-label d-flex align-items-center gap-1 mb-1">
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke=" #ffe600" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-tabler icons-tabler-outline icon-tabler-arrow-forward-up-double"><path stroke="none" d="M0 0h24v24H0z" fill="none" /><path d="M11 14l4 -4l-4 -4" /><path d="M16 14l4 -4l-4 -4" /><path d="M15 10h-7a4 4 0 1 0 0 8h1" /></svg>
        Forwarded from <em class='fwd-room-name ms-1'>${fromLabel}</em>
      </div>`;
    }

    let guestHTML="";
    if(data.guest){
      const g=data.guest, au=g.assigned_user;
      const auH=au?`<div class="d-flex align-items-center gap-2 mt-1">${au.image?`<img src="${au.image}" style="width:28px;height:28px;border-radius:4px;object-fit:cover;">`:`<span style="width:28px;height:28px;background:#374151;border-radius:4px;display:inline-flex;align-items:center;justify-content:center;font-size:.7rem;">${(au.full_name||"?")[0]}</span>`}<span class="small text-white">${au.title||""} ${au.full_name||""}</span></div>`:"";
      guestHTML=`<div class="text-white text-center px-2 py-2 mb-2 d-flex flex-column align-items-center gap-1" style="border-radius:10px;max-width:250px;box-shadow:0 4px 8px #000000a1;background:#11182781;">${g.image?`<img src="${g.image}" style="width:180px;height:180px;object-fit:cover;border-radius:8px;">`:`<div class="fw-bold fs-2 d-flex align-items-center justify-content-center" style="width:180px;height:180px;border-radius:8px;background:#374151;">${(g.name||"?")[0].toUpperCase()}</div>`}<div class="fw-bold fs-4 text-warning">${g.title||""} ${g.name||""}</div><div class="text-muted small">${g.custom_id||""}</div><div class="small">${fmtGuestDate(g.date_of_visit)}</div>${auH}</div>`;
    }
    const fileHTML=renderFileBubble(data.file);
    let linkHTML="";
    if(data.link_preview){
      const lp=data.link_preview; let host=""; try{host=new URL(lp.url).hostname;}catch{}
      linkHTML=`<a href="${lp.url}" target="_blank" rel="noopener noreferrer" class="text-white text-decoration-none px-2 py-2 mb-2 rounded-2 d-flex align-items-center gap-2" style="box-shadow:0 4px 8px #000a;background:#11182781;max-width:100%;overflow:hidden;">${lp.image?`<img src="${lp.image}" style="flex-shrink:0;width:60px;height:60px;border-radius:4px;object-fit:cover;">`:""}<div class="flex-grow-1 overflow-hidden"><div class="fw-bold text-truncate">${lp.title||""}</div><div class="text-muted small">${lp.description||""}</div><div class="text-secondary small text-truncate">${host}</div></div></a>`;
    }

    const initials=(data.sender_name||"?").split(" ").map(n=>n[0]||"").slice(0,2).join("").toUpperCase()||"?";
    const avatarStyle=data.sender_image?`background-image:url('${data.sender_image}');background-size:cover;background-position:center;`:"";
    const avatarInner=data.sender_image?"":initials;
    const avatarHTML=`<span class="avatar-wrap position-relative d-inline-flex" style="flex-shrink:0;">
      <span class="avatar rounded private-chat-trigger d-flex align-items-center justify-content-center"
        data-member-id="${data.sender_member_id||""}" data-user-id="${data.sender_id}"
        data-name="${(data.sender_name||"").replace(/"/g,"&quot;")}" data-room-id="${activeRoomId||""}"
        title="Start private chat"
        style="width:28px;height:28px;font-size:.65rem;font-weight:600;cursor:pointer;color:#ffffff;${avatarStyle}${data.color?"background-color:"+data.color+";":"background:#ffffff;"}">${avatarInner}</span>
      <span class="pc-unread-badge" data-for-user="${data.sender_id}" style="display:none;position:absolute;top:-4px;left:-4px;min-width:14px;height:14px;background:#ef4444;color:#fff;font-size:.55rem;font-weight:700;border-radius:9999px;align-items:center;justify-content:center;padding:0 3px;line-height:1;z-index:2;"></span>
    </span>`;

    const rawMsg=isDeleted?"":(data.message||"");

    // ── Plan discussion detection ────────────────────────────────────────
    let _planMeta = null;
    let _planUserText = "";
    let _planCardHTML = "";
    if (!isDeleted && rawMsg.startsWith(CHAT_PLAN_SENTINEL)) {
      try {
        const sentinel_rest = rawMsg.slice(CHAT_PLAN_SENTINEL.length);
        const sep = sentinel_rest.indexOf("\n\n");
        const metaRaw = sep !== -1 ? sentinel_rest.slice(0, sep) : sentinel_rest;
        _planUserText = sep !== -1 ? sentinel_rest.slice(sep + 2) : "";
        _planMeta = JSON.parse(metaRaw);
        const _planTitle = htmlEsc(_planMeta.plan_title || "");
        const _passRef   = htmlEsc(_planMeta.passage_reference || "");
        const _devTitle  = htmlEsc(_planMeta.devotional_title || "");
        const _schedDate = htmlEsc(_planMeta.scheduled_date || "");
        const _entryId   = _planMeta.entry_id || "";
        _planCardHTML = `<div class="chat-plan-disc-card mb-2" data-entry-id="${_entryId}">
          ${_planUserText ? `<div class="chat-plan-disc-question mb-2">${htmlEsc(_planUserText)}</div>` : ""}
          <div class="chat-plan-disc-ref-card p-2">
            <div class="d-flex align-items-center gap-2 mb-1">
              <span style="font-size:.85rem;">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M12 6.5a5.5 5.5 0 0 1 4-1.5h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-3.5"/>
                  <path d="M12 6.5a5.5 5.5 0 0 0-4-1.5H5a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h3.5"/>
                  <path d="M12 6.5V19"/><path d="M9 10h1"/><path d="M9 13h1"/>
                </svg>
              </span>
              <span class="fw-semibold text-warning" style="font-size:.82rem;">${_passRef}</span>
            </div>
            ${_devTitle ? `<div class="text-white small" style="font-size:.78rem;">${_devTitle}</div>` : ""}
            ${_planTitle ? `<div class="text-muted" style="font-size:.72rem;">Plan: ${_planTitle}</div>` : ""}
            ${_schedDate ? `<div class="text-muted" style="font-size:.68rem;">${_schedDate}</div>` : ""}
          </div>
          <button type="button" class="btn btn-xs mt-2 w-100 chat-plan-disc-thread-toggle"
            data-entry-id="${_entryId}">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-tabler icons-tabler-outline icon-tabler-messages">
              <path stroke="none" d="M0 0h24v24H0z" fill="none" />
              <path d="M21 14l-3 -3h-7a1 1 0 0 1 -1 -1v-6a1 1 0 0 1 1 -1h9a1 1 0 0 1 1 1v10" />
              <path d="M14 15v2a1 1 0 0 1 -1 1h-7l-3 3v-10a1 1 0 0 1 1 -1h2" />
            </svg> 
            Threads
          </button>
          <div class="chat-plan-disc-thread d-none mt-2" data-entry-id="${_entryId}">
            <div class="chat-plan-disc-thread-list text-muted fst-italic small">Loading…</div>
            <textarea class="form-control form-control-sm mt-2 chat-plan-disc-reply-input" rows="2"
              placeholder="Add to this discussion…"
              style="background: #111827;color:#e2e8f0;box-shadow:inset 0 4px 8px #000000d2;border-radius:12px;font-size:.78rem;"></textarea>
            <div class="d-flex justify-content-end mt-1">
              <button type="button" class="btn btn-xs btn-primary chat-plan-disc-reply-btn"
                data-entry-id="${_entryId}"
                data-plan-id="${_planMeta.plan_id || ""}"
                style="border-radius:12px;font-size:.72rem;">Reply</button>
            </div>
          </div>
        </div>`;
      } catch(_e) {
        _planMeta = null;
        _planCardHTML = "";
      }
    }

    const renderedMsg=isDeleted?`<em class="text-muted small">Message deleted</em>`:(_planMeta ? "" : linkifyText(renderWithMentions(formatMsg(rawMsg),data.mentions)));

    // FIX: React corner btn — sticky to top-right for !isMe, top-left for isMe.
    // Uses absolute positioning inside the bubble (bubble must be position:relative).
    // The CSS class chat-react-corner-btn-me / chat-react-corner-btn-them controls side.
    const _bubbleAccent = !isMe && data.color
      ? `background:linear-gradient(135deg, #0c121f 0%, #0c121f 85%, ${data.color}18 100%) !important;`
      : "";

    el.innerHTML=`<div class="d-flex align-items-end mb-1 ${isMe? "justify-content-end":""}">
      ${!isMe?`<div class="chat-avatar-anchor me-1" style="position:relative;flex-shrink:0;">${avatarHTML}</div>`:""}
      <div class="chat-bubble position-relative ${isMe?"chat-bubble-me":""} ${isMe?"chat-bubble-me-bg":"chat-bubble-them-bg"}"
           data-sender-id="${data.sender_id}"
           style="${_bubbleAccent}">
        <button type="button"
          class="chat-react-corner-btn ${isMe ? "chat-react-corner-btn-me" : "chat-react-corner-btn-them"} d-none d-lg-inline-flex"
          title="React" aria-label="Reactions" data-react-msg="${data.id}">
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke=" #ff4800" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-tabler icons-tabler-outline icon-tabler-mood-spark">
            <path stroke="none" d="M0 0h24v24H0z" fill="none" />
            <path d="M21 12a9 9 0 1 0 -8.994 9" />
            <path d="M9 10h.01" />
            <path d="M15 10h.01" />
            <path d="M9.5 15a3.5 3.5 0 0 0 5 0" />
            <path d="M19 22.5a4.75 4.75 0 0 1 3.5 -3.5a4.75 4.75 0 0 1 -3.5 -3.5a4.75 4.75 0 0 1 -3.5 3.5a4.75 4.75 0 0 1 3.5 3.5" />
          </svg>
        </button>
        ${!isMe ? `<div class="mb-1 chat-bubble-sender" style="color:${data.color||"#94a3b8"};">
            <i>${data.sender_title||""} ${data.sender_name||""}</i>${_planMeta ? `<span style="color:#94a3b8;font-weight:400;font-style:italic;font-size:.72rem;"> ${_planMeta.is_admin_post ? "shared a Discourse" : "asked a Question"}</span>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 6.5a5.5 5.5 0 0 1 4-1.5h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-3.5"/>
              <path d="M12 6.5a5.5 5.5 0 0 0-4-1.5H5a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h3.5"/>
              <path d="M12 6.5V19"/><path d="M9 10h1"/><path d="M9 13h1"/>
            </svg>` : ""}
          </div>` : ""}
        <div class="chat-bubble-body">
          ${fwdHTML}${replyHTML}${guestHTML}${fileHTML}${linkHTML}
          ${_planCardHTML}
          ${(!_planMeta && (rawMsg.trim()||isDeleted))?`<div class="message-text text-white">${renderedMsg}</div>`:""}
          <div class="d-flex justify-content-end align-items-center gap-1 mt-1">
            <small class="text-muted chat-bubble-timestamp" data-full-date="${data.created_at}"
              title="${fmtDateLabel(data.created_at)} ${fmtTime(data.created_at)}"
              style="font-size:.55rem;white-space:nowrap;">${fmtTime(data.created_at)}</small>
            ${data.edited_at?`<em class="badge edited-tag">(edited)</em>`:""}
          </div>
        </div>
        <div class="chat-bubble-flags">
          <span class="chat-reaction-summary" data-msg-id="${data.id}" data-reaction-summary="${(data.reaction_summary?JSON.stringify(data.reaction_summary):'{}').replace(/"/g,'&quot;')}" data-reaction-total="${data.total_reactions||0}">${buildChatReactionSummaryHTML(data.reaction_summary, data.total_reactions, data.id)}</span>
        </div>
      </div>
    </div>`;

    const flags=el.querySelector(".chat-bubble-flags");
    if(data.pinned&&flags){const p=document.createElement("span");p.className="pin-flag bounce-flag";p.innerHTML=PIN_SVG;flags.appendChild(p);}
    if(flags&&data.mentions?.some(m=>parseInt(m.id)===_CUR_USER)){
      const f=document.createElement("span");f.className="mention-flag wiggle-flag";
      f.innerHTML=`<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0"/><path d="M16 12v1.5a2.5 2.5 0 0 0 5 0v-1.5a9 9 0 1 0-5.5 8.28"/></svg>`;
      flags.appendChild(f);
    }
    if(flags && data.was_forwarded && !isFwd) {
      const s = document.createElement("span"); s.className = "fwd-flag point-flag"; s.title = "Forwarded";
      s.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke=" #ffe600" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-tabler icons-tabler-outline icon-tabler-arrow-forward-up-double"><path stroke="none" d="M0 0h24v24H0z" fill="none" /><path d="M11 14l4 -4l-4 -4" /><path d="M16 14l4 -4l-4 -4" /><path d="M15 10h-7a4 4 0 1 0 0 8h1" /></svg>`;
      flags.appendChild(s);
    }
    el.querySelector(".chat-react-corner-btn")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openChatReactionPicker(e.currentTarget, [String(data.id)], isMe);
    });
    const bubbleEl=el.querySelector(".chat-bubble");
    if(!isTouch){
      bubbleEl.addEventListener("contextmenu",ev=>{ev.preventDefault();toggleSelect(bubbleEl,data.id);});
      bubbleEl.addEventListener("click",ev=>{if(selectedBubbles.size>0){ev.preventDefault();toggleSelect(bubbleEl,data.id);}});
    } else {
      let pressTimer,tsx=0,tsy=0,lpFired=false;
      bubbleEl.addEventListener("touchstart",ev=>{tsx=ev.changedTouches[0].screenX;tsy=ev.changedTouches[0].screenY;lpFired=false;pressTimer=setTimeout(()=>{lpFired=true;const anim=bubbleEl.animate([{transform:"scale(1)"},{transform:"scale(0.72)"}],{duration:110,fill:"forwards",easing:"ease-out"});if(navigator.vibrate)navigator.vibrate(18);anim.onfinish=()=>{toggleSelect(bubbleEl,data.id);bubbleEl.animate([{transform:"scale(0.72)"},{transform:"scale(1.05)"},{transform:"scale(1)"}],{duration:300,fill:"forwards",easing:"cubic-bezier(.22,1.35,.5,1)"});};},500);},{passive:true});
      bubbleEl.addEventListener("touchmove",ev=>{const dx=Math.abs(ev.changedTouches[0].screenX-tsx),dy=Math.abs(ev.changedTouches[0].screenY-tsy);if(dx>12||dy>12)clearTimeout(pressTimer);},{passive:true});
      bubbleEl.addEventListener("touchend",ev=>{clearTimeout(pressTimer);if(lpFired)return;const dx=ev.changedTouches[0].screenX-tsx,dy=Math.abs(ev.changedTouches[0].screenY-tsy);if(dx>58&&dy<28){popAnim(bubbleEl);setReply(data);}},{passive:true});
      bubbleEl.addEventListener("touchcancel",()=>clearTimeout(pressTimer),{passive:true});
      bubbleEl.addEventListener("contextmenu",ev=>ev.preventDefault());
    }
    el.querySelectorAll(".chat-reply-preview").forEach(r=>r.addEventListener("click",()=>scrollToMsg(r.dataset.parentId)));
    return el;
  }

  // ─── Reply ───────────────────────────────────────────────────
  function setReply(data) {
    replyToId=data.id;
    if(!replyPreview||!replyPreviewText) return;
    replyPreview.classList.remove("d-none");
    const isMe=String(data.sender_id)===String(_CUR_USER), owner=isMe?"You":`${data.sender_title||""} ${data.sender_name||""}`.trim();
    const _color=(()=>{
      if(isMe) return "#475569";
      const m=NORMALIZED_ALL_MEMBERS.find(u=>String(u.id)===String(data.sender_id));
      return (m&&m.color)?m.color:"#475569";
    })();
    replyPreview.style.borderLeftColor=_color;

    const rawMsg = data.message || "";
    if(rawMsg.startsWith(CHAT_PLAN_SENTINEL)){
      // Render a mini plan card in the reply preview strip
      let _meta={}, _bodyTxt="";
      try{
        const _rest=rawMsg.slice(CHAT_PLAN_SENTINEL.length);
        const _sep=_rest.indexOf("\n\n");
        _meta=JSON.parse(_sep!==-1?_rest.slice(0,_sep):_rest);
        _bodyTxt=_sep!==-1?_rest.slice(_sep+2).trim():"";
      }catch(_e){}
      const _ref  = htmlEsc(_meta.passage_reference||_meta.ref||"");
      const _plan = htmlEsc(_meta.plan_title||_meta.title||"");
      const _lbl  = _meta.is_admin_post?"started a Discourse":"asked a Question";
      replyPreviewText.innerHTML=`
        <small style="color:${_color};font-weight:500;">${htmlEsc(owner)}</small>
        <span class="ms-1 text-muted" style="font-size:.68rem;font-style:italic;"> ${_lbl}</span><br>
        <span class="text-secondary" style="font-size:.72rem;">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 6.5a5.5 5.5 0 0 1 4-1.5h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-3.5"/>
            <path d="M12 6.5a5.5 5.5 0 0 0-4-1.5H5a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h3.5"/>
            <path d="M12 6.5V19"/><path d="M9 10h1"/><path d="M9 13h1"/>
          </svg> 
          ${_ref}${_plan?" · "+_plan:""}
        </span>
        ${_bodyTxt?`<br><span class="text-muted" style="font-size:.7rem;">${htmlEsc(_bodyTxt.slice(0,80))}${_bodyTxt.length>80?"…":""}</span>`:""}
      `;
    } else {
      replyPreviewText.innerHTML=`<small style="color:${_color};font-weight:500;">${htmlEsc(owner)}</small><br><span class="text-muted">${msgPlainPreview(rawMsg, 100)}</span>`;
    }
    chatInput?.focus();
  }
  cancelReply?.addEventListener("click", () => {replyToId=null;replyPreview?.classList.add("d-none");if(replyPreviewText)replyPreviewText.innerHTML="";});

  // ─── Plan Discussion thread toggle + reply (delegated) ───────────────
  const _DISC_CREATE_URL_CHAT = (typeof DISC_CREATE_URL !== "undefined") ? DISC_CREATE_URL : null;
  const _DISC_LIST_URL_CHAT   = (typeof DISC_LIST_URL_TEMPLATE !== "undefined") ? DISC_LIST_URL_TEMPLATE : null;
  const _CSRF_CHAT = (typeof CSRF_TOKEN !== "undefined") ? CSRF_TOKEN : "";

  // Render a single thread reply item (used in chat bubble thread panel)
  function _renderDiscThreadItem(d) {
    const color = d.author_color || "#6366f1";
    const isAdmin = !!d.is_admin;
    const timeStr = fmtTime(d.created_at);
    const iso = d.created_at || "";
    return `<div class="plan-disc-thread-item mb-2 p-2" data-created-at="${htmlEsc(iso)}"
        style="background:#1e293b;border-radius:8px;border-left:3px solid ${color};">
      <div class="d-flex align-items-center gap-1 mb-1">
        <span class="fw-semibold" style="font-size:.75rem;color:${color};">${htmlEsc(d.author)}</span>
        ${isAdmin ? '<span class="badge ms-1" style="font-size:.58rem;background:#854d0e;color:#fef9c3;border-radius:4px;padding:1px 5px;">Admin</span>' : ""}
        <time class="ms-auto text-muted" datetime="${htmlEsc(iso)}" style="font-size:.65rem;" title="${htmlEsc(iso)}">${timeStr}</time>
      </div>
      <div style="color:#cbd5e1;font-size:.8rem;line-height:1.5;">${htmlEsc(d.body)}</div>
    </div>`;
  }

  // Insert date-separator divs between thread items that fall on different days
  function _injectThreadDateSeps(container) {
    const items = [...container.querySelectorAll(".plan-disc-thread-item")];
    let lastDay = null;
    items.forEach(item => {
      const d = safeDate(item.dataset.createdAt);
      if (!d) return;
      const dayStr = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      if (dayStr !== lastDay) {
        lastDay = dayStr;
        const sep = document.createElement("div");
        sep.className = "plan-disc-date-sep text-center my-1";
        sep.style.cssText = "font-size:.65rem;color:#475569;letter-spacing:.04em;";
        sep.textContent = dateLabel(d);
        container.insertBefore(sep, item);
      }
    });
  }

  async function _loadPlanDiscThreadInChat(entryId, listEl) {
    if (!_DISC_LIST_URL_CHAT) {
      listEl.innerHTML = '<div class="text-muted small p-1">Discussion list unavailable.</div>';
      return;
    }
    listEl.innerHTML = '<div class="text-muted small p-1">Loading…</div>';
    try {
      // ?destination=chat keeps feed discussions out of the chat thread
      const url = _DISC_LIST_URL_CHAT.replace(/\/0\//, `/${entryId}/`) + "?destination=chat";
      const res  = await fetch(url, { credentials: "same-origin" });
      const data = await res.json();
      if (!data.discussions?.length) {
        listEl.innerHTML = '<div class="text-muted fst-italic small p-1">No replies yet — be the first!</div>';
        return;
      }
      // Render items (server returns ascending order — newest at bottom)
      listEl.innerHTML = data.discussions.map(_renderDiscThreadItem).join("");
      _injectThreadDateSeps(listEl);
      // Scroll thread to bottom so newest is visible
      listEl.scrollTop = listEl.scrollHeight;
    } catch(e) {
      listEl.innerHTML = '<div class="text-danger small p-1">Could not load.</div>';
    }
  }

  chatContainer?.addEventListener("click", async (e) => {
    // Toggle thread panel
    const toggleBtn = e.target.closest(".chat-plan-disc-thread-toggle");
    if (toggleBtn) {
      const entryId = toggleBtn.dataset.entryId;
      const card = toggleBtn.closest(".chat-plan-disc-card");
      if (!card) return;
      const threadDiv = card.querySelector(".chat-plan-disc-thread");
      if (!threadDiv) return;
      const isHidden = threadDiv.classList.contains("d-none");
      threadDiv.classList.toggle("d-none", !isHidden);
      if (isHidden) {
        const listEl = threadDiv.querySelector(".chat-plan-disc-thread-list");
        if (listEl) await _loadPlanDiscThreadInChat(entryId, listEl);
      }
      return;
    }
    // Reply button
    const replyBtn = e.target.closest(".chat-plan-disc-reply-btn");
    if (replyBtn) {
      const entryId  = replyBtn.dataset.entryId;
      const planId   = replyBtn.dataset.planId;
      const card     = replyBtn.closest(".chat-plan-disc-card");
      const threadDiv = card?.querySelector(".chat-plan-disc-thread");
      const textarea = threadDiv?.querySelector(".chat-plan-disc-reply-input");
      const text = textarea?.value?.trim();
      if (!text || !_DISC_CREATE_URL_CHAT) return;
      replyBtn.disabled = true; replyBtn.textContent = "Posting…";
      try {
        const res = await fetch(_DISC_CREATE_URL_CHAT, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": _CSRF_CHAT },
          body: JSON.stringify({
            entry_id: parseInt(entryId),
            plan_id:  parseInt(planId),
            body:     text,
            destination: "thread",   // saved as destination="chat" server-side
            scope: "workforce",
            unit_id: null,
          }),
        });
        const data = await res.json();
        if (data.ok) {
          textarea.value = "";
          const listEl = threadDiv?.querySelector(".chat-plan-disc-thread-list");
          if (listEl) await _loadPlanDiscThreadInChat(entryId, listEl);
        }
      } catch(_e) { console.warn("plan disc reply:", _e); }
      finally { replyBtn.disabled = false; replyBtn.textContent = "Reply"; }
      return;
    }
  });

  // ─── Date separators ─────────────────────────────────────────
  // ─── Consecutive-sender stacking ────────────────────────────────────────────
  // When the same !isMe sender posts multiple messages in a row, all but the
  // last in the run get class "chat-bubble-stacked" which hides the avatar and
  // the ::after tail. The LAST bubble in a run always shows the avatar + tail.
  // ── _msgDay: returns YYYY-MM-DD string for a chat-item ─────────────────────
  function _msgDay(el) {
    const d = safeDate(el?.dataset?.createdAt);
    if (!d) return null;
    return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  }

  function _restack() {
    const items = [...chatContainer.querySelectorAll(".chat-item:not(.deleted-msg)")];
    for (let i = 0; i < items.length; i++) {
      const el       = items[i];
      const senderId = el.dataset.senderId;
      const isMe     = String(senderId) === String(_CUR_USER);

      const nextEl       = items[i + 1];
      const nextSenderId = nextEl ? nextEl.dataset.senderId : null;
      const nextIsMe     = nextSenderId ? String(nextSenderId) === String(_CUR_USER) : false;
      const prevEl       = items[i - 1];
      const prevSenderId = prevEl ? prevEl.dataset.senderId : null;

      // Date-boundary check — break the stack when the date changes
      const sameDay     = _msgDay(el) && nextEl ? _msgDay(el) === _msgDay(nextEl) : false;
      const prevSameDay = _msgDay(el) && prevEl ? _msgDay(el) === _msgDay(prevEl) : false;

      if (isMe) {
        el.classList.remove("chat-bubble-stacked");
        // Still tighten me-side consecutive bubbles on same day
        const meTight = prevEl && String(prevEl.dataset.senderId) === String(_CUR_USER) && prevSameDay;
        el.classList.toggle("chat-bubble-tight", meTight);
        continue;
      }

      // Stack only if next message is same sender AND same calendar day
      const stackThis = !!(nextSenderId && !nextIsMe && nextSenderId === senderId && sameDay);
      el.classList.toggle("chat-bubble-stacked", stackThis);

      // Tighten gap only if previous message is same sender AND same calendar day
      const tightTop = !!(prevSenderId && prevSenderId === senderId && prevSameDay);
      el.classList.toggle("chat-bubble-tight", tightTop);
    }
  }

  function normalizeSeparators() {
    chatContainer.querySelectorAll(".chat-date-separator").forEach(el=>el.remove());
    const msgs=[...chatContainer.querySelectorAll(".chat-item")].sort((a,b)=>(safeDate(a.dataset.createdAt)||new Date(0))-(safeDate(b.dataset.createdAt)||new Date(0)));
    msgs.forEach(m=>chatContainer.appendChild(m));
    let lastDay=null;
    msgs.forEach(msg=>{
      const d=safeDate(msg.dataset.createdAt);if(!d)return;
      const md=new Date(d.getFullYear(),d.getMonth(),d.getDate());
      if(!lastDay||md.getTime()!==lastDay.getTime()){
        lastDay=md;
        const sep=document.createElement("div");sep.className="chat-date-separator mb-2 text-center chat-date-sep-pill";
        sep.textContent=dateLabel(md);chatContainer.insertBefore(sep,msg);
      }
    });
  }
  function updateSticky() {
    if(!stickyDateHeader) return;
    const cr=chatContainer.getBoundingClientRect(); let t="";
    chatContainer.querySelectorAll(".chat-date-separator").forEach(s=>{if(s.getBoundingClientRect().top<=cr.top+10)t=s.textContent;});
    stickyDateHeader.textContent=t; stickyDateHeader.style.opacity=t?"1":"0";
  }
  chatContainer.addEventListener("scroll",updateSticky);

  // ─── Scroll ──────────────────────────────────────────────────
  async function scrollToMsg(msgId) {
    let target=document.getElementById("chat-bubble-"+msgId), tries=0;
    while(!target&&oldestLoaded&&tries++<5){await loadMoreMessages();target=document.getElementById("chat-bubble-"+msgId);}
    if(!target) return;
    target.scrollIntoView({behavior:"smooth",block:"center"});
    const b=target.querySelector(".chat-bubble");
    if(b){b.classList.add("highlight-chat-bubble");setTimeout(()=>b.classList.remove("highlight-chat-bubble"),5000);}
  }
  chatContainer.addEventListener("scroll",()=>{
    if(chatContainer.scrollTop===0&&!loading) loadMoreMessages();
    const atBottom=chatContainer.scrollHeight-chatContainer.scrollTop-chatContainer.clientHeight<64;
    if(scrollToBottomBtn){scrollToBottomBtn.classList.toggle("hidden",atBottom);scrollToBottomBtn.classList.toggle("show",!atBottom);}
  });
  scrollToBottomBtn?.addEventListener("click",()=>chatContainer.scrollTop=chatContainer.scrollHeight);

  // ─── Load messages ───────────────────────────────────────────
  function prependMessages(messages) {
    const prevH=chatContainer.scrollHeight, prevT=chatContainer.scrollTop;
    messages.forEach(msg=>{if(!document.getElementById("chat-bubble-"+msg.id)) chatContainer.prepend(renderMessage(msg));});
    chatContainer.scrollTop=chatContainer.scrollHeight-prevH+prevT;
    normalizeSeparators(); _restack(); updateSticky();
  }
  async function loadMoreMessages() {
    if(loading) return; loading=true;
    let url="load/?limit="+LIMIT;
    if(oldestLoaded) url+="&before="+encodeURIComponent(oldestLoaded);
    if(activeRoomId) url+="&room_id="+encodeURIComponent(activeRoomId);
    try {
      const res=await fetch(url), data=await res.json();
      if(data.messages?.length){prependMessages(data.messages);oldestLoaded=data.messages[0].created_at;}
    } catch(e){console.error("loadMore:",e);}
    loading=false;
  }
  function appendMessage(data) {
    if(!data) return;
    const msgRoom = data.room_id != null ? String(data.room_id) : null;
    const curRoom = activeRoomId  != null ? String(activeRoomId) : null;
    if (msgRoom !== curRoom) return;
    if (data.forwarded_from && data.target_room_id != null) {
      const targetRoom = data.target_room_id != null ? String(data.target_room_id) : null;
      if (targetRoom !== curRoom) return;
    }
    if(document.getElementById("chat-bubble-"+data.id)) return;
    if(data.guest){const g=[..._USER_GUESTS,..._UNASSIGNED].find(x=>x.id===(data.guest?.id));if(g)data.guest.assigned_user=g.assigned_user||data.guest.assigned_user||null;}
    chatContainer.appendChild(renderMessage(data));
    chatContainer.scrollTop=chatContainer.scrollHeight;
    normalizeSeparators(); _restack(); updateSticky();
  }
  loadMoreMessages();
  _LAST_MSGS.forEach(data => {
    if (!data) return;
    if (document.getElementById("chat-bubble-" + data.id)) return;
    // Only render messages matching the current active room
    const msgRoom = data.room_id != null ? String(data.room_id) : null;
    const curRoom = activeRoomId != null ? String(activeRoomId) : null;
    if (msgRoom !== curRoom) return;
    if (data.guest) {
      const g = [..._USER_GUESTS, ..._UNASSIGNED].find(x => x.id === (data.guest?.id));
      if (g) data.guest.assigned_user = g.assigned_user || data.guest.assigned_user || null;
    }
    chatContainer.appendChild(renderMessage(data));
  });
  _restack();
  // Auto-scroll to bottom and focus input on initial page load
  scrollToBottomAndFocus();

  // ─── Send ────────────────────────────────────────────────────
  async function sendMessage(text) {
    //if(!_canSendNow) return;
    text=(text??chatInput?.value??"").trim();
    if(!text&&!selectedGuest&&!selectedFile&&!selectedLP) return;
    const mm=detectMentions(text).map(m=>{const u=usersList.find(u=>u.id===m.id);return{...m,color:u?.color||"#00aeffff"};});
    mm.forEach(m=>{if(!mentions.some(x=>x.id===m.id)) mentions.push(m);});
    const payload={sender_id:_CUR_USER,room_id:activeRoomId,reply_to_id:replyToId||null,mentions:mentions.map(m=>m.id),
      guest_id:selectedGuest?parseInt(selectedGuest.id,10):null,
      guest:selectedGuest?{id:parseInt(selectedGuest.id,10),name:selectedGuest.name,custom_id:selectedGuest.custom_id,
        image:selectedGuest.image,title:selectedGuest.title,date_of_visit:selectedGuest.date_of_visit,assigned_user:selectedGuest.assigned_user||null}:null};
    if(text) payload.message=text;
    if(selectedLP) payload.link_preview=selectedLP;
    if(selectedFile){
      const fd=new FormData(); fd.append("file",selectedFile);
      const url=document.getElementById("chatUrlUpload")?.dataset?.url||"/workforce/upload_file/";
      try{const res=await fetch(url,{method:"POST",body:fd});if(!res.ok)throw new Error("Upload failed");payload.file=await res.json();selectedFile=null;}
      catch(err){console.error(err);return;}
    }
    wsSend(payload);
    if(chatInput){chatInput.value="";autoResize(chatInput);}
    removeGuestPreview();removeFilePreview();removeLinkPreview();
    replyToId=null;replyPreview?.classList.add("d-none");if(replyPreviewText)replyPreviewText.innerHTML="";
    mentions=[];
  }

  sendButton?.addEventListener("click", e => { e.preventDefault(); sendMessage(chatInput?.value); });

  // ─── Textarea ────────────────────────────────────────────────
  function autoResize(el) {
    if(!el) return;
    el.style.height="auto"; el.style.height=Math.min(el.scrollHeight,100)+"px";
    el.style.overflowY=el.scrollHeight>100?"auto":"hidden";
  }
  chatInput?.addEventListener("input",()=>autoResize(chatInput));
  autoResize(chatInput);

  chatInput?.addEventListener("keydown", e => {
    const items=mentionDropdown?.querySelectorAll("div[data-id]"), open=items?.length&&mentionDropdown.style.display!=="none";
    if(open){
      if(e.key==="ArrowDown"){e.preventDefault();mentionIdx=(mentionIdx+1)%items.length;updateMentionActive();return;}
      if(e.key==="ArrowUp"){e.preventDefault();mentionIdx=(mentionIdx-1+items.length)%items.length;updateMentionActive();return;}
      if(e.key==="Enter"||e.key==="Tab"){e.preventDefault();selectMention(items[mentionIdx]);return;}
      if(e.key==="Escape"){e.preventDefault();hideMentions();return;}
    }
    if(e.key==="Enter"&&(e.shiftKey||e.ctrlKey)){e.preventDefault();sendMessage(chatInput.value);return;}
    if(e.key==="Enter"&&!e.shiftKey&&!e.ctrlKey){
      e.preventDefault();
      const s=chatInput.selectionStart, before=chatInput.value.slice(0,s), after=chatInput.value.slice(chatInput.selectionEnd);
      const m=before.slice(before.lastIndexOf("\n")+1).match(/^(\s*)(\d+)\.\s/);
      const ins="\n"+(m?m[1]+(parseInt(m[2])+1)+". ":"");
      chatInput.value=before+ins+after;const p=s+ins.length;chatInput.setSelectionRange(p,p);autoResize(chatInput);
    }
  });

  // ─── Mention dropdown ────────────────────────────────────────
  // Build a set of user IDs for the currently active room so @mention
  // list is scoped to room participants only.
  function _getRoomMentionPool() {
    if (!activeRoomId) {
      // General room — all members
      return usersList;
    }
    const roomData = NORMALIZED_CHAT_ROOMS.find(r => r.id === String(activeRoomId));
    if (!roomData || !Array.isArray(roomData.members) || !roomData.members.length) {
      return usersList;
    }
    // Build a Set of user IDs in this room (including global-tier)
    const roomMemberIds = new Set(
      roomData.members.map(m => String(m.user_id || m.id || "")).filter(Boolean)
    );
    DEFAULT_TIER_MEMBERS.forEach(m => {
      const uid = String(m.user_id || m.id || "");
      if (uid) roomMemberIds.add(uid);
    });
    return usersList.filter(u => roomMemberIds.has(String(u.id)));
  }

  chatInput?.addEventListener("input",()=>{
    const pos=chatInput.selectionStart, before=chatInput.value.slice(0,pos), match=before.match(/@([^\s@]*)$/);
    if(match){
      const pool = _getRoomMentionPool();
      filteredUsers=pool.filter(u=>(`${u.title} ${u.full_name}`).toLowerCase().includes(match[1].toLowerCase()));
      mentionIdx=0;showMentions();
    }
    else hideMentions();
    clearTimeout(lpTimer);
    lpTimer=setTimeout(()=>{const word=(chatInput.value.trim().split(/\s+/)[0]||""),url=extractURL(word);if(url&&(!selectedLP||selectedLP.url!==url))fetchLinkPreview(url);else if(!url)removeLinkPreview();},300);
  });

  // Also handle mention typing inside the edit textarea
  document.addEventListener("input", (e) => {
    const ta = e.target;
    if (!ta || !ta.id || !ta.id.startsWith("editIn")) return;
    const pos = ta.selectionStart, before = ta.value.slice(0, pos), match = before.match(/@([^\s@]*)$/);
    if (match) {
      const pool = _getRoomMentionPool();
      filteredUsers = pool.filter(u => (`${u.title} ${u.full_name}`).toLowerCase().includes(match[1].toLowerCase()));
      mentionIdx = 0;
      // Show mention dropdown anchored to textarea
      if (!mentionDropdown || !filteredUsers.length) return hideMentions();
      mentionDropdown.innerHTML = filteredUsers.map((u, i) =>
        `<div class="mention-item ${i===mentionIdx?"active":""}" data-id="${u.id}">` +
        `<span class="avatar" style="${u.image?"background-image:url("+u.image+");background-size:cover;":""}">${!u.image?(u.full_name||u.username).slice(0,2).toUpperCase():""}</span>` +
        `<div class="d-flex flex-column" style="line-height:1.2;"><span style="font-size:.6rem;color:#94a3b8;">${u.title||""}</span><span style="font-size:.8rem;color:#e2e8f0;">${u.full_name}</span></div></div>`
      ).join("");
      mentionDropdown.classList.remove("d-none"); mentionDropdown.style.display = "block";
      mentionDropdown.style.position = "fixed"; mentionDropdown.style.left = "50%"; mentionDropdown.style.transform = "translateX(-50%)";
      mentionDropdown.style.minWidth = Math.min(360, window.innerWidth - 32) + "px"; mentionDropdown.style.zIndex = "9999";
      const ir = ta.getBoundingClientRect(), dh = mentionDropdown.offsetHeight || 200;
      mentionDropdown.style.top = Math.max(8, ir.top - dh - 8) + "px";
      // Override selectMention to insert into this textarea
      mentionDropdown._editTarget = ta;
    } else {
      hideMentions();
    }
  });
  function showMentions() {
    if(!mentionDropdown||!filteredUsers.length) return hideMentions();
    mentionDropdown.innerHTML=filteredUsers.map((u,i)=>`<div class="mention-item ${i===mentionIdx?"active":""}" data-id="${u.id}"><span class="avatar" style="${u.image?"background-image:url("+u.image+");background-size:cover;":""}">${!u.image?(u.full_name||u.username).slice(0,2).toUpperCase():""}</span><div class="d-flex flex-column" style="line-height:1.2;"><span style="font-size:.6rem;color:#94a3b8;">${u.title||""}</span><span style="font-size:.8rem;color:#e2e8f0;">${u.full_name}</span></div></div>`).join("");
    mentionDropdown.classList.remove("d-none"); mentionDropdown.style.display="block";
    mentionDropdown.style.position="fixed"; mentionDropdown.style.left="50%"; mentionDropdown.style.transform="translateX(-50%)";
    mentionDropdown.style.minWidth=Math.min(360,window.innerWidth-32)+"px"; mentionDropdown.style.zIndex="9999";
    if(chatInput){const ir=chatInput.getBoundingClientRect(),dh=mentionDropdown.offsetHeight||200;mentionDropdown.style.top=Math.max(8,ir.top-dh-8)+"px";}
    mentionDropdown._editTarget = null; // reset edit target when triggered from chatInput
  }
  function hideMentions(){if(!mentionDropdown)return;mentionDropdown.classList.add("d-none");mentionDropdown.style.display="none";mentionIdx=0;mentionDropdown._editTarget=null;}
  function updateMentionActive(){mentionDropdown?.querySelectorAll("div[data-id]").forEach((el,i)=>el.classList.toggle("active",i===mentionIdx));}
  function selectMention(item){
    const u=filteredUsers.find(u=>String(u.id)==item?.dataset?.id);if(!u)return;
    const text="@"+(u.title?u.title+" ":"")+u.full_name+" ";
    // Support edit textarea target
    const target = (mentionDropdown && mentionDropdown._editTarget) ? mentionDropdown._editTarget : chatInput;
    if (!target) return;
    const pos=target.selectionStart, before=target.value.slice(0,pos), match=before.match(/@([^\s@]*)$/), start=match?pos-match[0].length:pos;
    target.value=target.value.slice(0,start)+text+target.value.slice(target.selectionEnd);
    const np=start+text.length;target.setSelectionRange(np,np);hideMentions();target.focus();
  }
  mentionDropdown?.addEventListener("mousedown",e=>{e.preventDefault();const i=e.target.closest("div[data-id]");if(i)selectMention(i);});

  // ─── Link preview ────────────────────────────────────────────
  function extractURL(text){if(!text)return null;const m=text.match(/^(https?:\/\/[^\s]+|www\.[^\s]+)$/i);if(!m)return null;return /^https?:\/\//i.test(m[0])?m[0]:"https://"+m[0];}
  async function fetchLinkPreview(url){try{const res=await fetch("/workforce/fetch_link_preview/?url="+encodeURIComponent(url));if(res.ok){const d=await res.json();if(d.title){selectedLP=d;createLinkPreview(d);}}}catch{}}
  function createLinkPreview(pd){
    removeLinkPreview();selectedLP=pd;
    const p=document.createElement("div");p.id="linkPreview";p.className="d-flex align-items-center text-white border-0 p-2 mb-2 rounded-2";p.style.cssText="background:#111827;box-shadow:0 4px 8px #0000004d;";
    let host="";try{host=new URL(pd.url).hostname;}catch{}
    p.innerHTML=(pd.image?`<img src="${pd.image}" style="width:60px;height:60px;border-radius:4px;object-fit:cover;margin-right:10px;">`:`<span style="width:60px;height:60px;border-radius:4px;margin-right:10px;display:inline-flex;align-items:center;justify-content:center;background:#374151;font-size:1.4rem;">🌐</span>`)+`<div class="flex-grow-1 overflow-hidden"><div class="fw-bold text-truncate">${pd.title}</div><div class="text-muted small">${pd.description||""}</div><div class="text-secondary small">${host}</div></div><span id="cancelLinkPreview" style="cursor:pointer;padding:4px;color:#ef4444;flex-shrink:0;">✖</span>`;
    document.getElementById("guestPreviewContainer")?.appendChild(p);
    document.getElementById("cancelLinkPreview")?.addEventListener("click",removeLinkPreview);
  }
  function removeLinkPreview(){document.getElementById("linkPreview")?.remove();selectedLP=null;}

  // ─── File attachment ─────────────────────────────────────────
  document.getElementById("attachFileBtn")?.addEventListener("click",()=>document.getElementById("fileInput")?.click());
  document.getElementById("fileInput")?.addEventListener("change",e=>{const f=e.target.files[0];if(!f)return;createFilePreview(f);chatInput?.focus();e.target.value="";});
  function createFilePreview(file){
    removeFilePreview();selectedFile=file;
    const type=(file.type||"").toLowerCase(),ext=file.name.split(".").pop().toLowerCase(),url=URL.createObjectURL(file);
    const thumb=type.startsWith("image/")?`<img src="${url}" style="max-width:60px;max-height:60px;object-fit:cover;border-radius:4px;margin-right:10px;">`:type.startsWith("video/")?`<video src="${url}" style="max-width:60px;max-height:60px;border-radius:4px;margin-right:10px;" muted></video>`:type.startsWith("audio/")?`<audio src="${url}" style="width:120px;margin-right:10px;" controls></audio>`:`<div style="width:60px;height:60px;display:flex;align-items:center;justify-content:center;background:#1f2937;border-radius:6px;margin-right:10px;">${fileIcon(ext,type)}</div>`;
    const p=document.createElement("div");p.id="filePreview";p.className="d-flex align-items-center text-white border-0 p-2 mb-2 rounded-2";p.style.cssText="background:#111827;box-shadow:0 4px 8px #0000004d;";
    p.innerHTML=`${thumb}<div class="flex-grow-1 overflow-hidden"><strong class="text-white text-truncate d-block" style="max-width:160px;">${file.name}</strong><small class="text-muted">${(file.size/1024).toFixed(1)} KB</small></div><span id="cancelFilePreview" style="cursor:pointer;padding:4px;color:#ef4444;flex-shrink:0;">✖</span>`;
    document.getElementById("guestPreviewContainer")?.appendChild(p);
    document.getElementById("cancelFilePreview")?.addEventListener("click",removeFilePreview);
  }
  function removeFilePreview(){document.getElementById("filePreview")?.remove();selectedFile=null;}

  // ─── Guest popup ─────────────────────────────────────────────
  function renderGuestList(filter){
    if(!popupBody)return;popupBody.innerHTML="";
    const f=filter.toLowerCase(),gm={};_USER_GUESTS.forEach(u=>gm[u.id]=u.guests||[]);
    let vis=_USERS;if(!_IS_ADMIN&&!_CAN_MANAGE_G)vis=vis.filter(u=>u.id===_CUR_USER);
    vis.forEach(user=>{
      let guests=[...(gm[user.id]||[])];if(user.id===_CUR_USER&&_CAN_MANAGE_G)guests=guests.concat(_UNASSIGNED);
      const fg=guests.filter(g=>(g.name||"").toLowerCase().includes(f));
      if(!(user.full_name||"").toLowerCase().includes(f)&&!fg.length)return;
      const d=document.createElement("div");d.className="p-2";
      d.innerHTML=`<div class="d-flex align-items-center gap-2 fw-bold text-secondary small mb-1">${user.image?`<img src="${user.image}" style="width:24px;height:24px;border-radius:4px;object-fit:cover;">`:""} ${user.full_name||""}</div>`+fg.map(g=>`<div class="ps-3 py-1 border-start border-secondary" style="cursor:pointer;" data-guest='${JSON.stringify(g).replace(/'/g,"&#39;")}'><span class="small">${g.custom_id?`<span class="text-muted font-monospace">${g.custom_id}</span> `:""} ${g.title||""} ${g.name}</span></div>`).join("");
      popupBody.appendChild(d);
      d.querySelectorAll("[data-guest]").forEach(item=>item.addEventListener("click",()=>{createGuestPreview(JSON.parse(item.dataset.guest));guestPopup?.classList.add("d-none");}));
    });
  }
  openGuestBtn?.addEventListener("click",e=>{e.preventDefault();guestPopup?.classList.remove("d-none");renderGuestList("");});
  popupClose?.addEventListener("click",()=>guestPopup?.classList.add("d-none"));
  guestPopup?.querySelector?.(".popup-backdrop")?.addEventListener("click",()=>guestPopup?.classList.add("d-none"));
  popupSearch?.addEventListener("input",e=>renderGuestList(e.target.value));
  if(_CAN_GUESTS) document.querySelectorAll(".guest-controls").forEach(el=>el.style.display="");
  function createGuestPreview(guest){
    removeGuestPreview();const m=[..._USER_GUESTS,..._UNASSIGNED].find(g=>g.id===guest.id);guest.assigned_user=m?.assigned_user||null;selectedGuest=guest;
    const p=document.createElement("div");p.id="guestPreview";p.className="d-flex align-items-center text-white border-0 p-2 mb-2 rounded-2";p.style.cssText="background:#111827;box-shadow:0 4px 8px #0000004d;";
    p.innerHTML=(guest.image?`<img src="${guest.image}" style="width:60px;height:60px;border-radius:6px;object-fit:cover;margin-right:10px;">`:`<span style="width:60px;height:60px;border-radius:4px;margin-right:10px;display:inline-flex;align-items:center;justify-content:center;background:#374151;font-size:1.4rem;">${(guest.name||"?")[0]}</span>`)+`<div class="flex-grow-1"><strong class="text-warning">${guest.title||""} ${guest.name}</strong><br><small class="text-muted">${guest.custom_id||""} · ${guest.date_of_visit||""}</small></div><span id="cancelGuestPreview" style="cursor:pointer;padding:4px;color:#ef4444;flex-shrink:0;">✖</span>`;
    document.getElementById("guestPreviewContainer")?.appendChild(p);
    document.getElementById("cancelGuestPreview")?.addEventListener("click",removeGuestPreview);
    chatInput?.focus();
  }
  function removeGuestPreview(){document.getElementById("guestPreview")?.remove();selectedGuest=null;}
  const pendingGuest=localStorage.getItem("attachGuest");
  if(pendingGuest){try{createGuestPreview(JSON.parse(pendingGuest));}catch{}localStorage.removeItem("attachGuest");}

  // ─── Action buttons ──────────────────────────────────────────
  document.getElementById("replyBtn")?.addEventListener("click",()=>{
    if(!selectedBubbles.size)return;
    const id=[...selectedBubbles].pop(),node=document.getElementById("chat-bubble-"+id);if(!node)return;
    const b=node.querySelector(".chat-bubble");if(b)popAnim(b);
    setReply({id,sender_id:node.dataset.senderId,sender_title:node.dataset.senderTitle,sender_name:node.dataset.senderName,message:node.dataset.rawMessage||node.querySelector(".message-text")?.textContent?.trim()||"",guest:node.dataset.guest?JSON.parse(node.dataset.guest):null});
    clearSelections();
  });
  document.getElementById("copyBtn")?.addEventListener("click",async()=>{
    if(!selectedBubbles.size)return;
    const parts=[];
    for(const id of selectedBubbles){const node=document.getElementById("chat-bubble-"+id);if(!node)continue;const text=node.querySelector(".message-text")?.textContent?.trim()||"",iso=node.querySelector("[data-full-date]")?.dataset?.fullDate||"",owner=String(node.dataset.senderId)===String(_CUR_USER)?"You":`${node.dataset.senderTitle||""} ${node.dataset.senderName||""}`.trim();parts.push(fmtCopy(iso)+" — "+owner+":\n"+text);}
    await navigator.clipboard.writeText(parts.join("\n"));
    const btn=document.getElementById("copyBtn");if(btn){const o=btn.innerHTML;btn.textContent="✓ Copied";setTimeout(()=>btn.innerHTML=o,1200);}
    clearSelections();
  });
  document.getElementById("editBtn")?.addEventListener("click",()=>{
    if(selectedBubbles.size!==1)return;
    const mid=[...selectedBubbles][0],node=document.getElementById("chat-bubble-"+mid);
    const item = node?.closest(".chat-item");
    const sent = item ? safeDate(item.dataset.createdAt) : null;
    // FIX: Double-check edit window at click time (belt-and-suspenders)
    if (!sent || (Date.now() - sent.getTime()) >= EDIT_WINDOW_MS) return;
    const t=node?.querySelector(".message-text"); if(!t)return;
    const tClone = t.cloneNode(true);
    tClone.querySelectorAll(".edited-tag, .badge").forEach(el => el.remove());
    const orig = node.dataset.rawMessage || tClone.textContent.trim();
    node.dataset.rawMessage = orig;
    t.innerHTML=`<div class="edit-compose-wrap">
      <textarea id="editIn${mid}" class="form-control form-control-sm border-0 bg-transparent text-white w-100"
        rows="1" style="resize:none;max-height:4.5rem;overflow-y:auto;line-height:1.5;font-size:inherit;"
      >${orig.replace(/</g,"&lt;")}</textarea>
      <div class="d-flex gap-2 mt-1 justify-content-end">
        <button class="btn btn-sm btn-success py-0 px-2" id="editSave${mid}">Save</button>
        <button class="btn btn-sm btn-secondary py-0 px-2" id="editCancel${mid}">✕</button>
      </div>
    </div>`;
    const ta = document.getElementById("editIn"+mid);
    function growTA() { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 72) + "px"; }
    ta?.addEventListener("input", growTA);
    requestAnimationFrame(()=>{ growTA(); ta?.focus(); ta?.setSelectionRange(ta.value.length, ta.value.length); });
    document.getElementById("editSave"+mid)?.addEventListener("click",()=>{
      const nv = document.getElementById("editIn"+mid)?.value.trim();
      if(!nv||nv===orig){ t.innerHTML=linkifyText(formatMsg(orig)); return; }
      wsSend({action:"edit",message_id:mid,message:nv,sender_id:_CUR_USER});
      node.dataset.rawMessage = nv;
      const diffHTML = _diffHighlight(orig, nv);
      t.innerHTML = diffHTML;
    });
    document.getElementById("editCancel"+mid)?.addEventListener("click",()=>{ t.innerHTML = linkifyText(formatMsg(orig)); });
    clearSelections();
  });
  document.getElementById("deleteBtn")?.addEventListener("click",()=>{
    if(!selectedBubbles.size)return;
    const count=selectedBubbles.size;
    let delModal=document.getElementById("deleteConfirmModal");
    if(!delModal){delModal=document.createElement("div");delModal.id="deleteConfirmModal";delModal.className="fullscreen-modal";document.body.appendChild(delModal);}
    delModal.style.display="flex";
    delModal.innerHTML=`<div class="modal-box" style="max-width:360px;text-align:center;">
      <div style="font-size:2rem;margin-bottom:8px;">
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M20 6a1 1 0 0 1 .117 1.993l-.117.007h-.081l-.919 11a3 3 0 0 1-2.824 2.995l-.176.005h-8c-1.598 0-2.904-1.249-2.992-2.75l-.005-.167-.923-11.083h-.08a1 1 0 0 1-.117-1.993l.117-.007zm-10 4a1 1 0 0 0-1 1v6a1 1 0 0 0 2 0v-6a1 1 0 0 0-1-1m4 0a1 1 0 0 0-1 1v6a1 1 0 0 0 2 0v-6a1 1 0 0 0-1-1"/><path d="M14 2a2 2 0 0 1 2 2a1 1 0 0 1-1.993.117l-.007-.117h-4l-.007.117a1 1 0 0 1-1.993-.117a2 2 0 0 1 1.85-1.995l.15-.005z"/></svg>
      </div>
      <h3 class="text-white fw-bold mb-2">Delete ${count} message${count!==1?"s":""}?</h3>
      <div class="alert alert-warning py-2 justify-content-center fst-italic fw-bold" style="border-radius:24px;box-shadow:inset 0 4px 8px #000000d2;">
        This action <strong class="text-red">CANNOT</strong> be undone!
      </div>
      <div class="d-flex gap-2">
        <button id="delConfirm" class="btn bg-danger-lt flex-fill" style="box-shadow:0 -4px 8px #000000d2;border-radius:24px;">Delete</button>
        <button id="delCancel" class="btn bg-secondary-lt flex-fill" style="box-shadow:0 -4px 8px #000000d2;border-radius:24px;">Cancel</button>
      </div>
    </div>`;
    const doDelete=()=>{
      [...selectedBubbles].forEach(mid=>{
        wsSend({action:"delete",message_id:mid,sender_id:_CUR_USER});
        const b=document.getElementById("chat-bubble-"+mid),t=b?.querySelector(".message-text");
        if(t){t.innerHTML=`<em class="text-muted small">Message deleted</em>`;b.classList.add("deleted-msg");}
      });
      clearSelections(); delModal.style.display="none";
    };
    delModal.querySelector("#delConfirm").onclick=doDelete;
    delModal.querySelector("#delCancel").onclick=()=>delModal.style.display="none";
    delModal.onclick=e=>{if(e.target===delModal)delModal.style.display="none";};
  });
  document.getElementById("forwardBtn")?.addEventListener("click", () => {
    if (!selectedBubbles.size) return;
    const ids = [...selectedBubbles];
    const currentRoomStr  = String(activeRoomId || "");
    const currentRoomData = NORMALIZED_CHAT_ROOMS.find(r => r.id === currentRoomStr);
    const currentIsSubRoom = !!(currentRoomData && currentRoomData.parent_room_id);
    const currentParentId  = currentIsSubRoom ? String(currentRoomData.parent_room_id) : null;
    const fwdRooms = NORMALIZED_CHAT_ROOMS.filter(r => {
      if (String(r.id) === currentRoomStr) return false;
      if (r.parent_room_id) {
        if (currentIsSubRoom) return String(r.parent_room_id) === currentParentId;
        else return String(r.parent_room_id) === currentRoomStr;
      }
      if (currentIsSubRoom) return String(r.id) === currentParentId;
      return true;
    });
    let modal = document.getElementById("forwardModal");
    if (!modal) { modal = document.createElement("div"); modal.id = "forwardModal"; modal.className = "fullscreen-modal"; document.body.appendChild(modal); }
    modal.style.display = "flex";
    const optionsHTML = [
      ...(activeRoomId && !currentIsSubRoom ? [`<option value="__general__">General Workforce</option>`] : []),
      ...fwdRooms.map(r => { const isSubRoom = !!r.parent_room_id; const label = isSubRoom ? `# ${r.name}` : r.name; return `<option value="${r.id}">${label}</option>`; }),
    ].join("");
    modal.innerHTML = `<div class="modal-box" style="box-shadow:0 4px 8px #000000d2;border-radius:36px;">
      <h3 class="text-white fw-bold mb-3">Forward to…</h3>
      <select id="forwardSel" class="form-select bg-dark text-white mb-3" style="box-shadow:inset 0 4px 8px #000000d2;border-radius:24px;">${optionsHTML}</select>
      <div class="d-flex gap-2">
        <button id="fwdConfirm" class="btn bg-success-lt flex-fill" style="box-shadow:0 4px 8px #000000d2;border-radius:24px;">Forward</button>
        <button id="fwdCancel" class="btn bg-secondary-lt flex-fill" style="box-shadow:0 4px 8px #000000d2;border-radius:24px;">Cancel</button>
      </div>
    </div>`;
    modal.querySelector("#fwdConfirm").onclick = () => {
      const selVal = modal.querySelector("#forwardSel").value;
      const targetRoomId = selVal === "__general__" ? null : parseInt(selVal, 10);
      const curRoomData = activeRoomId ? NORMALIZED_CHAT_ROOMS.find(r => String(r.id) === String(activeRoomId)) : null;
      const fromRoomName = curRoomData ? curRoomData.name : "General Workforce";
      const fromRoomId   = activeRoomId || null;
      ids.forEach(id => {
        wsSend({ action: "forward", message_id: id, target_room_id: targetRoomId, sender_id: _CUR_USER, forwarded_from_room_id: fromRoomId, forwarded_from_room_name: fromRoomName });
        const node  = document.getElementById("chat-bubble-" + id);
        const flags = node?.querySelector(".chat-bubble-flags");
        if (flags && !node.querySelector(".fwd-flag")) {
          const s = document.createElement("span"); s.className = "fwd-flag"; s.title = `Forwarded to ${selVal === "__general__" ? "General" : (NORMALIZED_CHAT_ROOMS.find(r=>String(r.id)===selVal)?.name || selVal)}`;
          s.dataset.fwdFromRoom = fromRoomName;
          s.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke=" #ffe600" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-tabler icons-tabler-outline icon-tabler-arrow-forward-up-double"><path stroke="none" d="M0 0h24v24H0z" fill="none" /><path d="M11 14l4 -4l-4 -4" /><path d="M16 14l4 -4l-4 -4" /><path d="M15 10h-7a4 4 0 1 0 0 8h1" /></svg>`;
          flags.appendChild(s);
          node.dataset.forwarded = "1";
          node.dataset.forwardedFromRoom = fromRoomName;
        }
      });
      modal.style.display = "none"; clearSelections();
    };
    modal.querySelector("#fwdCancel").onclick = () => { modal.style.display = "none"; clearSelections(); };
    modal.onclick = e => { if (e.target === modal) { modal.style.display = "none"; clearSelections(); } };
  });

  // ─── Private chat incoming ───────────────────────────────────
  function handlePrivateIncoming(data) {
    const senderId    = String(data.sender_id || data.sender_user_id || "");
    const isMe        = senderId === String(_CUR_USER);
    const otherUserId = isMe
      ? String(data.recipient_user_id || data.recipient || "")
      : senderId;
    const key        = _pcKey(otherUserId);
    const senderName = data.sender_title ? data.sender_title + " " + (data.sender_name || "") : (data.sender_name || "");
    const ts = fmtTime(data.created_at || new Date().toISOString());
    const seenKey = data.client_id ? `client:${data.client_id}` : (data.id ? `db:${data.id}` : `${senderId}:${data.created_at}:${data.message}`);
    const thread = _pcEnsureThread(key, senderName);
    if (thread.seen.has(seenKey)) return;
    thread.seen.add(seenKey);
    thread.messages.push({ id: data.id, client_id: data.client_id, isMe, text: data.message || "", ts });
    const msgs = document.getElementById("pcMessages");
    if (_pcOpenId === key && msgs) {
      const div = document.createElement("div"); div.className = isMe ? "text-end my-1" : "text-start my-1";
      div.innerHTML = `<span class="badge px-3 py-2 rounded-3 text-white" style="max-width:85%;word-break:break-word;white-space:normal;background:${isMe ? "#166534" : "#374151"};">${data.message}</span>
        <div style="font-size:.6rem;color:#64748b;margin-top:1px;">${ts}</div>`;
      msgs.appendChild(div); msgs.scrollTop = msgs.scrollHeight;
    } else if (!isMe) {
      _PC_UNREAD[key] = (_PC_UNREAD[key] || 0) + 1;
      _pcBadgeUpdate(otherUserId);
    }
  }
  window.handlePrivateMessage = handlePrivateIncoming;
  window.handlePrivateMessage_cr = handlePrivateIncoming;
  _pcLoadUnreadCounts();

  // ─── Search ──────────────────────────────────────────────────
  document.querySelectorAll(".chat-search").forEach(inp=>inp.addEventListener("input",function(){
    const q=this.value.toLowerCase();
    chatContainer.querySelectorAll(".chat-item").forEach(i=>i.style.display=i.innerText.toLowerCase().includes(q)?"":"none");
  }));

  // ─── Mobile VH ───────────────────────────────────────────────
  function setVH(){document.documentElement.style.setProperty("--vh",window.innerHeight*0.01+"px");}
  window.addEventListener("resize",setVH);window.addEventListener("orientationchange",setVH);setVH();

});
