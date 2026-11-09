// AI dialog: model + "prepare images" (embeddings), tag pools (the AI may only choose from these tags),
// training from your own tags, vision models (Ollama or an OpenAI-compatible server), and reviewing suggestions.

import { api } from './api.js';
import { openDatasetDialog } from './datasetdlg.js';
import { ask, openDialog } from './dialog.js';
import { el, fmtDate, fmtNum, setKids } from './util.js';

const METHOD = {
  zeroshot: 'Image–text model (no examples needed)',
  trained: 'Learned from your tags (most accurate for your style)',
  vlm: 'Vision-language model (Gemma, Qwen-VL... via Ollama or llama.cpp / LM Studio) - slower, can write captions',
};

export async function openAIDialog(app) {
  const body = el('div', { class: 'form aidlg' });
  let timer = 0;
  openDialog({ title: 'AI tagging & search', width: 860, body, buttons: [{ label: 'Close' }], onClose: () => clearInterval(timer) });
  const btn = (label, onclick, cls = '') => el('button', { class: 'btn ' + cls, type: 'button', text: label, onclick });
  const err = (e) => el('div', { class: 'err', text: e.message || String(e) });

  const datasetBox = () => el('div', { class: 'bset' },
    el('div', { class: 'bset-head' }, el('b', { text: 'Training dataset' }), el('span', { class: 'muted small', text: ' · pictures + caption .txt files for LoRA / fine-tuning' })),
    el('div', { class: 'dlg-inline-buttons' }, btn(`Export ${app.selected.size ? 'the selection' : 'this view'} as a dataset…`, () => openDatasetDialog(app))));

  const scopeIds = (scope) => (scope === 'selection' ? [...app.selected] : [...app.items.ids]);
  const scopeSel = () => {
    const n = app.selected.size;
    const s = el('select', {}, el('option', { value: 'selection', text: `selection (${fmtNum(n)})`, disabled: !n }),
      el('option', { value: 'view', text: `current view (${fmtNum(app.items.ids.length)})` }));
    s.value = n ? 'selection' : 'view';
    return s;
  };

  async function show() {
    clearInterval(timer);
    let info, profiles = [];
    try { info = await api.ai(); } catch (e) { setKids(body, err(e)); return; }
    if (info.job?.running) { progress(); return; }
    const st = info.status || {};
    if (!st.installed) {
      setKids(body,
        el('h3', { text: 'AI add-on not installed' }),
        el('div', { class: 'p-hint small', text: 'The AI features need PyTorch and open_clip (about 2–3 GB, with GPU support on NVIDIA cards). Everything runs on your computer.' }),
        el('pre', { class: 'code', text: 'Windows:      install.bat ai\nLinux/macOS:  ./install.sh ai' }),
        el('div', { class: 'muted small', text: `Then restart Kurator. (${st.error || ''})` }),
        datasetBox());
      return;
    }
    try { profiles = await api.aiProfiles(); } catch { /* none */ }
    const s = info.settings;
    const models = { ...info.models };
    if (s.model === 'custom') models.custom = `Custom: ${s.custom_model}${s.custom_pretrained ? ` / ${s.custom_pretrained}` : ''}`;
    const model = el('select', {}, Object.entries(models).map(([k, label]) => el('option', { value: k, text: label })));
    model.value = s.model in models ? s.model : 'fast';
    model.onchange = async () => { await api.aiSettings({ model: model.value }).catch((e) => app.toast(e.message)); show(); };
    const pct = info.images ? Math.round((100 * info.embedded) / info.images) : 0;
    const smodel = el('select', {}, [['off', 'Off'], ...Object.entries(info.sound_models || {})].map(([k, label]) => el('option', { value: k, text: label })));
    smodel.value = s.sound_model || 'clap';
    smodel.onchange = async () => { await api.aiSettings({ sound_model: smodel.value }).catch((e) => app.toast(e.message)); show(); };
    const todo = info.embedded < info.images || (info.sound_search && s.sound_model !== 'off' && info.sounds_embedded < info.sounds);
    const head = el('div', { class: 'ai-head' },
      el('div', {}, el('b', { text: st.device === 'cuda' ? `GPU: ${st.gpu || 'CUDA'}${st.vram_gb ? ` (${st.vram_gb} GB)` : ''}` : st.device === 'mps' ? 'Apple GPU (Metal)' : 'CPU (no CUDA GPU found - works, but slower)' }),
        el('span', { class: 'muted small', text: ` · PyTorch ${st.torch}` })),
      el('div', { class: 'row' }, el('label', { class: 'chk' }, 'Model', model),
        el('span', { class: 'muted small', text: `${fmtNum(info.embedded)} of ${fmtNum(info.images)} images prepared (${pct}%)` }),
        btn(todo ? 'Prepare images and sounds' : 'Up to date ✓', async () => { try { await api.aiEmbed(); progress(); } catch (e) { app.toast(e.message); } }, todo ? 'primary' : '')),
      el('div', { class: 'row' }, el('label', { class: 'chk' }, 'Sounds', smodel),
        info.sound_search
          ? el('span', { class: 'muted small', text: `${fmtNum(info.sounds_embedded)} of ${fmtNum(info.sounds)} sounds prepared - search sounds by description (?rain on a tin roof), S finds sounds that sound alike` })
          : el('span', { class: 'muted small', text: 'Text-to-sound search needs transformers: run install.bat ai (or ./install.sh ai) again' })),
      el('div', { class: 'p-hint small', text: 'Preparing reads each image once (from the thumbnail cache) and stores a compact "fingerprint". It powers search by description (type ?dragon with wings in the search box), Find similar (S) and the tag pools below. The first use downloads the model.' }));

    const cards = profiles.map((p) => {
      const scope = scopeSel();
      const tagNames = p.tag_ids.map((t) => app.tags.byId.get(t)?.name || '?').join(', ');
      const tr = p.trained;
      return el('div', { class: 'bset' },
        el('div', { class: 'bset-head' }, el('b', { text: p.name }), el('span', { class: 'muted', text: ` · ${p.mode === 'single' ? 'one tag per image' : 'several tags allowed'} · ${METHOD[p.method].split(' (')[0]}` })),
        el('div', { class: 'small', text: tagNames }),
        el('div', { class: 'muted small', text: [
          p.method === 'trained' ? (tr ? `trained on ${fmtNum(tr.examples)} images · ${tr.metric} ${Math.round(tr.score * 100)}%${tr.weak?.length ? ` · few examples: ${tr.weak.join(', ')}` : ''}` : 'not trained yet') : null,
          p.method === 'vlm' ? `model ${p.vlm_model || s.vlm_model || '?'}${p.captions ? ' · writes captions' : ''}` : null,
          `threshold ${Math.round(p.threshold * 100)}%`,
          p.last_run ? `last run ${fmtDate(p.last_run)}: ${fmtNum(p.last_result?.suggested ?? 0)} suggestions` : 'never run',
        ].filter(Boolean).join(' · ') }),
        el('div', { class: 'dlg-inline-buttons' },
          p.method === 'trained' ? btn('Train', async () => {
            try { const r = await api.aiTrain(p.id); app.toast(`Trained: ${r.metric} ${Math.round(r.score * 100)}% on ${r.examples} images`, 3500); } catch (e) { app.toast(e.message, 4000); }
            show();
          }) : null,
          el('span', { class: 'small muted', text: 'Run on' }), scope,
          btn('Suggest tags', async () => {
            const ids = scopeIds(scope.value);
            if (!ids.length) { app.toast('Nothing to run on'); return; }
            try { await api.aiRun(p.id, ids); progress(); } catch (e) { app.toast(e.message, 4000); }
          }, 'primary'),
          btn('Edit', () => editor(p)),
          btn('Delete', async () => { if (await ask(`Delete tag pool "${p.name}"?\n\nTags and suggestions stay.`, { ok: 'Delete', danger: true })) { await api.aiDeleteProfile(p.id); show(); } }, 'danger')));
    });

    // the vision model server used by "vision-language model" pools
    const prov = el('select', {}, Object.entries(info.vlm_providers || { ollama: 'Ollama' }).map(([k, label]) => el('option', { value: k, text: label })));
    prov.value = s.vlm_provider || 'ollama';
    const url = el('input', { type: 'text', spellcheck: 'false', style: { width: '250px' } });
    const key = el('input', { type: 'password', autocomplete: 'off', style: { width: '170px' } });
    const keyLbl = el('label', { class: 'chk' }, 'API key', key);
    const vstat = el('span', { class: 'muted small' });
    const paintVlm = () => {
      const oa = prov.value === 'openai';
      url.value = oa ? s.openai_url : s.ollama_url;
      url.placeholder = oa ? 'http://127.0.0.1:8080/v1' : 'http://127.0.0.1:11434';
      key.placeholder = s.openai_key_set ? 'saved - type to replace' : 'optional';
      keyLbl.style.display = oa ? '' : 'none';
    };
    const checkVlm = async () => {
      vstat.textContent = 'checking…';
      try {
        const r = await api.aiOllama();
        vstat.textContent = r.ok ? `${r.models.length ? `${fmtNum(r.models.length)} models` : 'no models yet'} ✓` : r.error;
        vstat.className = r.ok ? 'ok small' : 'err small';
      } catch (e) { vstat.textContent = e.message; vstat.className = 'err small'; }
    };
    const saveVlm = async () => {
      const patch = { vlm_provider: prov.value };
      patch[prov.value === 'openai' ? 'openai_url' : 'ollama_url'] = url.value.trim();
      if (key.value) patch.openai_key = key.value;
      try {
        const ns = await api.aiSettings(patch);
        Object.assign(s, ns);
        key.value = '';
        paintVlm();
        checkVlm();
      } catch (e) { app.toast(e.message, 4000); }
    };
    prov.onchange = () => { s.vlm_provider = prov.value; paintVlm(); saveVlm(); };
    url.onchange = saveVlm;
    key.onchange = saveVlm;
    paintVlm();
    const vlmBox = el('div', { class: 'bset' },
      el('div', { class: 'bset-head' }, el('b', { text: 'Vision model server' }), el('span', { class: 'muted small', text: ' · for tag pools that use a vision-language model, and for captions' })),
      el('div', { class: 'row' }, el('label', { class: 'chk' }, 'Server', prov), el('label', { class: 'chk' }, 'Address', url), keyLbl,
        btn('Check', checkVlm), vstat),
      el('div', { class: 'p-hint small', text: 'Ollama: ollama.com, then e.g. "ollama pull gemma3:12b". OpenAI-compatible: llama.cpp (llama-server -m model.gguf --mmproj mmproj.gguf), LM Studio (Developer → Start server, port 1234), vLLM, LocalAI, Jan. These run on your computer; an internet address sends the pictures to that service.' }));
    if (profiles.some((p) => p.method === 'vlm')) checkVlm();

    const minScore = el('input', { type: 'number', min: '0', max: '100', step: '5', value: '90', style: { width: '64px' } });
    const review = el('div', { class: 'bset' },
      el('div', { class: 'bset-head' }, el('b', { text: `${fmtNum(info.suggestions)} suggestions waiting` })),
      el('div', { class: 'p-hint small', text: 'Suggestions are not tags until you accept them. In the grid they show as hollow dots; select images and press A to accept or Shift+A to reject (rejections teach a trained pool what a tag is not).' }),
      el('div', { class: 'dlg-inline-buttons' },
        btn('Review them', () => { app.setSuggestedFilter(true); document.querySelector('.dlg-wrap')?.remove(); document.body.classList.remove('modal-open'); }, 'primary'),
        el('span', { class: 'small muted', text: 'Accept all with score ≥' }), minScore, el('span', { class: 'small muted', text: '%' }),
        btn('Accept', async () => { const r = await api.aiAccept({ min_score: +minScore.value / 100 }); app.onAIAccepted(r); show(); }),
        btn('Reject all', async () => { if (await ask('Reject all waiting suggestions?', { ok: 'Reject all', danger: true })) { await api.aiReject({}); app.reload(); show(); } }, 'danger')));

    setKids(body, head, el('h3', { text: 'Tag pools' }),
      el('div', { class: 'p-hint small', text: 'A pool is a set of tags the AI may choose from - e.g. human / robot / creature for a characters folder. It never invents other tags.' }),
      cards.length ? cards : null,
      el('div', { class: 'dlg-inline-buttons' }, btn('+ New tag pool', () => editor(null), 'primary')),
      info.suggestions ? review : null,
      vlmBox, datasetBox());
  }

  async function editor(p) {
    const isNew = !p;
    const v = p ? { ...p } : { name: 'New pool', tag_ids: [], mode: 'single', method: 'zeroshot', threshold: 0.5, vlm_model: '', captions: 0 };
    const chosen = new Set(v.tag_ids);
    const name = el('input', { type: 'text', value: v.name });
    const prompts = el('div', { class: 'prompts' });
    const renderPrompts = () => setKids(prompts, [...chosen].map((tid) => {
      const t = app.tags.byId.get(tid);
      const inp = el('input', { type: 'text', value: t?.ai_prompt || '', placeholder: `optional, e.g. a pencil sketch of a ${(t?.name || '').split('/').pop().toLowerCase()}; a ${(t?.name || '').split('/').pop().toLowerCase()} concept art  (; separates variants)`, spellcheck: 'false' });
      inp.onchange = () => api.tagPrompt(tid, inp.value).then(() => { if (t) t.ai_prompt = inp.value; }).catch((e) => app.toast(e.message));
      return el('div', { class: 'trow' }, el('span', { class: 'dot', style: { background: t?.color || '#888' } }), el('span', { class: 'pname', text: t?.name || '?' }), inp);
    }));
    const chips = el('div', {}, app.tags.facets.map((f) => {
      const ts = app.tags.list.filter((t) => t.facet === f.key && !t.archived);
      return ts.length ? el('div', { class: 'chip-list' }, el('span', { class: 'tb-group', text: f.label }), ts.map((t) => {
        const c = el('button', { class: 'chip' + (chosen.has(t.id) ? ' full' : ''), type: 'button', style: { '--c': t.color }, text: t.name });
        c.onclick = () => { if (chosen.has(t.id)) chosen.delete(t.id); else chosen.add(t.id); c.classList.toggle('full', chosen.has(t.id)); renderPrompts(); };
        return c;
      })) : null;
    }));
    const mode = el('select', {}, el('option', { value: 'single', text: 'one tag per image (e.g. human OR robot)' }), el('option', { value: 'multi', text: 'several tags allowed' }));
    mode.value = v.mode;
    const method = el('select', {}, Object.entries(METHOD).map(([k, t]) => el('option', { value: k, text: t })));
    method.value = v.method;
    const thr = el('input', { type: 'number', min: '0', max: '100', step: '5', value: String(Math.round(v.threshold * 100)), style: { width: '64px' } });
    const vlm = el('select', {}, el('option', { value: '', text: 'loading…' }));
    const caps = el('input', { type: 'checkbox' });
    caps.checked = !!v.captions;
    const vlmRow = el('div', { class: 'row' }, el('label', { class: 'chk' }, 'Vision model', vlm), el('label', { class: 'chk' }, caps, 'also write a caption (for training datasets)'));
    const sync = () => { vlmRow.style.display = method.value === 'vlm' ? '' : 'none'; };
    method.onchange = () => { sync(); if (method.value === 'zeroshot' && mode.value === 'multi') thr.value = '15'; };
    sync();
    api.aiOllama().then((r) => {
      const ol = r.provider !== 'openai';
      const none = r.ok ? (ol ? 'no models - run: ollama pull gemma3:12b' : 'no models loaded on the server')
        : (ol ? 'Ollama not running (ollama.com)' : 'server not answering - see "Vision model server"');
      setKids(vlm, r.models.length ? r.models.map((m) => el('option', { value: m, text: m })) : el('option', { value: '', text: none }));
      if (v.vlm_model && r.models.includes(v.vlm_model)) vlm.value = v.vlm_model;
    });
    const msg = el('div');
    renderPrompts();
    setKids(body,
      el('h3', { text: isNew ? 'New tag pool' : `Edit "${v.name}"` }),
      el('label', {}, 'Name', name),
      el('div', { class: 'small', text: 'Tags the AI may choose from (pick 2 or more):' }), chips,
      el('div', { class: 'row' }, el('label', { class: 'chk' }, 'Mode', mode), el('label', { class: 'chk' }, 'Suggest when score ≥', thr, '%')),
      el('label', { class: 'chk' }, 'Method', method),
      vlmRow,
      el('div', { class: 'small', text: 'Descriptions help the image–text model and the vision models; sketches often need hints like "a pencil sketch of a person".' }),
      prompts, msg,
      el('div', { class: 'dlg-inline-buttons' }, btn('Back', show), btn(isNew ? 'Create pool' : 'Save', async () => {
        try {
          await api.aiSaveProfile({ name: name.value, tag_ids: [...chosen], mode: mode.value, method: method.value,
            threshold: (+thr.value || 0) / 100, vlm_model: vlm.value, captions: caps.checked }, p?.id);
          show();
        } catch (e) { setKids(msg, err(e)); }
      }, 'primary')));
  }

  function progress() {
    const bar = el('div', { class: 'pbar' }, el('span'));
    const line = el('div');
    const out = el('div');
    setKids(body, el('h3', { text: 'Working…' }), bar, line, out,
      el('div', { class: 'dlg-inline-buttons' }, btn('Cancel', () => api.aiCancel())));
    const tick = async () => {
      let st;
      try { st = await api.aiJob(); } catch { return; }
      bar.firstChild.style.width = st.total ? `${(100 * st.done) / st.total}%` : '5%';
      line.textContent = `${st.kind === 'embed' ? 'Preparing images' : st.kind === 'vlm' ? 'Asking the vision model' : 'Scoring tags'}: ${fmtNum(st.done)} / ${fmtNum(st.total)}`;
      if (!st.running) {
        clearInterval(timer);
        const r = st.result || {};
        setKids(out, st.errors?.length ? el('div', { class: 'err', text: st.errors.slice(0, 5).join(' · ') }) : null,
          el('div', { class: 'ok', text: st.kind === 'embed' ? `Prepared ${fmtNum(r.embedded ?? 0)} images` : `${fmtNum(r.suggested ?? 0)} suggestions for ${fmtNum(r.files ?? 0)} images${r.captions ? ` · ${fmtNum(r.captions)} captions` : ''}` }));
        body.querySelector('h3').textContent = 'Finished';
        setKids(body.querySelector('.dlg-inline-buttons'), btn('Back', show, 'primary'));
        app.reload();
      }
    };
    clearInterval(timer);
    timer = setInterval(tick, 700);
    tick();
  }

  show();
}
