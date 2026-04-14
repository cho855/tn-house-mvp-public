(function () {
  const storedBase = localStorage.getItem("TN_API_BASE");
  const isProdHost = window.location.hostname.endsWith("cindyho.work");
  const API_BASE = storedBase || (isProdHost
    ? "https://api.cindyho.work"
    : (window.location.port === "8000" ? "" : "http://127.0.0.1:8000"));

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (body && body.detail && typeof body.detail === "object" && body.detail.message) {
          detail += `: ${body.detail.message}`;
        } else if (body && typeof body.detail === "string") {
          detail += `: ${body.detail}`;
        }
      } catch (_) {
        // Ignore non-JSON error responses.
      }
      throw new Error(detail);
    }
    return await response.json();
  }

  window.TNHouseApi = { API_BASE, fetchJson };
})();
