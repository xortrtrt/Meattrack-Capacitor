(() => {
  const backButton = document.querySelector("[data-error-back]");
  if (!backButton) {
    return;
  }

  backButton.addEventListener("click", () => {
    if (window.history.length > 1) {
      window.history.back();
      return;
    }
    window.location.assign("/");
  });
})();
