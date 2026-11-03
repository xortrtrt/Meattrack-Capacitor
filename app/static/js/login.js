document.querySelectorAll("[data-login-form]").forEach((form) => {
  if (!window.PortalForms?.validate) {
    form.noValidate = false;
    return;
  }

  form.addEventListener("submit", (event) => {
    if (!window.PortalForms.validate(form)) event.preventDefault();
  });
});

document.querySelectorAll("[data-password-toggle]").forEach((toggle) => {
  const input = document.getElementById("login-password");
  if (!input) return;

  toggle.classList.add("is-available");
  toggle.addEventListener("click", () => {
    const revealing = input.type === "password";
    input.type = revealing ? "text" : "password";
    toggle.textContent = revealing ? "Hide" : "Show";
    toggle.setAttribute("aria-label", `${revealing ? "Hide" : "Show"} password`);
    toggle.setAttribute("aria-pressed", String(revealing));
  });
});
