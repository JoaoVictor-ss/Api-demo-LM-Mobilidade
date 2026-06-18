# API demo — LM Mobilidade

API FastAPI para consultar veículos na Webmotors.

Duas estratégias:

| Endpoint | Fonte | Qualidade do dado |
|---|---|---|
| `GET /search_vehicle` | Tavily (busca indexada) + regex nos snippets | aproximado |
| `GET /webmotors/*` | **API interna da Webmotors** (`/api/detail`, `/api/search`) | **dado real e estruturado** |

Os endpoints `/webmotors/*` retornam preço, km, cor, câmbio, cidade/UF, ano de
fabricação/modelo etc. direto da fonte.

## Como o bloqueio anti-bot é resolvido

A Webmotors fica atrás do **PerimeterX**. `curl`/`fetch` puro com cookie
copiado cai em 401/403: o token `_px3` é emitido por uma sessão de navegador
real e amarrado ao fingerprint + IP. O fluxo (em `webmotors_scraper.py`):

1. **Mint** — abre o **Chrome real** (headed) via [`agent-browser`](https://www.npmjs.com/package/agent-browser)
   com um init-script de stealth (patch de `navigator.webdriver` etc.). Deixa o
   sensor PerimeterX rodar e, **se aparecer o captcha "Pressione e segure",
   resolve sozinho** segurando o mouse no botão via eventos CDP. Colhe os
   cookies `_px3`, `_pxvid`, `pxcts`, `_pxde` + User-Agent.
2. **Scrape** — com esses cookies + UA + mesmo IP, `requests` puro no
   `/api/detail/...` e `/api/search/car` retorna **200 de forma consistente**.
   O `_px3` dura ~10 min e não rotaciona; quando uma chamada toma 403, o cliente
   **re-minta sozinho**.

Validado: 40/40 hits no detail + 12/12 carros distintos a partir da busca, e
cold-start completo (mint → busca → detalhe) reproduzível do zero.

## Setup (macOS / Linux)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# dependências de runtime do mint (uma vez)
npm i -g agent-browser && agent-browser install
# + Google Chrome instalado

cp .env.example .env   # ajuste se necessário (Tavily é opcional)
uvicorn vehicle_search:app --reload
```

> No primeiro request a `/webmotors/*` o Chrome abre por ~30–60s (mint). Os
> requests seguintes reusam a sessão em cache (~8 min) e são HTTP puro, rápidos.
> Sem ambiente gráfico? Exporte `WEBMOTORS_COOKIE` com cookies válidos (ver
> `.env.example`) e o browser nem é chamado.

## Rodar no Windows

Roda no Windows (x64). O caminho do Chrome e o User-Agent já são detectados por
SO automaticamente — você não precisa configurar nada disso na mão.

Pré-requisito comum: **Python 3.10+** (baixe em
[python.org](https://www.python.org/downloads/) e marque **"Add python.exe to
PATH"** no instalador). Comandos abaixo em **PowerShell**, na raiz do projeto:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

> Se o `Activate.ps1` for bloqueado pela política de execução, rode antes:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`
> (ou, no `cmd`, use `.venv\Scripts\activate.bat`).

Agora escolha **um** dos dois caminhos pra passar pelo PerimeterX. Pra só ver
funcionando rápido, use o **A**; pra deixar rodando sem copiar cookie na mão,
use o **B**.

### Caminho A — Bypass por cookie (mais simples, sem Node)

Python puro, sem browser automatizado. Bom pra testar em 2 minutos.

1. Abra `https://www.webmotors.com.br` no seu Chrome (resolva o "Pressione e
   segure" se aparecer).
2. `F12` → aba **Application** → **Cookies** → `https://www.webmotors.com.br`.
   Copie os valores de `_px3`, `_pxvid`, `pxcts` e `_pxde`.
3. No `.env`, preencha numa linha só:
   ```env
   WEBMOTORS_COOKIE=_px3=<valor>; _pxvid=<valor>; pxcts=<valor>; _pxde=<valor>
   ```
4. Suba a API:
   ```powershell
   uvicorn vehicle_search:app --reload
   ```

> O `_px3` expira em ~10 min. Quando os endpoints começarem a dar **502**,
> repita os passos 1–3 com cookies novos — ou use o Caminho B, que renova
> sozinho.

### Caminho B — Automático (sem copiar cookie)

Abre o Chrome real uma vez, resolve o PerimeterX sozinho e re-minta quando o
token expira. Precisa de mais duas coisas instaladas:

- **Google Chrome** (o caminho padrão é detectado automaticamente).
- **Node.js 24+** ([nodejs.org](https://nodejs.org)) + o `agent-browser`:
  ```powershell
  npm i -g agent-browser
  agent-browser install
  ```

Deixe o `.env` **sem** `WEBMOTORS_COOKIE` e suba a API:

```powershell
uvicorn vehicle_search:app --reload
```

No primeiro request a `/webmotors/*` o Chrome abre por ~30–60s (mint); os
seguintes reusam a sessão em cache.

> Chrome instalado em local não-padrão? Defina `WEBMOTORS_CHROME_PATH` no `.env`
> apontando pro `chrome.exe`.

### Testar

Com a API no ar (qualquer caminho), abra `http://localhost:8000/docs` no
navegador (Swagger UI) ou, em outro terminal:

```powershell
curl.exe "http://localhost:8000/webmotors/search?marca=honda&modelo=city&per_page=12"
```

> No Windows use **`curl.exe`** — o `curl` "pelado" no PowerShell é um alias de
> `Invoke-WebRequest` e não aceita os mesmos argumentos.

## Endpoints

```bash
# Busca real (lista + médias)
curl 'http://localhost:8000/webmotors/search?marca=honda&modelo=city&per_page=12'

# Busca + detalhe completo de cada usado
curl 'http://localhost:8000/webmotors/detail?marca=honda&modelo=city&pages=1&per_page=12'

# Detalhe por URL de detail
curl 'http://localhost:8000/webmotors/detail_by_url?url=https://www.webmotors.com.br/api/detail/car/honda/city/15-i-vtec-flex-hatch-exl-cvt/4-portas/2022/69863170'

# Força um novo mint (reabre o Chrome)
curl -X POST 'http://localhost:8000/webmotors/refresh_session'
```

## CLI (sem subir a API)

```bash
python webmotors_scraper.py mint                              # abre o browser e salva a sessão
python webmotors_scraper.py search --marca honda --modelo city --per-page 12
python webmotors_scraper.py detail --marca honda --modelo city --per-page 6
python webmotors_scraper.py url "https://www.webmotors.com.br/api/detail/car/..."
```

## Configuração

Tudo via env (ver `.env.example`): `WEBMOTORS_CHROME_PATH`, `WEBMOTORS_UA`,
`WEBMOTORS_COOKIE` (bypass do browser), `WEBMOTORS_CACHE`,
`WEBMOTORS_PROFILE_DIR`, `WEBMOTORS_SESSION_TTL`.
