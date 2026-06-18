"""
Scraper real da Webmotors (API interna /api/detail e /api/search).

A Webmotors fica atrás do PerimeterX. curl/fetch puro com cookie copiado cai
em 401/403 porque o token `_px3` é emitido por uma sessão de navegador real e
amarrado ao fingerprint + IP. Este módulo resolve isso assim:

    1. MINT  -> abre o Chrome REAL (headed) via agent-browser com stealth,
                deixa o sensor PerimeterX rodar (e resolve o press-and-hold
                "Pressione e segure" se ele aparecer), e colhe os cookies
                `_px3`, `_pxvid`, `pxcts`, `_pxde` + User-Agent.
    2. SCRAPE -> com esses cookies + UA + mesmo IP, requests puro no
                `/api/detail/...` e `/api/search/car` retorna 200 de forma
                consistente. O `_px3` dura ~10 min e NÃO rotaciona, então
                quando uma chamada toma 403 o cliente re-minta sozinho.

Validado: 40/40 hits no detail + 12/12 carros distintos a partir da busca.

Dependências de runtime:
    - agent-browser (npm i -g agent-browser && agent-browser install)
    - Google Chrome instalado
    - requests

Bypass do browser: se você já tem cookies válidos, exporte
WEBMOTORS_COOKIE="_px3=...; _pxvid=...; pxcts=...; _pxde=..." e
WEBMOTORS_UA="...". Aí o mint via browser nem é chamado.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# ==========================================
# Configuração (tudo sobrescrevível por env)
# ==========================================

AGENT_BROWSER = os.getenv("AGENT_BROWSER_BIN", "agent-browser")

# Caminho do Chrome real. Default macOS; em Linux aponte para google-chrome.
_DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_PATH = os.getenv("WEBMOTORS_CHROME_PATH", _DEFAULT_CHROME)

# UA coerente com o Chrome usado no mint. Tem que bater com o binário real.
DEFAULT_UA = os.getenv(
    "WEBMOTORS_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
)

BASE_URL = "https://www.webmotors.com.br"
SESSION_NAME = os.getenv("WEBMOTORS_SESSION", "wm-scraper")
PROFILE_DIR = os.getenv(
    "WEBMOTORS_PROFILE_DIR",
    str(Path(tempfile.gettempdir()) / "wm-scraper-profile"),
)
CACHE_PATH = Path(
    os.getenv("WEBMOTORS_CACHE", str(Path(tempfile.gettempdir()) / "wm-session.json"))
)

# Viewport fixa -> o card do press-and-hold fica sempre centralizado e o botão
# numa coordenada determinística (centro horizontal, +57px do centro vertical).
VIEWPORT = (1280, 800)
_BUTTON_X = VIEWPORT[0] // 2
_BUTTON_Y = VIEWPORT[1] // 2 + 57

# Cookies que importam pro PerimeterX (o resto é analytics).
PX_COOKIES = ("_px3", "_pxvid", "pxcts", "_pxde")

# `_px3` dura ~10 min; tratamos como velho antes disso pra re-mintar com folga.
SESSION_TTL_SECONDS = int(os.getenv("WEBMOTORS_SESSION_TTL", "480"))

# Script de stealth injetado antes de qualquer JS da página (patch dos tells
# clássicos de automação que o PerimeterX procura).
_STEALTH_JS = """
(() => {
  try { Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => undefined, configurable: true }); } catch (e) {}
  try { if (!window.chrome) window.chrome = {}; if (!window.chrome.runtime) window.chrome.runtime = {}; } catch (e) {}
  try {
    const q = navigator.permissions && navigator.permissions.query;
    if (q) navigator.permissions.query = (p) => p && p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission }) : q(p);
  } catch (e) {}
  try { Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5], configurable: true }); } catch (e) {}
  try { Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR','pt','en-US','en'], configurable: true }); } catch (e) {}
})();
"""


class WebmotorsBlocked(RuntimeError):
    """Falha em obter uma sessão válida (PerimeterX bloqueou de novo)."""


@dataclass
class WebmotorsSession:
    cookies: dict[str, str]
    user_agent: str
    minted_at: float = field(default_factory=time.time)

    @property
    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    @property
    def age(self) -> float:
        return time.time() - self.minted_at

    def is_fresh(self) -> bool:
        return bool(self.cookies.get("_px3")) and self.age < SESSION_TTL_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "cookies": self.cookies,
            "user_agent": self.user_agent,
            "minted_at": self.minted_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WebmotorsSession":
        return cls(
            cookies=data["cookies"],
            user_agent=data["user_agent"],
            minted_at=data.get("minted_at", time.time()),
        )


# ==========================================
# agent-browser: helpers de subprocess
# ==========================================

def _ab(args: list[str], *, session: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Roda um comando agent-browser numa sessão e devolve o CompletedProcess."""
    return subprocess.run(
        [AGENT_BROWSER, "--session", session, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _ab_eval(expr: str, *, session: str) -> str:
    out = _ab(["eval", expr], session=session).stdout.strip()
    # agent-browser imprime a string entre aspas; remove se for o caso.
    if len(out) >= 2 and out[0] == '"' and out[-1] == '"':
        return json.loads(out)
    return out


def _ab_get_cookies(*, session: str) -> list[dict[str, Any]]:
    out = _ab(["cookies", "get", "--json"], session=session).stdout
    try:
        return json.loads(out)["data"]["cookies"]
    except (json.JSONDecodeError, KeyError):
        return []


def _is_blocked(*, session: str) -> bool:
    title = _ab(["get", "title"], session=session).stdout.lower()
    return "denied" in title or "acesso negado" in title or "pressione e segure" in title


def _solve_press_and_hold(*, session: str, attempts: int = 3) -> bool:
    """Resolve o captcha "Pressione e segure" segurando o mouse no botão.

    O botão fica centralizado horizontalmente e ~57px abaixo do centro
    vertical (card de tamanho fixo, centralizado na viewport fixa). Eventos
    de mouse via CDP são em nível de browser e ignoram os hooks de geometria
    que o PerimeterX coloca no iframe.
    """
    for i in range(attempts):
        hold_ms = 7000 + i * 1500  # vai segurando mais a cada tentativa
        _ab(["mouse", "move", str(_BUTTON_X), str(_BUTTON_Y)], session=session)
        _ab(["wait", "400"], session=session)
        _ab(["mouse", "down"], session=session)
        _ab(["wait", str(hold_ms)], session=session, timeout=hold_ms // 1000 + 20)
        _ab(["mouse", "up"], session=session)
        _ab(["wait", "4000"], session=session)
        if not _is_blocked(session=session):
            return True
    return not _is_blocked(session=session)


# ==========================================
# MINT: abre o Chrome real e colhe os cookies
# ==========================================

def mint_session(*, headless: bool = False, verbose: bool = True) -> WebmotorsSession:
    """Abre o Chrome real, passa pelo PerimeterX e devolve uma sessão válida.

    Bypass: se WEBMOTORS_COOKIE estiver no ambiente, usa direto (sem browser).
    """
    env_cookie = os.getenv("WEBMOTORS_COOKIE")
    if env_cookie:
        cookies = dict(
            part.split("=", 1)
            for part in (p.strip() for p in env_cookie.split(";"))
            if "=" in part
        )
        return WebmotorsSession(cookies=cookies, user_agent=DEFAULT_UA)

    def log(msg: str) -> None:
        if verbose:
            print(f"[mint] {msg}", file=sys.stderr)

    stealth_file = Path(tempfile.gettempdir()) / "wm-stealth.js"
    stealth_file.write_text(_STEALTH_JS)

    launch = [
        "--executable-path", CHROME_PATH,
        "--user-agent", DEFAULT_UA,
        "--init-script", str(stealth_file),
        "--profile", PROFILE_DIR,
    ]
    if not headless:
        launch.append("--headed")

    # Fecha sessão anterior e sobe limpa na viewport fixa antes de navegar.
    _ab(["close"], session=SESSION_NAME)
    log("lançando Chrome real...")
    _ab(["open", *launch], session=SESSION_NAME)
    _ab(["set", "viewport", str(VIEWPORT[0]), str(VIEWPORT[1])], session=SESSION_NAME)
    _ab(["open", BASE_URL], session=SESSION_NAME)
    _ab(["wait", "--load", "domcontentloaded"], session=SESSION_NAME)
    _ab(["wait", "3000"], session=SESSION_NAME)

    if _is_blocked(session=SESSION_NAME):
        log("press-and-hold detectado, resolvendo...")
        if not _solve_press_and_hold(session=SESSION_NAME):
            raise WebmotorsBlocked("não consegui resolver o press-and-hold do PerimeterX")
        log("captcha resolvido.")
    else:
        log("passou direto (sem captcha).")

    # Espera o sensor PerimeterX emitir o _px3.
    cookies: dict[str, str] = {}
    for _ in range(8):
        raw = _ab_get_cookies(session=SESSION_NAME)
        cookies = {
            c["name"]: c["value"]
            for c in raw
            if "webmotors.com.br" in c.get("domain", "")
        }
        if cookies.get("_px3"):
            break
        _ab(["wait", "1500"], session=SESSION_NAME)

    if not cookies.get("_px3"):
        raise WebmotorsBlocked("sessão sem _px3 após o mint")

    log(f"colhi {len(cookies)} cookies (_px3 ok).")
    return WebmotorsSession(cookies=cookies, user_agent=DEFAULT_UA)


def close_browser() -> None:
    _ab(["close"], session=SESSION_NAME)


# ==========================================
# Cache de sessão em disco
# ==========================================

def load_cached_session() -> WebmotorsSession | None:
    if not CACHE_PATH.exists():
        return None
    try:
        sess = WebmotorsSession.from_dict(json.loads(CACHE_PATH.read_text()))
        return sess if sess.is_fresh() else None
    except (json.JSONDecodeError, KeyError):
        return None


def save_cached_session(session: WebmotorsSession) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(session.to_dict()))
    except OSError:
        pass


# ==========================================
# Slug + montagem de URL de detail
# ==========================================

def slugify(value: str) -> str:
    """'1.5 i-VTEC FLEX HATCH EXL CVT' -> '15-i-vtec-flex-hatch-exl-cvt'."""
    value = value.lower().replace(".", "")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-")


def build_detail_url(
    *, make: str, model: str, version: str, doors: int | str, year: int | str, unique_id: int | str
) -> str:
    return (
        f"{BASE_URL}/api/detail/car/"
        f"{slugify(make)}/{slugify(model)}/{slugify(version)}/"
        f"{int(float(doors))}-portas/{int(float(year))}/{unique_id}"
    )


def detail_url_from_listing(listing: dict[str, Any]) -> str:
    """Monta a URL de detail a partir de um item do /api/search/car."""
    spec = listing["Specification"]
    return build_detail_url(
        make=spec["Make"]["Value"],
        model=spec["Model"]["Value"],
        version=spec["Version"]["Value"],
        doors=spec.get("NumberPorts", 4) or 4,
        year=spec.get("YearModel") or spec.get("YearFabrication"),
        unique_id=listing["UniqueId"],
    )


# ==========================================
# Cliente HTTP (com re-mint automático no 403)
# ==========================================

class WebmotorsClient:
    def __init__(self, *, session: WebmotorsSession | None = None, headless: bool = False):
        self._session = session or load_cached_session()
        self._headless = headless

    # -- sessão ----------------------------------------------------------

    def ensure_session(self, *, force: bool = False) -> WebmotorsSession:
        if force or self._session is None or not self._session.is_fresh():
            self._session = mint_session(headless=self._headless)
            save_cached_session(self._session)
        return self._session

    def _headers(self) -> dict[str, str]:
        sess = self.ensure_session()
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "user-agent": sess.user_agent,
            "referer": f"{BASE_URL}/",
            "cookie": sess.cookie_header,
        }

    def _get(self, url: str, *, _retried: bool = False) -> requests.Response:
        resp = requests.get(url, headers=self._headers(), timeout=30)
        if resp.status_code in (401, 403) and not _retried:
            # token velho/queimado -> re-minta uma vez e tenta de novo.
            self.ensure_session(force=True)
            return self._get(url, _retried=True)
        return resp

    # -- API -------------------------------------------------------------

    def search(
        self,
        *,
        make: str,
        model: str = "",
        page: int = 1,
        per_page: int = 24,
        order: int = 1,
        extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Lista anúncios. Retorna o JSON cru do /api/search/car."""
        inner = {"tipoveiculo": "carros", "marca1": make.lower(), "page": str(page)}
        if model:
            inner["modelo1"] = model.lower()
        if extra:
            inner.update(extra)
        inner_qs = "&".join(f"{k}={v}" for k, v in inner.items())
        inner_url = requests.utils.quote(f"{BASE_URL}/carros/estoque?{inner_qs}", safe="")
        url = (
            f"{BASE_URL}/api/search/car?url={inner_url}"
            f"&displayPerPage={per_page}&actualPage={page}"
            f"&showMenu=true&showCount=true&showBreadCrumb=true"
            f"&order={order}&mediaZeroKm=true"
        )
        resp = self._get(url)
        resp.raise_for_status()
        return resp.json()

    def get_detail(self, *, listing: dict[str, Any] | None = None, url: str | None = None) -> dict[str, Any]:
        """Detalhe completo de um anúncio (a partir de um item da busca ou URL)."""
        if url is None:
            if listing is None:
                raise ValueError("informe listing= ou url=")
            url = detail_url_from_listing(listing)
        resp = self._get(url)
        resp.raise_for_status()
        return resp.json()

    def iter_details(
        self, *, make: str, model: str = "", pages: int = 1, per_page: int = 24
    ) -> list[dict[str, Any]]:
        """Busca + detalha todos os usados (UniqueId > 0) das páginas pedidas."""
        out: list[dict[str, Any]] = []
        for p in range(1, pages + 1):
            results = self.search(make=make, model=model, page=p, per_page=per_page)
            for item in results.get("SearchResults", []):
                if item.get("UniqueId", 0) > 0:
                    out.append(self.get_detail(listing=item))
        return out


# ==========================================
# CLI
# ==========================================

def normalize_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Extrai os campos principais de um payload de detail."""
    spec = detail.get("Specification", {})
    prices = detail.get("Prices", {})
    seller = detail.get("Seller", {})
    return {
        "unique_id": detail.get("UniqueId"),
        "titulo": spec.get("Title"),
        "marca": (spec.get("Make") or {}).get("Value"),
        "modelo": (spec.get("Model") or {}).get("Value"),
        "versao": (spec.get("Version") or {}).get("Value"),
        "ano_modelo": spec.get("YearModel"),
        "ano_fabricacao": spec.get("YearFabrication"),
        "km": spec.get("Odometer"),
        "cor": (spec.get("Color") or {}).get("Primary"),
        "cambio": spec.get("Transmission"),
        "preco": prices.get("Price"),
        "cidade": seller.get("City"),
        "estado": seller.get("State"),
    }


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Scraper real da Webmotors (PerimeterX bypass).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_mint = sub.add_parser("mint", help="abre o browser, resolve o PX e salva a sessão")
    p_mint.add_argument("--headless", action="store_true")

    p_search = sub.add_parser("search", help="lista anúncios")
    p_search.add_argument("--marca", required=True)
    p_search.add_argument("--modelo", default="")
    p_search.add_argument("--page", type=int, default=1)
    p_search.add_argument("--per-page", type=int, default=24)

    p_detail = sub.add_parser("detail", help="detalha N usados de uma busca")
    p_detail.add_argument("--marca", required=True)
    p_detail.add_argument("--modelo", default="")
    p_detail.add_argument("--pages", type=int, default=1)
    p_detail.add_argument("--per-page", type=int, default=12)

    p_url = sub.add_parser("url", help="detalha uma URL de detail direto")
    p_url.add_argument("url")

    args = ap.parse_args(argv)
    client = WebmotorsClient(headless=getattr(args, "headless", False))

    if args.cmd == "mint":
        sess = client.ensure_session(force=True)
        print(json.dumps({"cookies": list(sess.cookies), "user_agent": sess.user_agent}, indent=2))
    elif args.cmd == "search":
        data = client.search(make=args.marca, model=args.modelo, page=args.page, per_page=args.per_page)
        rows = [
            normalize_detail({"Specification": it["Specification"], "Prices": it["Prices"], "UniqueId": it["UniqueId"]})
            for it in data.get("SearchResults", [])
        ]
        print(json.dumps({"count": data.get("Count"), "results": rows}, ensure_ascii=False, indent=2))
    elif args.cmd == "detail":
        details = client.iter_details(make=args.marca, model=args.modelo, pages=args.pages, per_page=args.per_page)
        print(json.dumps([normalize_detail(d) for d in details], ensure_ascii=False, indent=2))
    elif args.cmd == "url":
        print(json.dumps(normalize_detail(client.get_detail(url=args.url)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
