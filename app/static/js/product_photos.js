// Delegation keeps previews working when portal navigation replaces the form.
document.addEventListener('change', (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || !input.matches('[data-product-photo]')) return;
  const old = input.parentElement.querySelector('[data-photo-preview]');
  if (old) { URL.revokeObjectURL(old.src); old.remove(); }
  input.setCustomValidity('');
  const file = input.files?.[0];
  if (!file) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 2 * 1024 * 1024) {
    input.setCustomValidity('Choose a JPG, PNG, or WebP photo up to 2 MB.');
    input.reportValidity();
    return;
  }
  const preview = document.createElement('img');
  preview.dataset.photoPreview = '';
  preview.className = 'inventory-photo-preview';
  preview.alt = 'Selected product photo';
  preview.src = URL.createObjectURL(file);
  preview.addEventListener('load', () => URL.revokeObjectURL(preview.src), { once: true });
  input.parentElement.append(preview);
});
