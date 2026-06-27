document.addEventListener('DOMContentLoaded', () => {

  /* =========================
     1️⃣ DROPDOWN Z-INDEX MANAGEMENT & SMOOTH DROPUP
     ========================= */
  function updateDropdownZIndex(dropdown) {
    if (!dropdown) return;
    const navbar = document.querySelector('.navbar');
    const pageHeader = document.querySelector('.page-header');
    let baseZ = 1050;
    if (navbar && navbar.contains(dropdown)) baseZ = 2000;
    else if (pageHeader && pageHeader.contains(dropdown)) baseZ = 1110;
    const menu = dropdown.querySelector('.dropdown-menu');
    if (menu) menu.style.zIndex = baseZ.toString();
  }

  function handleDropdownBehavior(dropdown, toggle) {
    if (!dropdown || !toggle) return;
    const menu = dropdown.querySelector('.dropdown-menu');
    if (!menu) return;

    function adjustDropup() {
      const rect = dropdown.getBoundingClientRect();
      const spaceBelow = (window.innerHeight || document.documentElement.clientHeight) - rect.bottom;
      dropdown.classList.toggle('dropup', spaceBelow < 200);
    }

    toggle.addEventListener('click', () => requestAnimationFrame(adjustDropup));
  }

  document.querySelectorAll('[data-bs-toggle="dropdown"]').forEach(toggle => {
    const dropdown = toggle.closest('.dropdown');
    toggle.addEventListener('show.bs.dropdown', () => updateDropdownZIndex(dropdown));
    handleDropdownBehavior(dropdown, toggle);
  });

  window.addEventListener('resize', () => {
    document.querySelectorAll('[data-bs-toggle="dropdown"]').forEach(toggle => {
      const dropdown = toggle.closest('.dropdown');
      updateDropdownZIndex(dropdown);
    });
  });

  /* =========================
     2️⃣ Dropend hover behavior for desktop
     ========================= */
  document.querySelectorAll('.dropend').forEach(dropend => {
    const toggle = dropend.querySelector('[data-bs-toggle="dropdown"]');
    if (!toggle) return;
    dropend.addEventListener('mouseenter', () => {
      if (window.innerWidth >= 992) bootstrap.Dropdown.getOrCreateInstance(toggle).show();
    });
    dropend.addEventListener('mouseleave', () => {
      if (window.innerWidth >= 992) bootstrap.Dropdown.getInstance(toggle)?.hide();
    });
  });

  /* =========================
     3️⃣ HEADER FILTER DROPDOWN FLOATING FIX
     ========================= */
  document.querySelectorAll('.page-header .dropdown-toggle').forEach(toggle => {
    const dropdown = toggle.closest('.dropdown');
    const menu = dropdown?.querySelector('.dropdown-menu');
    if (!dropdown || !menu) return;

    toggle.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      document.body.appendChild(menu);
      menu.style.position = 'absolute';
      menu.style.minWidth = `${dropdown.offsetWidth}px`;
      const rect = toggle.getBoundingClientRect();
      menu.style.top = `${rect.bottom + window.scrollY}px`;
      menu.style.left = `${rect.left + window.scrollX}px`;
      menu.classList.toggle('show');

      function closeMenu(event) {
        const target = event.target;
        if (!menu.contains(target) && !toggle.contains(target)) {
          menu.classList.remove('show');
          dropdown.appendChild(menu);
          document.removeEventListener('click', closeMenu);
        }
      }
      document.addEventListener('click', closeMenu);
    });
  });

  /* =========================
     4️⃣ TOOLTIP INIT
     ========================= */
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el));

  /* =========================
     5️⃣ TOP SERVICES PROGRESS BARS
     ========================= */
  fetch(window.APP_CONFIG.urls.topServicesData)
    .then(res => res.json())
    .then(data => {
      const services = data.services || [];
      const progressContainer = document.getElementById('topServicesProgress');
      const labelsContainer = document.getElementById('topServicesLabels');
      if (!progressContainer || !labelsContainer) return;

      progressContainer.innerHTML = '';
      labelsContainer.innerHTML = '';
      const colors = ['primary','info','success','danger','warning','secondary','dark','muted','teal','pink'];

      services.forEach((service, i) => {
        const colorClass = `bg-${colors[i % colors.length]}`;
        const progressBar = document.createElement('div');
        progressBar.className = `progress-bar ${colorClass}`;
        progressBar.style.width = `${service.percent}%`;
        progressBar.setAttribute('role','progressbar');
        progressBar.setAttribute('aria-valuenow',service.percent);
        progressBar.setAttribute('aria-valuemin','0');
        progressBar.setAttribute('aria-valuemax','100');
        progressBar.setAttribute('data-bs-toggle','tooltip');
        progressBar.title = `${service.service_attended||'No Service'}: ${service.count} guests`;
        progressContainer.appendChild(progressBar);

        const labelCol = document.createElement('div');
        labelCol.className = 'col-auto d-flex align-items-center';
        const legendBox = document.createElement('span');
        legendBox.className = `legend me-2 ${colorClass}`;
        const labelText = document.createElement('span');
        labelText.textContent = service.service_attended || 'No Service';
        const countText = document.createElement('span');
        countText.className = 'd-none d-md-inline d-lg-none d-xxl-inline ms-2 text-secondary';
        countText.textContent = `${service.count} guests`;
        labelCol.append(legendBox,labelText,countText);
        labelsContainer.appendChild(labelCol);
      });

      document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el));
    })
    .catch(err => console.error('Failed to fetch top services:', err));

  /* =========================
     6️⃣ ANIMATED COUNTERS
     ========================= */
  document.querySelectorAll('[data-count]').forEach(el => {
    const endValue = parseInt(el.getAttribute('data-count')||'0',10);
    let startTime = null;
    function step(timestamp) {
      if (!startTime) startTime = timestamp;
      const progress = Math.min((timestamp-startTime)/1500,1);
      el.textContent = Math.floor(progress*endValue).toString();
      if (progress<1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  });

  /* =========================
     7️⃣ CHANNEL OF VISIT TABLE
     ========================= */
  fetch(window.APP_CONFIG.urls.channelBreakdown)
    .then(res => res.json())
    .then(data => {
      const tbody = document.getElementById('channelProgressTableBody');
      if (!tbody) return;
      tbody.innerHTML = '';
      if (!data.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted">No data available</td></tr>';
        return;
      }
      data.forEach(({label,count,percent}) => {
        tbody.innerHTML += `
          <tr>
            <td>${label}</td>
            <td>${count}</td>
            <td class="w-50">
              <div class="progress progress-xs">
                <div class="progress-bar bg-primary" style="width:${percent}%"></div>
              </div>
            </td>
          </tr>`;
      });
    })
    .catch(err => console.error('Channel table load error:', err));

  /* =========================
     8️⃣ SOCIAL MEDIA FIELD HANDLING
     ========================= */
  (function(){
    const baseUrls = {
      linkedin: 'https://www.linkedin.com/in/',
      whatsapp: 'https://wa.me/',
      instagram: 'https://www.instagram.com/',
      twitter: 'https://twitter.com/',
      tiktok: 'https://www.tiktok.com/@',
    };

    const container = document.getElementById('socialMediaFieldsContainer');
    const addButton = document.getElementById('addSocialMediaField');
    const form = document.querySelector('form');
    if(!container || !addButton) return;
    if (container.dataset.smBound === '1') return;
    container.dataset.smBound = '1';

    const defaultSocialIcon = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-tabler icons-tabler-outline icon-tabler-restore"><path stroke="none" d="M0 0h24v24H0z" fill="none" /><path d="M3.06 13a9 9 0 1 0 .49 -4.087" /><path d="M3 4.001v5h5" /><path d="M11 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0" /></svg>`;
    const platformIcons = {
      linkedin: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" stroke="#037ae9ff" stroke-width="2"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M8 11v5"/><path d="M8 8v.01"/><path d="M12 16v-5"/><path d="M16 16v-3a2 2 0 1 0-4 0"/><path d="M3 7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4h-10a4 4 0 0 1-4-4z"/></svg>`,
      whatsapp: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" stroke="#03f74cff" stroke-width="2"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M3 21l1.65-3.8a9 9 0 1 1 3.4 2.9l-5.05.9"/><path d="M9 10a.5.5 0 0 0 1 0v-1a.5.5 0 0 0-1 0v1a5 5 0 0 0 5 5h1a.5.5 0 0 0 0-1h-1a.5.5 0 0 0 0 1"/></svg>`,
      instagram: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" stroke="#de08f1ff" stroke-width="2"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M4 8a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v8a4 4 0 0 1-4 4h-8a4 4 0 0 1-4-4z"/><path d="M9 12a3 3 0 1 0 6 0a3 3 0 0 0-6 0"/><path d="M16.5 7.5v.01"/></svg>`,
      twitter: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="#ffffffff" stroke="#000000ff" stroke-width="2"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M4 4l11.733 16h4.267l-11.733-16z"/><path d="M4 20l6.768-6.768m2.46-2.46l6.772-6.772"/></svg>`,
      tiktok: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" stroke="#25ECE6" stroke-width="2"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M21 7.917v4.034a9.948 9.948 0 0 1-5-1.951v4.5a6.5 6.5 0 1 1-8-6.326v4.326a2.5 2.5 0 1 0 4 2v-11.5h4.083a6.005 6.005 0 0 0 4.917 4.917z"/></svg>`,
    };
    const iconFor = (type) => platformIcons[type] || defaultSocialIcon;

    function updateResetVisibility(field) {
      const typeInput = field.querySelector('input[name="social_media_type[]"]');
      const menu = field.querySelector('.dropdown-menu');
      if (!menu) return;
      let resetOption = field.querySelector('.social-media-option[data-type=""]');
      if (!resetOption) {
        const li = document.createElement('li');
        li.innerHTML = '<a class="dropdown-item social-media-option" href="#" data-type="" style="color:#f59f00;font-weight:600;">Reset</a>';
        menu.appendChild(li);
        resetOption = li.querySelector('.social-media-option');
      }
      // Keep reset visible so users can always clear selected platform.
      resetOption.style.display = '';
      resetOption.style.color = '#f59f00';
      resetOption.style.fontWeight = '600';
      // Keep title in sync with current row state.
      if (!typeInput?.value) resetOption.title = 'Clear platform selection';
    }

    function updateDropdownLogo(field){
      const typeInput = field.querySelector('input[name="social_media_type[]"]');
      const dropdownBtn = field.querySelector('button.socialMediaDropdown');
      if (!dropdownBtn) return;
      if(typeInput?.value){
        dropdownBtn.innerHTML = iconFor(typeInput.value) + '<span class="visually-hidden">Toggle Dropdown</span>';
        return;
      }
      dropdownBtn.innerHTML = defaultSocialIcon + '<span class="visually-hidden">Toggle Dropdown</span>';
      updateResetVisibility(field);
    }

    function toggleAddButton(){
      // Keep visible so users can add more rows immediately.
      addButton.style.display = 'inline-block';
    }

    container.querySelectorAll('.social-media-field').forEach(updateDropdownLogo);
    toggleAddButton();

    // Ensure only one click handler even if scripts are re-applied.
    const addButtonFresh = addButton.cloneNode(true);
    addButton.parentNode.replaceChild(addButtonFresh, addButton);
    addButtonFresh.addEventListener('click', () => {
      const firstChild = container.firstElementChild;
      if(!firstChild) return;
      const menu = firstChild.querySelector('.dropdown-menu');
      const menuHtml = menu ? menu.innerHTML : '';
      const newField = document.createElement('div');
      newField.className = 'social-media-field mb-2';
      newField.innerHTML = `
        <div class="input-group input-group-flat flex-grow-1 position-relative" style="width: 100%;">
          <button type="button" class="btn bg-grey text-white border-0 d-flex align-items-center justify-content-center socialMediaDropdown" data-bs-toggle="dropdown" aria-expanded="false" style="width: 38px; height: 38px; padding: 0;">
            ${defaultSocialIcon}<span class="visually-hidden">Toggle Dropdown</span>
          </button>
          <input type="text" name="social_media_handle[]" class="form-control bg-grey text-white border-0" placeholder="Enter Social Media Handle/Link" value="">
          <input type="hidden" name="social_media_type[]" value="">
          <ul class="dropdown-menu dropdown-menu-end">${menuHtml}</ul>
        </div>
      `;
      container.appendChild(newField);
      updateDropdownLogo(newField);
      toggleAddButton();
    });

    container.addEventListener('click', e=>{
      const optionEl = e.target.closest('.social-media-option');
      if(!optionEl) return;
      e.preventDefault();
      const selectedType = optionEl.getAttribute('data-type') || '';
      const fieldGroup = optionEl.closest('.social-media-field');
      const typeInput = fieldGroup.querySelector('input[name="social_media_type[]"]');
      const handleInput = fieldGroup.querySelector('input[name="social_media_handle[]"]');
      const dropdownBtn = fieldGroup.querySelector('button.socialMediaDropdown');
      if(typeInput) typeInput.value = selectedType;
      if(dropdownBtn) dropdownBtn.innerHTML = iconFor(selectedType) + '<span class="visually-hidden">Toggle Dropdown</span>';
      if(handleInput){
        if(!selectedType){
          handleInput.value = '';
          handleInput.placeholder = 'Enter Social Media Handle/Link';
          handleInput.focus();
          toggleAddButton();
          return;
        }
        let handle = handleInput.value.trim();
        for(const [type,url] of Object.entries(baseUrls)) if(handle.startsWith(url)) handle=handle.slice(url.length);
        handleInput.value = handle;
        handleInput.placeholder = selectedType && baseUrls[selectedType] ? baseUrls[selectedType] : 'Enter Social Media Handle/Link';
        handleInput.focus();
      }
      updateResetVisibility(fieldGroup);
      toggleAddButton();
    });

    if(form){
      form.addEventListener('submit', ()=>{
        const allTypeInputs = form.querySelectorAll('input[name="social_media_type[]"]');
        const allHandleInputs = form.querySelectorAll('input[name="social_media_handle[]"]');
        allTypeInputs.forEach((typeInput,i)=>{
          const type=typeInput.value;
          let handle = allHandleInputs[i].value.trim();
          if(type && baseUrls[type] && !handle.startsWith(baseUrls[type])) allHandleInputs[i].value=baseUrls[type]+handle;
        });
      });
    }

  })();

  /* =========================
     9️⃣ GUEST DETAIL MODAL
     ========================= */
  const modalEl = document.getElementById('guestDetailModal');
  if(modalEl){
    const modal = new bootstrap.Modal(modalEl);
    const modalBody = modalEl.querySelector('.modal-body');
    document.querySelectorAll('.guest-name-link').forEach(link=>{
      link.addEventListener('click', e=>{
        e.preventDefault();
        const url = link.dataset.detailUrl || '';
        modalBody.innerHTML='<div class="text-center py-5">Loading...</div>';
        modal.show();
        if(!url){
          modalBody.innerHTML='<div class="text-center text-muted py-5">No data available</div>';
          return;
        }
        fetch(url).then(r=>r.text())
          .then(html=>{ modalBody.innerHTML = html||'<div class="text-center text-muted py-5">No data available</div>'; })
          .catch(err=>{ console.error('Modal load error:',err); modalBody.innerHTML='<div class="text-center text-danger py-5">Failed to load guest details</div>'; });
      });
    });
  }

  /* =========================
     🔟 PWA SERVICE WORKER
     ========================= */
  // Convert Base64 URL-safe string to Uint8Array
  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }

  // Helper: get CSRF token
  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
  }

  if ("serviceWorker" in navigator && "PushManager" in window) {
    window.addEventListener("load", async () => {
      try {
        // Register SW
        const swRegistration = await navigator.serviceWorker.register("/sw.js");
        console.log("Service Worker registered:", swRegistration);

        // Refresh page if a new service worker activates
        navigator.serviceWorker.addEventListener("controllerchange", () => {
          window.location.reload();
        });

        // Request notification permission
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          console.warn("Notification permission denied");
          return;
        }

        // Grab key injected from template
        const vapidPublicKey = window.VAPID_PUBLIC_KEY;
        console.log("Using VAPID key:", vapidPublicKey, "Length:", vapidPublicKey.length);

        // Convert and subscribe
        const applicationServerKey = urlBase64ToUint8Array(vapidPublicKey);
        const subscription = await swRegistration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey
        });

        // Send subscription to backend
        await fetch("/notifications/save-subscription/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCookie("csrftoken"),
          },
          body: JSON.stringify(subscription),
        });

        console.log("Push subscription saved:", subscription);
      } catch (err) {
        console.error("Push setup failed:", err);
      }
    });
  }

  /* =========================
     1️⃣1️⃣ NOTIFICATIONS
     ========================= */
  (() => {
    let notifSocket = null;
    let audioUnlocked = false;

    const notifSound = document.getElementById("notifSound");
    const previewAudio = document.getElementById("previewSound");
    const settingsForm = document.getElementById("notif-settings-form");
    const soundSelect = document.getElementById("id_notification_sound");
    const previewBtn = document.getElementById("preview-sound-btn");

    const { userSound, soundMap, urls, csrfToken, settings } = window.APP_CONFIG || {};
    let vibrationEnabled = settings?.vibration_enabled || false;

    // =========================
    // SOUND HANDLING
    // =========================
    function setNotificationSound(soundKey) {
      const map = window.APP_CONFIG?.sound?.soundMap || {};
      const defaultSrc = Object.values(map)[0] || "";
      const src = map[soundKey] || defaultSrc;

      if (!src) {
        console.warn("⚠️ No sound source found for:", soundKey);
        return;
      }

      notifSound.src = src;
      notifSound.load();
    }

    // Initialize with user’s saved sound
    const initialSound = window.APP_CONFIG?.sound?.userSound || "chime1";
    setNotificationSound(initialSound);

    function unlockAudio() {
      if (audioUnlocked) return;
      [notifSound, previewAudio].forEach(audio => {
        if (!audio) return;
        audio.volume = 0;
        audio.play()
          .then(() => {
            audio.pause();
            audio.currentTime = 0;
            audio.volume = 1;
          })
          .catch(() => {});
      });
      audioUnlocked = true;
      console.log("✅ Audio unlocked");
    }
    ["click", "keydown", "touchstart"].forEach(evt =>
      document.addEventListener(evt, unlockAudio, { once: true })
    );

    function playNotifSound() {
      if (!audioUnlocked) return;
      notifSound.currentTime = 0;
      notifSound.play().catch(err =>
        console.warn("🔇 Notification blocked:", err)
      );
    }

    function vibrate() {
      const vibrationEnabled = window.APP_CONFIG?.sound?.vibrationEnabled || false;
      if (vibrationEnabled && "vibrate" in navigator) {
        navigator.vibrate([200, 100, 200]); // buzz–pause–buzz
      }
    }

    // =========================
    // WEBSOCKET HANDLING
    // =========================
    function connectNotifSocket() {
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const socketUrl = `${protocol}://${window.location.host}/ws/notifications/`;
      notifSocket = new WebSocket(socketUrl);

      notifSocket.onopen = () =>
        console.log("🔌 Notifications socket connected");

      notifSocket.onmessage = event => {
        const data = JSON.parse(event.data);
        if (data.type === "notification") {
          renderNotification(data.content);
          playNotifSound();
          vibrate();
        }
      };

      notifSocket.onclose = () => {
        console.warn("❌ Notifications socket closed. Reconnecting in 5s...");
        setTimeout(connectNotifSocket, 5000);
      };
    }

    function renderNotification(n) {
      const notifList = document.getElementById("notif-list");
      let badge = document.getElementById("notif-badge");
      if (!notifList) return;

      if (!badge) {
        const bell = document.querySelector('.nav-link[data-bs-toggle="dropdown"]');
        if (bell) {
          badge = document.createElement("span");
          badge.id = "notif-badge";
          badge.className = "badge bg-red text-light";
          bell.appendChild(badge);
        }
      }

      const item = document.createElement("div");
      item.className = "list-group-item notif-item";
      item.dataset.id = n.id;
      item.innerHTML = `
        <div class="row align-items-center">
          <div class="col-auto">
            <span class="status-dot status-dot-animated ${
              n.is_urgent ? "bg-red" : n.is_success ? "bg-green" : "bg-gray"
            } d-block"></span>
          </div>
          <div class="col">
            <a href="${n.link || "#"}" class="text-body d-block notif-link text-truncate">${n.title}</a>
            <div class="d-block text-secondary mt-n1 notif-description" data-full="${n.description}">
              ${n.description.length > 150
                ? n.description.slice(0, 150) + ' <a href="#" class="show-more">...more</a>'
                : n.description}
            </div>
          </div>
        </div>
      `;
      notifList.prepend(item);

      if (badge) badge.textContent = notifList.children.length;
    }

    // =========================
    // MARK ALL READ
    // =========================
    document.querySelectorAll(".mark-all-read-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        try {
          const res = await fetch(urls.markAllRead, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken }
          });
          if (res.ok) {
            document.querySelectorAll("#notif-list").forEach(list => {
              list.innerHTML = '<div class="list-group-item">No new notifications</div>';
            });
            document.querySelectorAll("#notif-badge").forEach(badge => badge.remove());
          }
        } catch (err) {
          console.error("Failed to mark all read", err);
        }
      });
    });

    // =========================
    // SETTINGS FORM
    // =========================
    // PREVIEW BUTTON
    previewBtn?.addEventListener("click", () => {
      if (!soundSelect) return;

      const selected = soundSelect.value;
      const map = window.APP_CONFIG?.sound?.soundMap || {};
      const src = map[selected];

      if (src) {
        previewAudio.src = src;
        previewAudio.currentTime = 0;
        previewAudio.play().catch(err =>
          console.warn("🔇 Preview blocked:", err)
        );
      } else {
        console.warn("⚠️ No preview sound found for:", selected);
      }
    });

    // SAVE SETTINGS
    settingsForm?.addEventListener("submit", async e => {
      e.preventDefault();
      const formData = new FormData(settingsForm);

      try {
        const res = await fetch(window.APP_CONFIG.urls.updateSettings, {
          method: "POST",
          headers: { "X-CSRFToken": window.APP_CONFIG.csrfToken },
          body: formData
        });

        if (res.ok) {
          alert("✅ Notification settings updated");

          // Update sound + vibration immediately
          setNotificationSound(soundSelect.value);
          const formDataObj = Object.fromEntries(formData.entries());
          window.APP_CONFIG.sound.vibrationEnabled =
            formDataObj.vibration_enabled === "on";
        } else {
          alert("⚠️ Failed to update settings");
        }
      } catch (err) {
        console.error("❌ Error saving settings:", err);
        alert("⚠️ Error saving settings");
      }
    });

    // =========================
    // INIT
    // =========================
    connectNotifSocket();
  })();

});
