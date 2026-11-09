// Minimal modal dialog. While open, the app's global shortcuts are paused (body.modal-open).

import { el } from './util.js';

export function openDialog({ title, body, buttons = [], width = 520, onClose }) {
  const btnEls = {};
  const footer = el('div', { class: 'dlg-buttons' });
  const box = el('div', { class: 'dlg', style: { width: `${width}px` }, role: 'dialog', 'aria-label': title },
    el('div', { class: 'dlg-title', text: title }), el('div', { class: 'dlg-body' }, body), footer);
  const wrap = el('div', { class: 'dlg-wrap' }, box);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    wrap.remove();
    document.removeEventListener('keydown', onKey, true);
    if (!document.querySelector('.dlg-wrap')) document.body.classList.remove('modal-open');
    onClose?.();
  };
  const onKey = (e) => {
    if (e.key !== 'Escape') return;
    const all = document.querySelectorAll('.dlg-wrap');
    if (all[all.length - 1] !== wrap) return;      // a dialog opened from this one (folder picker) closes first
    e.preventDefault(); e.stopImmediatePropagation(); close();
  };
  for (const b of buttons) {
    const n = el('button', { class: 'btn' + (b.primary ? ' primary' : '') + (b.danger ? ' danger' : ''), type: 'button', text: b.label,
      onclick: () => b.onClick ? b.onClick(close) : close() });
    if (b.disabled) n.disabled = true;
    if (b.left) n.classList.add('left');
    btnEls[b.id || b.label] = n;
    footer.append(n);
  }
  document.addEventListener('keydown', onKey, true);
  wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
  document.body.append(wrap);
  document.body.classList.add('modal-open');
  setTimeout(() => box.querySelector('input,select,textarea,button.primary')?.focus(), 0);
  return { close, box, buttons: btnEls };
}

/** A yes/no question in the page (window.confirm does nothing in the macOS desktop app). Resolves true
 * for yes. The message may hold line breaks; the first paragraph is shown in bold. */
export function ask(message, { ok = 'OK', danger = false, title = 'Kurator' } = {}) {
  return new Promise((resolve) => {
    let answer = false;
    const [head, ...rest] = String(message).split('\n\n');
    openDialog({
      title, width: 460,
      body: el('div', { class: 'ask' }, el('div', { class: 'ask-head', text: head }), rest.length ? el('div', { class: 'p-hint small ask-more', text: rest.join('\n\n') }) : null),
      buttons: [{ label: 'Cancel' }, { label: ok, primary: !danger, danger, onClick: (close) => { answer = true; close(); } }],
      onClose: () => resolve(answer),
    });
  });
}
