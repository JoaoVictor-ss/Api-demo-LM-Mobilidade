const form = document.querySelector("#vehicle-form");
const batchForm = document.querySelector("#batch-form");
const submitButton = document.querySelector("#submit-button");
const batchButton = document.querySelector("#batch-button");
const modeInputs = document.querySelectorAll("input[name='analysis_mode']");
const emptyState = document.querySelector("#empty-state");
const loadingState = document.querySelector("#loading-state");
const errorState = document.querySelector("#error-state");
const resultState = document.querySelector("#result-state");
const batchResultState = document.querySelector("#batch-result-state");
const metricsEl = document.querySelector("#metrics");
const batchMetricsEl = document.querySelector("#batch-metrics");
const recommendationText = document.querySelector("#recommendation-text");
const batchResultText = document.querySelector("#batch-result-text");
const batchDownload = document.querySelector("#batch-download");
const vehicleList = document.querySelector("#vehicle-list");
let batchDownloadUrl = "";

const money = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 0,
});

const number = new Intl.NumberFormat("pt-BR", {
  maximumFractionDigits: 0,
});

function setState(state) {
  emptyState.classList.toggle("hidden", state !== "empty");
  loadingState.classList.toggle("hidden", state !== "loading");
  errorState.classList.toggle("hidden", state !== "error");
  resultState.classList.toggle("hidden", state !== "result");
  batchResultState.classList.toggle("hidden", state !== "batch");
}

function setMode(mode) {
  form.classList.toggle("hidden", mode !== "single");
  batchForm.classList.toggle("hidden", mode !== "batch");
  setState("empty");
}

function readNumber(formData, key) {
  const value = String(formData.get(key) || "").trim();
  return value ? Number(value) : null;
}

function buildPayload(formData) {
  return {
    marca: String(formData.get("marca") || "").trim(),
    modelo: String(formData.get("modelo") || "").trim(),
    localidade: String(formData.get("localidade") || "").trim(),
    cor: String(formData.get("cor") || "").trim(),
    ano_de: readNumber(formData, "ano_de"),
    ano_ate: readNumber(formData, "ano_ate"),
    pages: readNumber(formData, "pages") || 1,
    per_page: readNumber(formData, "per_page") || 24,
  };
}

function formatMoney(value) {
  return value ? money.format(value) : "-";
}

function formatKm(value) {
  return value ? `${number.format(value)} km` : "-";
}

function renderMetrics(metricas) {
  metricsEl.innerHTML = [
    ["Anúncios", metricas.total_anuncios ?? 0],
    ["Preço médio", formatMoney(metricas.media_preco)],
    ["Km médio", formatKm(metricas.media_km)],
  ]
    .map(
      ([label, value]) => `
        <div class="metric">
          <span>${label}</span>
          <strong>${value}</strong>
        </div>
      `,
    )
    .join("");
}

function renderBatchMetrics(count) {
  batchMetricsEl.innerHTML = `
    <div class="metric">
      <span>Carros processados</span>
      <strong>${count || "-"}</strong>
    </div>
    <div class="metric">
      <span>Arquivo</span>
      <strong>XLSX</strong>
    </div>
    <div class="metric">
      <span>Status</span>
      <strong>Pronto</strong>
    </div>
  `;
}

function renderVehicles(vehicles) {
  vehicleList.innerHTML = vehicles
    .map((vehicle) => {
      const title = vehicle.titulo || [vehicle.marca, vehicle.modelo, vehicle.versao].filter(Boolean).join(" ");
      const location = [vehicle.cidade, vehicle.estado].filter(Boolean).join(" - ");
      const link = vehicle.url
        ? `<a class="vehicle-link" href="${vehicle.url}" target="_blank" rel="noreferrer">Abrir anúncio</a>`
        : "";
      return `
        <article class="vehicle-card">
          <h3>${title || "Anúncio sem título"}</h3>
          <div class="vehicle-meta">
            <span>${formatMoney(vehicle.preco)}</span>
            <span>${formatKm(vehicle.km)}</span>
            <span>${vehicle.ano_modelo || "-"}</span>
            <span>${vehicle.cor || "-"}</span>
            <span>${location || "-"}</span>
          </div>
          ${link}
        </article>
      `;
    })
    .join("");
}

async function submitSearch(event) {
  event.preventDefault();
  setState("loading");
  submitButton.disabled = true;

  try {
    const payload = buildPayload(new FormData(form));
    const response = await fetch("/recommendation", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Não foi possível gerar a recomendação.");
    }

    renderMetrics(data.metricas);
    recommendationText.textContent = data.recomendacao;
    renderVehicles(data.anuncios_analisados || []);
    setState("result");
  } catch (error) {
    errorState.textContent = error.message;
    setState("error");
  } finally {
    submitButton.disabled = false;
  }
}

async function submitBatch(event) {
  event.preventDefault();
  setState("loading");
  batchButton.disabled = true;

  try {
    const formData = new FormData(batchForm);
    const response = await fetch("/recommendation/batch", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Nao foi possivel processar a planilha.");
    }

    const blob = await response.blob();
    if (batchDownloadUrl) {
      URL.revokeObjectURL(batchDownloadUrl);
    }
    batchDownloadUrl = URL.createObjectURL(blob);
    batchDownload.href = batchDownloadUrl;
    const count = response.headers.get("X-Batch-Items");
    renderBatchMetrics(count);
    batchResultText.textContent = "O Excel com o melhor anuncio encontrado para cada carro esta disponivel para download.";
    setState("batch");
  } catch (error) {
    errorState.textContent = error.message;
    setState("error");
  } finally {
    batchButton.disabled = false;
  }
}

form.addEventListener("submit", submitSearch);
batchForm.addEventListener("submit", submitBatch);
modeInputs.forEach((input) => {
  input.addEventListener("change", () => setMode(input.value));
});
