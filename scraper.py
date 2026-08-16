#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ingestion quotidienne des cotations de la Bourse de Casablanca
Source : https://medias24.com/leboursier/marches-boursier (Tableau Variations)
Auteur : Expert Data & Financial Scraping
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration du Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("BVC_Scraper")

# ---------------------------------------------------------------------------
# Constantes & Sélecteurs
# ---------------------------------------------------------------------------
TARGET_URL = "https://medias24.com/leboursier/marches-boursier"
DATA_DIR = "data"

# Headers réalistes simulant un navigateur Chrome sous macOS / Windows
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://medias24.com/",
    "Sec-Ch-Ua": '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1"
}


# ---------------------------------------------------------------------------
# Fonctions de Nettoyage & Parsing
# ---------------------------------------------------------------------------
def clean_number(value_str: Optional[str]) -> Optional[float]:
    """
    Nettoie et convertit une chaîne représentant un nombre en float.
    Gère les virgules, espaces insécables, pourcentages et tirets de valeurs nulles.
    Exemples :
        '1 800'     -> 1800.0
        '98,45'     -> 98.45
        '+0,46%'    -> 0.46
        '-49,95'    -> -49.95
        '-' ou 'N/A'-> None
    """
    if not value_str:
        return None
    
    val = str(value_str).strip()
    if val in ['-', '--', 'N/A', 'nan', '', '0 0']:
        return None

    # Suppression des espaces (normaux, insécables, fins), des '+' et du '%'
    cleaned = (
        val.replace('\xa0', '')
           .replace('\u202f', '')
           .replace(' ', '')
           .replace('%', '')
           .replace('+', '')
           .replace(',', '.')
    )
    
    try:
        return float(cleaned)
    except ValueError:
        logger.warning(f"Impossible de convertir la valeur numérique : '{value_str}'")
        return None


def clean_stock_name(raw_name: str) -> str:
    """
    Nettoie le nom de la valeur en retirant les liens d'action du DOM
    ('Fiche valeur', 'Transactions de la journée une par une', 'Carnet d'ordres').
    """
    unwanted_phrases = [
        "Fiche valeur",
        "Transactions de la journée une par une",
        "Transactions de la journée",
        "Carnet d'ordres",
        "une par une"
    ]
    name = raw_name
    for phrase in unwanted_phrases:
        name = name.replace(phrase, "")
    
    # Nettoyage des espaces multiples
    return re.sub(r'\s+', ' ', name).strip()


def generate_ticker_key(name: str) -> str:
    """
    Génère un identifiant / slug normalisé en majuscules (ex: 'ATTIJARIWAFA_BANK', 'MAROC_TELECOM').
    """
    slug = re.sub(r'[^A-Za-z0-9]+', '_', name.upper()).strip('_')
    return slug


def parse_casablanca_datetime(date_str: Optional[str]) -> Optional[str]:
    """
    Convertit la chaîne de date du site (ex: '13/08/2026 à 15:42:16')
    en format standard ISO 'YYYY-MM-DD HH:MM:SS'.
    """
    if not date_str or str(date_str).strip() in ['-', '']:
        return None

    cleaned = str(date_str).replace('\xa0', ' ').replace('\u202f', ' ').strip()
    match = re.search(r'(\d{2}/\d{2}/\d{4})\s*(?:à|@)?\s*(\d{2}:\d{2}(?::\d{2})?)', cleaned)
    if match:
        date_part, time_part = match.group(1), match.group(2)
        if len(time_part.split(':')) == 2:
            time_part += ":00"
        try:
            dt = datetime.strptime(f"{date_part} {time_part}", "%d/%m/%Y %H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return cleaned
    return cleaned


# ---------------------------------------------------------------------------
# Client HTTP & Extraction HTML
# ---------------------------------------------------------------------------
def get_http_session() -> requests.Session:
    """Crée une session requests avec pool de connexions et retry exponentiel."""
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HTTP_HEADERS)
    return session


def fetch_page_content(url: str = TARGET_URL) -> str:
    """Télécharge le code HTML de la page cible."""
    session = get_http_session()
    logger.info(f"Connexion à l'URL source : {url}")
    try:
        response = session.get(url, timeout=25)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text
    except requests.exceptions.RequestException as e:
        logger.error(f"Échec de récupération de la page : {e}")
        raise


def extract_stock_data(html_content: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Parse le HTML, localise le tableau des variations et extrait les données de chaque action.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Stratégie 1 : Recherche du tableau par mots-clés d'en-tête (Cours, Valeur, Volume, Haut/Bas)
    target_table = None
    all_tables = soup.find_all("table")
    
    for table in all_tables:
        header_text = " ".join([th.get_text(strip=True).lower() for th in table.find_all(["th", "td"])])
        if ("cours" in header_text or "valeur" in header_text) and ("volume" in header_text or "haut" in header_text or "bas" in header_text):
            rows = table.find_all("tr")
            # Le marché officiel compte plus de 60 valeurs cotées
            if len(rows) >= 10:
                target_table = table
                break

    # Stratégie de repli (Fallback) : recherche des lignes à 8 cellules avec timestamp
    if not target_table:
        for table in all_tables:
            matching_rows = [
                r for r in table.find_all("tr")
                if len(r.find_all(["td"])) >= 7 and re.search(r'\d{2}/\d{2}/\d{4}', r.get_text())
            ]
            if len(matching_rows) >= 10:
                target_table = table
                break

    if not target_table:
        raise ValueError("Impossible de trouver le tableau des variations boursières dans le DOM HTML.")

    records: List[Dict[str, Any]] = []
    dict_by_ticker: Dict[str, Dict[str, Any]] = {}

    rows = target_table.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue

        raw_name = cells[0].get_text(separator=" ")
        stock_name = clean_stock_name(raw_name)
        
        # Ignorer les lignes d'en-têtes ou vides
        if not stock_name or stock_name.lower() in ["nom", "valeur", "titre", "secteur"]:
            continue

        ticker = generate_ticker_key(stock_name)
        cours_cloture = clean_number(cells[1].get_text())
        var_pct = clean_number(cells[2].get_text())
        var_abs = clean_number(cells[3].get_text()) if len(cells) >= 8 else None
        plus_haut = clean_number(cells[4].get_text()) if len(cells) >= 8 else clean_number(cells[3].get_text())
        plus_bas = clean_number(cells[5].get_text()) if len(cells) >= 8 else clean_number(cells[4].get_text())
        volume_titres = clean_number(cells[6].get_text()) if len(cells) >= 8 else clean_number(cells[5].get_text())
        date_heure = (
            parse_casablanca_datetime(cells[7].get_text()) if len(cells) >= 8
            else parse_casablanca_datetime(cells[6].get_text())
        )

        record = {
            "ticker": ticker,
            "valeur": stock_name,
            "cours_cloture_mad": cours_cloture,
            "variation_pct": var_pct,
            "variation_mad": var_abs,
            "plus_haut_mad": plus_haut,
            "plus_bas_mad": plus_bas,
            "volume_titres": int(volume_titres) if volume_titres is not None else None,
            "date_derniere_transaction": date_heure
        }

        records.append(record)
        dict_by_ticker[ticker] = record

    logger.info(f"{len(records)} valeurs extraites et nettoyées avec succès.")
    return records, dict_by_ticker


# ---------------------------------------------------------------------------
# Sauvegarde des Données (CSV & JSON)
# ---------------------------------------------------------------------------
def save_data(records: List[Dict[str, Any]], dict_by_ticker: Dict[str, Dict[str, Any]]) -> None:
    """Sauvegarde les données sous formats CSV et JSON horodatés et 'latest'."""
    os.makedirs(DATA_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1. Export CSV
    df = pd.DataFrame(records)
    csv_timestamped = os.path.join(DATA_DIR, f"cotations_casablanca_{today_str}.csv")
    csv_latest = os.path.join(DATA_DIR, "cotations_casablanca_latest.csv")

    df.to_csv(csv_timestamped, index=False, encoding="utf-8-sig")
    df.to_csv(csv_latest, index=False, encoding="utf-8-sig")
    logger.info(f"Fichiers CSV générés : '{csv_timestamped}' & '{csv_latest}'")

    # 2. Export JSON structuré avec métadonnées
    payload = {
        "metadata": {
            "source_url": TARGET_URL,
            "marche": "Bourse de Casablanca (BVC)",
            "date_extraction_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "total_actions": len(records)
        },
        "cotations_by_ticker": dict_by_ticker,
        "cotations_list": records
    }

    json_timestamped = os.path.join(DATA_DIR, f"cotations_casablanca_{today_str}.json")
    json_latest = os.path.join(DATA_DIR, "cotations_casablanca_latest.json")

    with open(json_timestamped, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    with open(json_latest, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"Fichiers JSON générés : '{json_timestamped}' & '{json_latest}'")


# ---------------------------------------------------------------------------
# Point d'Entrée Principal
# ---------------------------------------------------------------------------
def main():
    logger.info("=== Démarrage du scraping de la Bourse de Casablanca ===")
    try:
        html = fetch_page_content(TARGET_URL)
        records, dict_by_ticker = extract_stock_data(html)
        if not records:
            raise ValueError("Aucune donnée n'a pu être extraite. Vérifiez la structure de la page.")
        save_data(records, dict_by_ticker)
        logger.info("=== Ingestion terminée avec succès ===")
    except Exception as e:
        logger.critical(f"Erreur critique lors de l'ingestion : {e}", exc_info=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
