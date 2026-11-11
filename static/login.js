'use strict';
document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = document.getElementById('msg');
  const btn = document.getElementById('go');
  msg.textContent = '';
  btn.disabled = true;
  try {
    const r = await fetch('/api/login', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'augusta' },
      body: JSON.stringify({ username: document.getElementById('u').value, password: document.getElementById('p').value }),
    });
    if (r.ok) { location.href = '/'; return; }
    let detail = 'Sign-in failed.';
    try { const j = await r.json(); if (typeof j.detail === 'string') detail = j.detail; } catch (err) { /* keep default */ }
    msg.textContent = detail;
  } catch (err) {
    msg.textContent = 'Cannot reach the server.';
  }
  btn.disabled = false;
});
