(function () {
  'use strict';

  function csrfToken() {
    const match = document.cookie.match(/(?:^|; )receipt_csrf=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  const SESSION_MESSAGE = 'Sesi Anda telah berakhir. Silakan masuk kembali.';
  const OCR_MESSAGE = 'Sistem pembaca otomatis sedang tidak aktif. Silakan hubungi tim IT.';

  function friendlyMessage(status, detail) {
    const text = detail || '';
    if (status === 401 || status === 408) return SESSION_MESSAGE;
    if (status === 403 && /csrf|sesi|token|unauthorized/i.test(text)) return SESSION_MESSAGE;
    if (status === 403) return 'Anda tidak memiliki izin untuk melakukan tindakan ini.';
    if (status === 500 || status === 502 || status === 504) return OCR_MESSAGE;
    if (/csrf|unauthorized|session/i.test(text)) return SESSION_MESSAGE;
    return text || 'Permintaan gagal. Silakan coba lagi.';
  }

  async function api(url, options) {
    const opts = Object.assign({}, options || {});
    opts.headers = Object.assign({ 'X-CSRF-Token': csrfToken() }, opts.headers || {});
    const response = await fetch(url, opts);
    let body = null;
    try { body = await response.json(); } catch (err) { body = null; }
    if (!response.ok) {
      const detail = (body && (body.detail || body.message)) || '';
      const error = new Error(friendlyMessage(response.status, detail));
      error.body = body;
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function show(element, message) {
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
  }

  function hide(element) {
    if (!element) return;
    element.hidden = true;
  }

  function setPill(element, isGood, text) {
    element.textContent = text;
    element.classList.toggle('is-good', isGood);
    element.classList.toggle('is-bad', !isGood);
  }

  let statusTimer = null;

  function stopStatusPolling() {
    if (statusTimer !== null) {
      window.clearInterval(statusTimer);
      statusTimer = null;
    }
  }

  function canPollStatus() {
    return !!(document.body && document.body.dataset.auth === 'true'
      && document.getElementById('serverStatus')
      && document.getElementById('ocrStatus'));
  }

  function startStatusPolling() {
    if (!canPollStatus()) return;
    stopStatusPolling();
    refreshStatus();
    statusTimer = window.setInterval(refreshStatus, 60000);
  }

  const logoutForm = document.getElementById('logoutForm');
  if (logoutForm) logoutForm.addEventListener('submit', stopStatusPolling);

  function startClock() {
    const timeElement = document.getElementById('clockTime');
    if (!timeElement) return;
    const dateElement = document.getElementById('clockDate');

    function tick() {
      const now = new Date();
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Jakarta', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true
      }).formatToParts(now);
      const part = function (type) {
        const found = parts.find(function (item) { return item.type === type; });
        return found ? found.value : '';
      };
      timeElement.textContent = part('hour') + ':' + part('minute') + ':' + part('second') + ' ' + part('dayPeriod');
      if (dateElement) {
        dateElement.textContent = new Intl.DateTimeFormat('id-ID', {
          timeZone: 'Asia/Jakarta', weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'
        }).format(now);
      }
    }

    tick();
    window.setInterval(tick, 1000);
  }

  async function refreshStatus() {
    if (!canPollStatus()) {
      stopStatusPolling();
      return;
    }
    const serverElement = document.getElementById('serverStatus');
    const ocrElement = document.getElementById('ocrStatus');
    try {
      const response = await fetch('/api/status', { headers: { 'Accept': 'application/json' } });
      if (response.redirected && new URL(response.url, window.location.origin).pathname === '/login') {
        stopStatusPolling();
        return;
      }
      if (response.status === 401 || response.status === 303) {
        stopStatusPolling();
        return;
      }
      if (!response.ok) throw new Error('Status tidak tersedia');
      const data = await response.json();
      setPill(serverElement, true, 'Server: Terhubung');
      setPill(ocrElement, !!data.ocr_ready, data.ocr_ready ? 'Layanan OCR: Aktif' : 'Layanan OCR: Tidak Aktif');
    } catch (err) {
      setPill(serverElement, false, 'Server: Terputus');
      setPill(ocrElement, false, 'Layanan OCR: Tidak Aktif');
    }
  }

  const video = document.getElementById('preview');
  if (video) {
    const canvas = document.getElementById('frame');
    const still = document.getElementById('still');
    const placeholder = document.getElementById('placeholder');
    const startBtn = document.getElementById('startBtn');
    const captureBtn = document.getElementById('captureBtn');
    const retakeBtn = document.getElementById('retakeBtn');
    const processBtn = document.getElementById('processBtn');
    const fileInput = document.getElementById('fileInput');
    const statusBox = document.getElementById('status');
    const errorBox = document.getElementById('error');
    let stream = null;
    let selected = null;
    let objectUrl = null;

    function clearSelected() {
      selected = null;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
      still.removeAttribute('src');
      still.hidden = true;
      if (fileInput) fileInput.value = '';
      hide(statusBox);
      hide(errorBox);
    }

    function acceptFile(file) {
      if (!file) return;
      if (!/^image\//.test(file.type)) {
        show(errorBox, 'Pilih file foto dengan format JPG, PNG, atau WEBP.');
        return;
      }
      setSelected(file);
    }

    function setSelected(blob) {
      clearSelected();
      selected = blob;
      objectUrl = URL.createObjectURL(blob);
      still.src = objectUrl;
      still.hidden = false;
      video.hidden = true;
      hide(placeholder);
      hide(startBtn);
      hide(captureBtn);
      retakeBtn.hidden = false;
      processBtn.hidden = false;
    }

    startBtn.addEventListener('click', async function () {
      hide(errorBox);
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        show(errorBox, 'Perangkat ini belum bisa membuka kamera. Silakan pilih foto yang sudah tersedia.');
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 960 } },
          audio: false
        });
        video.srcObject = stream;
        await video.play();
        video.hidden = false;
        hide(placeholder);
        hide(startBtn);
        captureBtn.hidden = false;
      } catch (err) {
        show(errorBox, 'Kamera belum bisa dipakai. Periksa izin kamera atau pilih foto yang sudah tersedia.');
      }
    });

    captureBtn.addEventListener('click', function () {
      if (!video.videoWidth) {
        show(errorBox, 'Kamera belum siap. Coba lagi sebentar.');
        return;
      }
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video, 0, 0);
      canvas.toBlob(function (blob) {
        if (blob) setSelected(blob);
      }, 'image/jpeg', 0.9);
    });

    retakeBtn.addEventListener('click', function () {
      clearSelected();
      processBtn.hidden = true;
      retakeBtn.hidden = true;
      if (stream) {
        video.hidden = false;
        captureBtn.hidden = false;
        placeholder.hidden = true;
        startBtn.hidden = true;
      } else {
        placeholder.hidden = false;
        startBtn.hidden = false;
      }
    });

    fileInput.addEventListener('change', function () {
      if (fileInput.files && fileInput.files[0]) acceptFile(fileInput.files[0]);
    });

    const dropZone = document.getElementById('dropZone');
    if (dropZone) {
      ['dragenter', 'dragover'].forEach(function (type) {
        dropZone.addEventListener(type, function (event) {
          event.preventDefault();
          dropZone.classList.add('is-dragging');
        });
      });
      ['dragleave', 'drop'].forEach(function (type) {
        dropZone.addEventListener(type, function (event) {
          event.preventDefault();
          dropZone.classList.remove('is-dragging');
        });
      });
      dropZone.addEventListener('drop', function (event) {
        const files = event.dataTransfer && event.dataTransfer.files;
        if (files && files[0]) acceptFile(files[0]);
      });
    }

    processBtn.addEventListener('click', async function () {
      if (!selected) return;
      processBtn.disabled = true;
      hide(errorBox);
      show(statusBox, 'Sedang membaca dokumen. Mohon tunggu...');
      const body = new FormData();
      body.append('image', selected, 'receipt.jpg');
      try {
        const data = await api('/api/scans', { method: 'POST', body: body });
        clearSelected();
        location.href = '/scans/' + data.scan_id + '/review';
      } catch (err) {
        clearSelected();
        if (err.status === 409) {
          show(errorBox, 'Dokumen ini sudah pernah dibaca. Buka Riwayat untuk melihat hasil sebelumnya.');
          return;
        }
        show(errorBox, err.message);
      } finally {
        processBtn.disabled = false;
      }
    });
  }

  const reviewForm = document.getElementById('reviewForm');
  if (reviewForm) {
    const saveBtn = document.getElementById('saveBtn');
    const deleteBtn = document.getElementById('deleteBtn');
    const errorBox = document.getElementById('error');

    function toAmount(value, fallback) {
      if (value === '' || value === null || typeof value === 'undefined') return fallback;
      let text = String(value).trim().replace(/[\s\u00a0]/g, '');
      const idThousands = /^-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$/.test(text);
      const usThousands = /^-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$/.test(text);
      const decimal = /^-?\d+[.,]\d{1,2}$/.test(text);
      const integer = /^-?\d+$/.test(text);
      if (idThousands) text = text.replace(/\./g, '').replace(',', '.');
      else if (usThousands) text = text.replace(/,/g, '');
      else if (decimal) text = text.replace(',', '.');
      else if (!integer) return fallback;
      const parsed = Number(text);
      return Number.isInteger(parsed) ? parsed : fallback;
    }

    const VERIFICATIONS = [
      { field: 'transaction_date', label: 'Tanggal Transaksi', name: 'verified_transaction_date' },
      { field: 'merchant', label: 'Nama Toko', name: 'verified_merchant' },
      { field: 'total', label: 'Total (Rp)', name: 'verified_total' }
    ];

    function verificationCheckbox(name) {
      return reviewForm.querySelector('input[name="' + name + '"]');
    }

    function hasValue(field) {
      const element = reviewForm.elements.namedItem(field);
      if (!element) return false;
      if (field === 'total') {
        const amount = toAmount(element.value, null);
        return amount !== null && amount > 0;
      }
      return String(element.value || '').trim() !== '';
    }

    function syncVerification(item) {
      const checkbox = verificationCheckbox(item.name);
      if (checkbox) checkbox.checked = hasValue(item.field);
    }

    VERIFICATIONS.forEach(function (item) {
      const element = reviewForm.elements.namedItem(item.field);
      if (element) {
        element.addEventListener('input', function () { syncVerification(item); });
        element.addEventListener('change', function () { syncVerification(item); });
      }
      syncVerification(item);
    });

    const verifyAllBtn = document.getElementById('verifyAllBtn');
    if (verifyAllBtn) {
      verifyAllBtn.addEventListener('click', function () {
        VERIFICATIONS.forEach(function (item) {
          const checkbox = verificationCheckbox(item.name);
          if (checkbox) checkbox.checked = true;
        });
      });
    }

    function payloadFromForm() {
      const payload = {};
      Array.prototype.forEach.call(reviewForm.elements, function (element) {
        if (!element.name || element.type === 'hidden' || element.type === 'checkbox') return;
        if (element.type === 'number') {
          payload[element.name] = toAmount(element.value, null);
        } else {
          payload[element.name] = element.value;
        }
      });
      payload.total = toAmount(payload.total, null);
      payload.verified_fields = [];
      Array.prototype.forEach.call(
        reviewForm.querySelectorAll('input[type="checkbox"]:checked'),
        function (element) {
          const field = element.name.replace(/^verified_/, '');
          const known = VERIFICATIONS.some(function (item) { return item.field === field; });
          if (known) payload.verified_fields.push(field);
        }
      );
      return payload;
    }

    function missingVerifications(payload) {
      return VERIFICATIONS.filter(function (item) {
        const checkbox = verificationCheckbox(item.name);
        return !checkbox || !checkbox.checked || !hasValue(item.field) || payload[item.field] === null;
      });
    }

    saveBtn.addEventListener('click', async function () {
      hide(errorBox);
      const payload = payloadFromForm();
      const missing = missingVerifications(payload);
      if (missing.length) {
        const labels = missing.map(function (item) { return item.label; }).join(', ');
        show(errorBox, labels + ' belum terisi' + '.');
        const firstCheckbox = verificationCheckbox(missing[0].name);
        if (firstCheckbox) firstCheckbox.focus();
        return;
      }
      saveBtn.disabled = true;
      try {
        await api('/api/scans/' + saveBtn.dataset.scanId + '/confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        location.href = '/history';
      } catch (err) {
        show(errorBox, err.message);
        saveBtn.disabled = false;
      }
    });

    deleteBtn.addEventListener('click', async function () {
      if (!window.confirm('Hapus hasil bacaan ini?')) return;
      deleteBtn.disabled = true;
      try {
        await api('/api/scans/' + deleteBtn.dataset.scanId + '/delete', { method: 'POST' });
        location.href = '/history';
      } catch (err) {
        show(errorBox, err.message);
        deleteBtn.disabled = false;
      }
    });
  }

  const exportBtn = document.getElementById('exportBtn');
  if (exportBtn) {
    const errorBox = document.getElementById('error');
    const statusBox = document.getElementById('status');
    exportBtn.addEventListener('click', async function () {
      if (exportBtn.dataset.ready !== 'true') {
        show(errorBox, exportBtn.dataset.emptyMessage || 'Belum ada data yang siap diexport.');
        return;
      }
      exportBtn.disabled = true;
      hide(errorBox);
      show(statusBox, 'Sedang menyiapkan laporan Excel...');

      try {
        const data = await api('/api/exports', { method: 'POST' });
        location.href = data.download_url;
      } catch (err) {
        show(errorBox, err.message);
        hide(statusBox);
        exportBtn.disabled = false;
      }
    });
  }

  const userForm = document.getElementById('userForm');
  if (userForm) {
    const errorBox = document.getElementById('userError');
    const noticeBox = document.getElementById('temporaryPassword');
    userForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      hide(errorBox);
      hide(noticeBox);
      const body = new FormData(userForm);
      try {
        const data = await api('/admin/users', { method: 'POST', body: body });
        show(noticeBox, data.message + ' Password: ' + data.temporary_password);
        userForm.reset();
      } catch (err) {
        show(errorBox, err.message);
      }
    });

    Array.prototype.forEach.call(document.querySelectorAll('.reset-password'), function (button) {
      button.addEventListener('click', async function () {
        if (!window.confirm('Reset kata sandi ' + button.dataset.username + '? Sesi aktif akan dicabut.')) return;
        button.disabled = true;
        try {
          const data = await api('/admin/users/' + button.dataset.userId + '/reset-password', { method: 'POST' });
          show(noticeBox, data.message + ' Password: ' + data.temporary_password);
        } catch (err) {
          show(errorBox, err.message);
        } finally {
          button.disabled = false;
        }
      });
    });
  }

  const retrainBtn = document.getElementById('retrainBtn');
  if (retrainBtn) {
    const statusBox = document.getElementById('retrainStatus');
    const errorBox = document.getElementById('retrainError');
    retrainBtn.addEventListener('click', async function () {
      retrainBtn.disabled = true;
      hide(errorBox);
      show(statusBox, 'Sedang melatih ulang model. Mohon tunggu...');
      try {
        const data = await api('/retrain', { method: 'POST' });
        hide(statusBox);
        const map = data.metrics && typeof data.metrics.map50 === 'number'
          ? ' mAP50: ' + data.metrics.map50.toFixed(3) + '.'
          : '.';
        if (data.status === 'promoted') {
          show(statusBox, 'Model baru berhasil dipromosikan ke produksi.' + map);
        } else if (data.status === 'rejected') {
          show(statusBox, 'Model baru tidak dipromosikan karena akurasinya lebih rendah.' + map);
        } else {
          show(statusBox, data.reason || 'Proses latih ulang selesai.');
        }
      } catch (err) {
        hide(statusBox);
        show(errorBox, err.message);
      } finally {
        retrainBtn.disabled = false;
      }
    });
  }

  const rollbackBtn = document.getElementById('rollbackBtn');
  if (rollbackBtn) {
    const statusBox = document.getElementById('retrainStatus');
    const errorBox = document.getElementById('retrainError');
    rollbackBtn.addEventListener('click', async function () {
      rollbackBtn.disabled = true;
      hide(errorBox);
      try {
        const data = await api('/rollback', { method: 'POST' });
        show(statusBox, data.reason || 'Model berhasil dikembalikan ke versi sebelumnya.');
      } catch (err) {
        show(errorBox, err.message);
      } finally {
        rollbackBtn.disabled = false;
      }
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('.verify-feedback'), function (button) {
    button.addEventListener('click', async function () {
      button.disabled = true;
      try {
        await api('/admin/feedback/' + encodeURIComponent(button.dataset.feedbackId) + '/verify', { method: 'POST' });
        show(document.getElementById('feedbackStatus'), 'Feedback ' + button.dataset.feedbackId + ' terverifikasi.');
        button.remove();
      } catch (err) {
        show(document.getElementById('feedbackStatus'), err.message);
        button.disabled = false;
      }
    });
  });

  startClock();
  startStatusPolling();
  window.addEventListener('pagehide', stopStatusPolling);
})();
