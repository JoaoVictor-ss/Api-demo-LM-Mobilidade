# API demo — LM Mobilidade

API FastAPI para consultar veículos na Webmotors.

Duas estratégias:

| Endpoint | Fonte | Qualidade do dado |
|---|---|---|
| `GET /search_vehicle` | Tavily (busca indexada) + regex nos snippets | aproximado |
| `GET /webmotors/*` | **API interna da Webmotors** (`/api/detail`, `/api/search`) | **dado real e estruturado** |

## Fluxo recomendado: UI + Bedrock Agent

O resultado desejado sai pela UI em `http://localhost:8000/` ou pelo endpoint
`POST /recommendation`.

Esse fluxo usa o `webmotors_scraper.py` como fonte verdadeira: ele consulta a
Webmotors, busca os detalhes dos anúncios, calcula preço médio e km médio, monta
um resumo dos melhores candidatos e envia esse contexto para um AWS Bedrock
Agent. O `/search_vehicle` é legado da versão 1.0 e traz dados mais simples.

Os endpoints `/webmotors/*` retornam preço, km, cor, câmbio, cidade/UF, ano de
fabricação/modelo etc. direto da fonte.

## Pesquisa em lote por Excel

Na UI existe o modo **Varios por Excel**. Nele voce envia uma planilha `.xlsx`
com ate 20 carros e a aplicacao devolve outro Excel para download com o melhor
anuncio ranqueado para cada linha. A busca em lote tambem usa a Webmotors real,
nao o endpoint legado `/search_vehicle`.

Colunas aceitas na planilha:

| Coluna | Obrigatoria | Exemplo |
|---|---:|---|
| `marca` | sim | Renault |
| `modelo` | nao | Sandero |
| `localidade` | nao | RJ |
| `cor` | nao | Branco |
| `ano_de` | nao | 2019 |
| `ano_ate` | nao | 2022 |

Tambem da para baixar um modelo pronto pela UI ou pelo endpoint
`GET /recommendation/batch/template`.

## Como o bloqueio anti-bot é resolvido

A Webmotors fica atrás do **PerimeterX**. `curl`/`fetch` puro com cookie
copiado cai em 401/403: o token `_px3` é emitido por uma sessão de navegador
real e amarrado ao fingerprint + IP. O fluxo (em `webmotors_scraper.py`):

1. **Mint** — abre o **Chrome real** (headed) via Playwright
   com um init-script de stealth (patch de `navigator.webdriver` etc.). Deixa o
   sensor PerimeterX rodar e, **se aparecer o captcha "Pressione e segure",
   resolve sozinho** segurando o mouse no botão via Playwright. Colhe os
   cookies `_px3`, `_pxvid`, `pxcts`, `_pxde` + User-Agent.
2. **Scrape** — com esses cookies + UA + mesmo IP, `requests` puro no
   `/api/detail/...` e `/api/search/car` retorna **200 de forma consistente**.
   O `_px3` dura ~10 min e não rotaciona; quando uma chamada toma 403, o cliente
   **re-minta sozinho**.

Validado: 40/40 hits no detail + 12/12 carros distintos a partir da busca, e
cold-start completo (mint → busca → detalhe) reproduzível do zero.

## Como rodar

Pré-requisitos:

- Python 3.10+
- Google Chrome instalado

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn vehicle_search:app --reload
```

Depois abra `http://localhost:8000/`.

> Se o `Activate.ps1` for bloqueado pela política de execução, rode antes:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`
> (ou, no `cmd`, use `.venv\Scripts\activate.bat`).

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn vehicle_search:app --reload
```

Depois abra `http://localhost:8000/`.

No primeiro request a `/webmotors/*`, o Playwright abre o Chrome para gerar a
sessão. Depois disso, a API reutiliza a sessão em cache.

Se o Chrome estiver em um local não padrão, defina `WEBMOTORS_CHROME_PATH` no
`.env`.

## Configurar AWS Bedrock Agent

Access key e secret access key autenticam sua aplicacao na AWS, mas para a
recomendacao funcionar voce tambem precisa:

- Uma regiao AWS com Amazon Bedrock disponivel, por exemplo `us-east-1`.
- Acesso ao foundation model escolhido para o Agent no Amazon Bedrock.
- Um Bedrock Agent criado, preparado e testado.
- Um alias do Agent criado para uso pela aplicacao.
- Permissao IAM para chamar `bedrock:InvokeAgent`.

Preencha o `.env` assim:

```env
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=sua_access_key
AWS_SECRET_ACCESS_KEY=sua_secret_key
BEDROCK_AGENT_ID=seu_agent_id
BEDROCK_AGENT_ALIAS_ID=seu_agent_alias_id
```

Para criar o Agent pelo console da AWS:

1. Abra Amazon Bedrock no console AWS.
2. Entre em Model access e confirme que o modelo escolhido esta liberado.
3. Entre em Agents e clique em Create Agent.
4. Escolha um foundation model, por exemplo um Claude ou Amazon Nova disponivel
   na sua conta.
5. Use uma instrucao como:

```text
Voce e um consultor de compra de carros usados. Analise os dados JSON enviados
pela aplicacao, compare preco, quilometragem, ano, localidade, cor e versao,
e recomende a melhor opcao de compra sem inventar dados.
```

6. Salve e clique em Prepare.
7. Teste o Agent no painel lateral.
8. Crie um alias para deploy. Copie o Agent ID e o Alias ID para o `.env`.

Observacao: este projeto nao precisa que o Agent tenha action group, porque o
backend ja entrega para ele os dados da Webmotors prontos em JSON.

## Testar

Com a API no ar, abra `http://localhost:8000/docs` ou rode:

```bash
curl "http://localhost:8000/webmotors/search?marca=honda&modelo=city&per_page=12"
curl "http://localhost:8000/webmotors/search?marca=honda&modelo=city&localidade=S%C3%A3o%20Paulo&cor=Branco&ano_de=2019&ano_ate=2022"
curl -X POST "http://localhost:8000/recommendation" -H "Content-Type: application/json" -d "{\"marca\":\"honda\",\"modelo\":\"city\",\"localidade\":\"Sao Paulo\",\"cor\":\"Branco\",\"ano_de\":2019,\"ano_ate\":2022,\"pages\":1,\"per_page\":8}"
curl -o modelo_pesquisa_carros.xlsx "http://localhost:8000/recommendation/batch/template"
curl -X POST "http://localhost:8000/recommendation/batch" -F "file=@modelo_pesquisa_carros.xlsx" -o resultado_recomendacoes.xlsx
```

No PowerShell, use `curl.exe` em vez de `curl`.

Também dá para testar sem subir a API:

```bash
python webmotors_scraper.py search --marca honda --modelo city --per-page 1
python webmotors_scraper.py search --marca honda --modelo city --localidade "São Paulo" --cor Branco --ano-de 2019 --ano-ate 2022
python webmotors_scraper.py detail --marca honda --modelo city --pages 1 --per-page 1
```

## Endpoints

```bash
# Busca real (lista + médias)
curl 'http://localhost:8000/webmotors/search?marca=honda&modelo=city&per_page=12'
curl 'http://localhost:8000/webmotors/search?marca=honda&modelo=city&localidade=S%C3%A3o%20Paulo&cor=Branco&ano_de=2019&ano_ate=2022'

# Busca + detalhe completo de cada usado
curl 'http://localhost:8000/webmotors/detail?marca=honda&modelo=city&pages=1&per_page=12'
curl 'http://localhost:8000/webmotors/detail?marca=honda&modelo=city&localidade=S%C3%A3o%20Paulo&cor=Branco&ano_de=2019&ano_ate=2022&pages=1&per_page=12'

# Detalhe por URL de detail
curl 'http://localhost:8000/webmotors/detail_by_url?url=https://www.webmotors.com.br/api/detail/car/honda/city/15-i-vtec-flex-hatch-exl-cvt/4-portas/2022/69863170'

# Força um novo mint (reabre o Chrome)
curl -X POST 'http://localhost:8000/webmotors/refresh_session'
curl -X POST 'http://localhost:8000/webmotors/refresh_session?clear_profile=true'
```

## CLI (sem subir a API)

```bash
python webmotors_scraper.py mint                              # abre o browser e salva a sessão
python webmotors_scraper.py mint --clear-profile             # limpa rastros locais antes do mint
python webmotors_scraper.py search --marca honda --modelo city --per-page 12
python webmotors_scraper.py search --marca honda --modelo city --localidade "São Paulo" --cor Branco --ano-de 2019 --ano-ate 2022
python webmotors_scraper.py detail --marca honda --modelo city --per-page 6
python webmotors_scraper.py url "https://www.webmotors.com.br/api/detail/car/..."
```

## Configuração

Tudo via env (ver `.env.example`): `WEBMOTORS_CHROME_PATH`, `WEBMOTORS_UA`,
`WEBMOTORS_CACHE`, `WEBMOTORS_PROFILE_DIR`, `WEBMOTORS_SESSION_TTL`.
