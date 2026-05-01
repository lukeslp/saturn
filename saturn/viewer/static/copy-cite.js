// Click-to-copy on citation blocks. Progressive enhancement — without JS,
// the user just selects + copies the text manually.
(() => {
  const blocks = document.querySelectorAll('[data-copy]');
  if (!blocks.length) return;

  const copy = async (text) => {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    // Fallback for non-secure contexts: select + execCommand
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch {}
    document.body.removeChild(ta);
    return ok;
  };

  blocks.forEach((el) => {
    el.tabIndex = 0;
    el.role = 'button';
    el.setAttribute('aria-label', `Copy ${el.previousElementSibling?.textContent || 'citation'} to clipboard`);

    const trigger = async () => {
      const ok = await copy(el.textContent.trim());
      if (ok) {
        el.classList.remove('copied');
        // Reflow so the animation restarts on repeat clicks
        void el.offsetWidth;
        el.classList.add('copied');
        setTimeout(() => el.classList.remove('copied'), 1500);
      }
    };

    el.addEventListener('click', trigger);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        trigger();
      }
    });
  });
})();
