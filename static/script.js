// AJAX подгрузка "Näytä lisää" (широкая кнопка, центр)
document.addEventListener("DOMContentLoaded", function () {
  const loadWrap = document.getElementById("load-more-wrap");
  if (!loadWrap) return;

  const loadBtn = document.getElementById("load-more");
  if (!loadBtn) return;

  loadBtn.addEventListener("click", async function () {
    const page = parseInt(loadBtn.getAttribute("data-page") || "2", 10);
    // берем параметры текущего поиска из формы
    const haku = document.getElementById("haku").value || "";
    const alue = document.getElementById("alue").value || "";

    loadBtn.disabled = true;
    loadBtn.textContent = "Ladataan...";

    try {
      const params = new URLSearchParams({
        haku: haku,
        alue: alue,
        page: String(page)
      });
      const resp = await fetch("/load_more?" + params.toString());
      if (!resp.ok) throw new Error("Network error");

      const data = await resp.json();
      const jobs = data.jobs || [];
      const has_next = !!data.has_next;

      const cards = document.getElementById("cards");
      if (!cards) return;

      // Добавляем карточки в DOM
      for (const j of jobs) {
        const art = document.createElement("article");
        art.className = "card";

        art.innerHTML = `
          <h3 class="card-title">${escapeHtml(j.title)}</h3>
          <div class="card-meta">
            <span class="company">${escapeHtml(j.company)}</span>
            <span class="city">• ${escapeHtml(j.city)}</span>
            <span class="source"> • ${escapeHtml(j.source)}</span>
          </div>
          <div class="card-actions">
            <a class="btn-link" href="${escapeAttr(j.link)}" target="_blank" rel="nofollow">Näytä ilmoitus</a>
          </div>
        `;
        cards.appendChild(art);
      }

      // обновляем кнопку
      if (has_next) {
        loadBtn.setAttribute("data-page", String(page + 1));
        loadBtn.disabled = false;
        loadBtn.textContent = "Näytä lisää";
      } else {
        // больше нет страниц — убираем кнопку
        loadWrap.removeChild(loadBtn);
        const gone = document.createElement("div");
        gone.textContent = "Ei lisää tuloksia.";
        loadWrap.appendChild(gone);
      }
    } catch (e) {
      console.error(e);
      loadBtn.disabled = false;
      loadBtn.textContent = "Virhe. Yritä uudelleen";
    }
  });

  // простая функция экранирования
  function escapeHtml(s) {
    if (!s) return "";
    return s.replace(/[&<>"']/g, function (m) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[m];
    });
  }
  function escapeAttr(s) {
    return encodeURI(s || "");
  }
});
