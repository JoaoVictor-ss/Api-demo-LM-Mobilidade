from fastapi import FastAPI
from tavily import TavilyClient
from dotenv import load_dotenv
from pathlib import Path
import os
import re

# ==========================================
# Carrega .env
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

if not TAVILY_API_KEY:
    raise ValueError(
        "TAVILY_API_KEY não encontrada no arquivo .env"
    )

# ==========================================
# FastAPI
# ==========================================

app = FastAPI()

client = TavilyClient(
    api_key=TAVILY_API_KEY
)

# ==========================================
# Mantém apenas Webmotors
# ==========================================

def keep_only_webmotors(results):

    return [
        r
        for r in results
        if "webmotors.com.br" in r.get("url", "")
    ]

# ==========================================
# Extrai preço e km
# ==========================================

def extract_vehicle_data(text):

    preco_pattern = r"R\$\s?([\d\.]+)"
    km_pattern = r"([\d\.]+)\s?Km"

    precos = re.findall(
        preco_pattern,
        text,
        flags=re.IGNORECASE
    )

    kms = re.findall(
        km_pattern,
        text,
        flags=re.IGNORECASE
    )

    anuncios = []

    total = min(
        len(precos),
        len(kms)
    )

    for i in range(total):

        try:

            preco = int(
                precos[i].replace(".", "")
            )

            km = int(
                kms[i].replace(".", "")
            )

            anuncios.append({
                "preco": preco,
                "km": km
            })

        except:
            pass

    return anuncios

# ==========================================
# Endpoint
# ==========================================

@app.get("/search_vehicle")
def search_vehicle(
    marca: str,
    modelo: str,
    ano: int,
    cor: str = "",
    localidade: str = ""
):

    query = f"""
    site:webmotors.com.br
    {marca}
    {modelo}
    {ano}
    {cor}
    {localidade}
    veículo usado
    preço
    quilometragem
    """

    result = client.search(
        query=query,
        search_depth="advanced",
        max_results=50
    )

    webmotors_results = keep_only_webmotors(
        result.get("results", [])
    )

    anuncios_extraidos = []

    for item in webmotors_results:

        content = item.get(
            "content",
            ""
        )

        anuncios = extract_vehicle_data(
            content
        )

        for anuncio in anuncios:

            anuncios_extraidos.append({

                "titulo": item.get(
                    "title",
                    ""
                ),

                "url": item.get(
                    "url",
                    ""
                ),

                "preco": anuncio["preco"],

                "km": anuncio["km"],

                "localidade_pesquisada": localidade,

                "cor_pesquisada": cor
            })

    # médias

    media_preco = None
    media_km = None

    if anuncios_extraidos:

        media_preco = round(
            sum(
                a["preco"]
                for a in anuncios_extraidos
            )
            / len(anuncios_extraidos),
            2
        )

        media_km = round(
            sum(
                a["km"]
                for a in anuncios_extraidos
            )
            / len(anuncios_extraidos),
            2
        )

    return {

        "marca": marca,

        "modelo": modelo,

        "ano": ano,

        "cor": cor,

        "localidade": localidade,

        "total_anuncios": len(
            anuncios_extraidos
        ),

        "media_preco": media_preco,

        "media_km": media_km,

        "anuncios": anuncios_extraidos
    }