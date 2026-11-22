
(() => {
  "use strict";

  // PDFKit backend address
  const API_BASE = "http://127.0.0.1:8000";

  const sidebar = document.getElementById("sidebar");
  const menuToggle = document.getElementById("menuToggle");

  // --------------------------------
  // 1. MOBILE SIDEBAR
  // --------------------------------

  if (sidebar && menuToggle) {
    menuToggle.addEventListener("click", () => {
      const isOpen = sidebar.classList.toggle("open");

      menuToggle.setAttribute(
        "aria-expanded",
        String(isOpen)
      );
    });

    // Close sidebar when clicking outside
    document.addEventListener("click", (event) => {
      if (
        window.innerWidth <= 900 &&
        sidebar.classList.contains("open") &&
        !sidebar.contains(event.target) &&
        !menuToggle.contains(event.target)
      ) {
        sidebar.classList.remove("open");

        menuToggle.setAttribute(
          "aria-expanded",
          "false"
        );
      }
    });

    // Close sidebar after choosing a link
    sidebar.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        sidebar.classList.remove("open");

        menuToggle.setAttribute(
          "aria-expanded",
          "false"
        );
      });
    });
  }

  // --------------------------------
  // 2. DASHBOARD ELEMENTS
  // --------------------------------

  const historyPanel =
    document.querySelector(".history-panel");

  const sidebarFoot =
    document.querySelector(".sidebar-foot");


  // --------------------------------
  // 3. FORMAT DATE
  // --------------------------------

  function formatDate(value) {
    if (!value) return "—";

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return String(value);
    }

    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    });
  }


  // --------------------------------
  // 4. DISPLAY RECENT ACTIVITY
  // --------------------------------

  function renderHistory(operations) {
    if (!historyPanel) return;

    historyPanel
      .querySelector(".empty-history")
      ?.remove();

    historyPanel
      .querySelector(".history-rows")
      ?.remove();

    // No previous operations
    if (
      !Array.isArray(operations) ||
      operations.length === 0
    ) {
      const empty = document.createElement("div");

      empty.className = "empty-history";

      const icon = document.createElement("div");
      icon.className = "empty-icon";
      icon.textContent = "◷";

      const heading = document.createElement("strong");
      heading.textContent = "No activity yet";

      const paragraph = document.createElement("p");

      paragraph.textContent =
        "Your recent PDF operations will show up here once you get started.";

      const link = document.createElement("a");

      link.href = "#tools";
      link.textContent = "Choose your first tool →";

      empty.append(
        icon,
        heading,
        paragraph,
        link
      );

      historyPanel.appendChild(empty);

      return;
    }

    // Create activity rows
    const rows = document.createElement("div");

    rows.className = "history-rows";

    operations.forEach((operation) => {
      const row = document.createElement("div");

      row.className = "history-row";

      row.style.cssText =
        "display:grid;" +
        "grid-template-columns:1.1fr 1.5fr 1.2fr .8fr;" +
        "gap:14px;" +
        "align-items:center;" +
        "padding:16px 20px;" +
        "border-top:1px solid #eceef4;" +
        "font-size:12px;" +
        "color:#62697d;";

      // Operation type
      const type = document.createElement("strong");

      type.textContent =
        String(operation.operation_type || "PDF task")
          .replaceAll("-", " ")
          .replace(/\b\w/g, letter =>
            letter.toUpperCase()
          );

      type.style.color = "#24283b";

      // Input filenames
      const files = document.createElement("span");

      files.textContent =
        Array.isArray(operation.input_filenames)
          ? operation.input_filenames.join(", ")
          : "—";

      files.style.overflowWrap = "anywhere";

      // Date
      const date = document.createElement("span");

      date.textContent =
        formatDate(operation.created_at);

      // Operation status
      const status = document.createElement("span");

      status.textContent =
        String(operation.status || "unknown");

      status.style.cssText =
        "font-weight:700;" +
        "text-transform:capitalize;" +
        "color:" +
        (
          operation.status === "success"
            ? "#15966b"
            : operation.status === "failed"
              ? "#d45b62"
              : "#b17a26"
        );

      row.append(
        type,
        files,
        date,
        status
      );

      rows.appendChild(row);
    });

    historyPanel.appendChild(rows);
  }


  // --------------------------------
  // 5. CONNECT TO BACKEND
  // --------------------------------

  async function loadDashboard() {

    try {

      // Check backend connection
      const healthResponse = await fetch(
        `${API_BASE}/api/health`
      );

      if (!healthResponse.ok) {
        throw new Error("Backend unavailable");
      }

      if (sidebarFoot) {
        sidebarFoot.textContent =
          "● Backend connected";
      }

      // Get recent activity
      const response = await fetch(
        `${API_BASE}/api/history?limit=8`
      );

      if (!response.ok) {
        throw new Error("Could not load activity");
      }

      const data = await response.json();

      renderHistory(data.operations || []);

    } catch (error) {

      console.error("PDFKit connection error:", error);

      if (sidebarFoot) {
        sidebarFoot.textContent =
          "● Backend offline";
      }

      const paragraph =
        historyPanel?.querySelector(
          ".empty-history p"
        );

      if (paragraph) {
        paragraph.textContent =
          "Start the PDFKit backend to load your recent operations.";
      }
    }
  }


  // --------------------------------
  // 6. START DASHBOARD
  // --------------------------------

  loadDashboard();

})();
