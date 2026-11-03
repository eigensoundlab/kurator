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
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
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
