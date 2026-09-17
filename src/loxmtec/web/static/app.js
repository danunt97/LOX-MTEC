/* Shared helpers for the LOX-MTEC web GUI. */

const LX = {
  async get(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  },

  async send(url, body, method = "POST") {
    const response = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      data = {};
    }
    if (!response.ok) {
      const errors = data.errors || [data.message || `HTTP ${response.status}`];
      throw new Error(errors.join(" · "));
    }
    return data;
  },

  toast(message, kind = "") {
    const host = document.getElementById("toast");
    if (!host) return;
    const box = document.createElement("div");
    box.className = `toast ${kind}`;
    box.textContent = message;
    host.appendChild(box);
    setTimeout(() => box.remove(), 5000);
  },

  pill(element, state, text) {
    if (!element) return;
    element.className = `pill ${state || "unknown"}`;
    element.textContent = text;
  },

  number(value, decimals = 1) {
    if (value === null || value === undefined || value === "") return "-";
    if (typeof value !== "number") return String(value);
    return value.toLocaleString("de-DE", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  },

  duration(seconds) {
    if (seconds === null || seconds === undefined) return "-";
    const total = Math.max(0, Math.round(seconds));
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${total % 60}s`;
    return `${total}s`;
  },

  /* Poll a callback on an interval and stop while the tab is hidden. */
  poll(callback, intervalMs) {
    let timer = null;
    const tick = async () => {
      if (!document.hidden) {
        try {
          await callback();
        } catch (err) {
          console.error(err);
        }
      }
      timer = setTimeout(tick, intervalMs);
    };
    tick();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        clearTimeout(timer);
        tick();
      }
    });
  },

  async action(url, label) {
    try {
      const data = await LX.send(url);
      LX.toast(data.message || label || "OK", data.ok === false ? "error" : "ok");
      return data;
    } catch (err) {
      LX.toast(err.message, "error");
      return { ok: false };
    }
  },
};

/* Wire up every [data-action] button to its endpoint. */
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  event.preventDefault();
  button.disabled = true;
  LX.action(button.dataset.action, button.dataset.label).finally(() => {
    button.disabled = false;
  });
});
