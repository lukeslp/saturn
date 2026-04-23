// Progressive-enhancement drag-and-drop for the dropzone. Works without JS;
// the label is already clickable and the file input is keyboard-accessible.
(() => {
  const zone = document.getElementById('dropzone');
  const input = document.getElementById('file-input');
  const callout = document.getElementById('dropzone-callout');
  const filename = document.getElementById('dropzone-filename');
  if (!zone || !input) return;

  const defaultCallout = callout.textContent;

  const setFile = (file) => {
    if (!file) {
      filename.textContent = '';
      callout.textContent = defaultCallout;
      return;
    }
    filename.textContent = file.name;
    callout.textContent = `${(file.size / 1024).toFixed(0)} kB`;
  };

  input.addEventListener('change', (e) => setFile(e.target.files[0]));

  ['dragenter', 'dragover'].forEach((evt) => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.add('is-dragging');
    });
  });
  ['dragleave', 'drop'].forEach((evt) => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.remove('is-dragging');
    });
  });
  zone.addEventListener('drop', (e) => {
    if (!e.dataTransfer.files || !e.dataTransfer.files.length) return;
    input.files = e.dataTransfer.files;
    setFile(e.dataTransfer.files[0]);
  });

  zone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      input.click();
    }
  });
})();
