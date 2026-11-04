document.querySelectorAll("[data-login-form]").forEach((form) => {
  if (!window.PortalForms?.validate) {
    form.noValidate = false;
    return;
  }

  // Validate before the shared submit handler applies its loading state.
  form.addEventListener("submit", (event) => {
    if (!window.PortalForms.validate(form)) event.preventDefault();
  }, { capture: true });
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

const otpCountdown = document.querySelector("[data-otp-resend-countdown]");
const otpResendStatus = document.querySelector("[data-otp-resend-status]");
const otpResendButton = document.querySelector("[data-otp-resend-button]");
if (otpCountdown && otpResendStatus && otpResendButton) {
  let secondsRemaining = Math.max(0, Number.parseInt(otpCountdown.textContent || "0", 10) || 0);
  const renderCountdown = () => {
    if (secondsRemaining <= 0) {
      otpResendStatus.textContent = "You can request another code now.";
      otpResendButton.disabled = false;
      return false;
    }
    const minutes = Math.floor(secondsRemaining / 60).toString().padStart(2, "0");
    const seconds = (secondsRemaining % 60).toString().padStart(2, "0");
    otpResendStatus.textContent = `Resend available in ${minutes}:${seconds}.`;
    otpResendButton.disabled = true;
    secondsRemaining -= 1;
    return true;
  };
  if (renderCountdown()) {
    const timer = window.setInterval(() => {
      if (!renderCountdown()) window.clearInterval(timer);
    }, 1000);
  }
}
