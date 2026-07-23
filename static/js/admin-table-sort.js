(function () {
  const DIRECTIONS = { asc: "ascending", desc: "descending" };

  function normalize(value, type) {
    const raw = (value || "").trim();
    if (type === "number") {
      const parsed = Number(raw.replace(/[^0-9.-]/g, ""));
      return Number.isNaN(parsed) ? 0 : parsed;
    }
    if (type === "size") {
      const match = raw.match(/([0-9.,]+)\s*(B|KB|MB|GB|TB)?/i);
      if (!match) return 0;
      const parsed = Number(match[1].replace(/,/g, ""));
      if (Number.isNaN(parsed)) return 0;
      const unit = (match[2] || "B").toUpperCase();
      const multipliers = { B: 1, KB: 1024, MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4 };
      return parsed * (multipliers[unit] || 1);
    }
    if (type === "date") {
      if (!raw || raw.toLowerCase() === "never" || raw.toUpperCase() === "N/A") return 0;
      const parsed = Date.parse(raw);
      return Number.isNaN(parsed) ? 0 : parsed;
    }
    return raw.toLowerCase();
  }

  function getCellValue(row, index) {
    const cell = row.children[index];
    if (!cell) return "";
    return cell.dataset.sortValue || cell.textContent || "";
  }

  function compareValues(a, b, type, direction) {
    const first = normalize(a, type);
    const second = normalize(b, type);
    let result = 0;

    if (type === "number" || type === "date" || type === "size") {
      result = first - second;
    } else {
      result = String(first).localeCompare(String(second), undefined, {
        numeric: true,
        sensitivity: "base",
      });
    }

    return direction === "desc" ? -result : result;
  }

  function updateIndicators(table, activeHeader, direction) {
    table.querySelectorAll("th[data-sort-key]").forEach((header) => {
      header.setAttribute("aria-sort", header === activeHeader ? DIRECTIONS[direction] : "none");
      let indicator = header.querySelector(".sort-indicator");
      if (!indicator) {
        indicator = document.createElement("span");
        indicator.className = "sort-indicator ml-1 inline-block text-xs text-gray-400";
        indicator.setAttribute("aria-hidden", "true");
        header.appendChild(indicator);
      }
      indicator.textContent = header === activeHeader ? (direction === "asc" ? "↑" : "↓") : "";
    });
  }

  function sortTable(header, forcedDirection) {
    const table = header.closest("table");
    if (!table) return;

    const tbody = table.tBodies[0];
    if (!tbody) return;

    const index = Array.from(header.parentElement.children).indexOf(header);
    const type = header.dataset.sortType || "text";
    const currentDirection = header.dataset.sortDirection;
    const direction = forcedDirection || (currentDirection === "asc" ? "desc" : "asc");

    table.querySelectorAll("th[data-sort-key]").forEach((candidate) => {
      if (candidate !== header) delete candidate.dataset.sortDirection;
    });
    header.dataset.sortDirection = direction;

    const rows = Array.from(tbody.querySelectorAll("tr")).filter((row) => {
      return row.children.length > index && !row.querySelector("td[colspan]");
    });

    rows.sort((a, b) => {
      return compareValues(getCellValue(a, index), getCellValue(b, index), type, direction);
    });

    rows.forEach((row) => tbody.appendChild(row));
    updateIndicators(table, header, direction);
  }

  function prepareSortableHeaders(root) {
    root.querySelectorAll("th[data-sort-key]").forEach((header) => {
      if (header.dataset.sortReady === "true") return;
      header.dataset.sortReady = "true";
      header.tabIndex = 0;
      header.setAttribute("role", "button");
      header.setAttribute("aria-sort", "none");
      header.classList.add(
        "cursor-pointer",
        "select-none",
        "hover:bg-gray-100",
        "dark:hover:bg-gray-700",
        "transition"
      );

      const indicator = document.createElement("span");
      indicator.className = "sort-indicator ml-1 inline-block text-xs text-gray-400";
      indicator.setAttribute("aria-hidden", "true");
      indicator.textContent = "";
      header.appendChild(indicator);
    });

  }

  document.addEventListener("click", (event) => {
    const header = event.target.closest("th[data-sort-key]");
    if (!header) return;
    sortTable(header);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const header = event.target.closest("th[data-sort-key]");
    if (!header) return;
    event.preventDefault();
    sortTable(header);
  });

  document.addEventListener("DOMContentLoaded", () => {
    prepareSortableHeaders(document);
  });
})();
