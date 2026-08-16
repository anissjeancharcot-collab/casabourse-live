#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper Bourse de Casablanca — Contournement Cloudflare / 403 WAF
Génère le fichier cotations.json pour le dashboard GitHub Pages.
"""

import os
import re
import json
import time
import random
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

import cloudscraper
from bs4 import BeautifulSoup
import pandas as pd

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
# Configuration & URLs
# ---------------------------------------------------------------------------
BASE_URL = "https://medias24.com"
TARGET_URL = "https://medias24.com/leboursier/marches-boursier"
OUTPUT_JSON_ROOT = "cotations.json"  # Lu directement par index.html
DATA_DIR = "data"

# En-têtes complets simulant fidèlement un navigateur Chrome réel
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://medias24.com/",
    "Sec-Ch-Ua": '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}


# ---------------------------------------------------------------------------
# Fonctions de Nettoyage & Conversion
# ---------------------------------------------------------------------------
def clean_number(value_str: Optional[str]) -> Optional[float]:
    """Convertit les nombres avec virgules et espaces en float."""
    if not value_str:
        return None
    val = str(value_str).strip()
    if val in ['-', '--', 'N/A', 'nan', '', '0 0']:
        return None

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
        return None


def clean_stock_name(raw_name: str) -> str:
    """Retire les libellés de sous-menus du DOM."""
    unwanted = [
        "Fiche valeur",
        "Transactions de la journée une par une",
        "Transactions de la journée",
        "Carnet d'ordres",
        "une par une"
    ]
    name = raw_name
    for u in unwanted:
        name = name.replace(u, "")
    return re.sub(r'\s+', ' ', name).strip()


def format_variation_pct(val_float: Optional[float], raw_str: str) -> str:
    """Garantit le format '+0.46%' ou '-2.70%'."""
    if val_float is None:
        return "0.00%"
    prefix = "+" if val_float > 0 else ""
    return f"{prefix}{val_float:.2f}%"


def format_variation_abs(val_float: Optional[float]) -> str:
    """Garantit le format '+0.45' ou '-49.95'."""
    if val_float is None:
        return "0.00"
    prefix = "+" if val_float > 0 else ""
    return f"{prefix}{val_float:.2f}"


# ---------------------------------------------------------------------------
# Ingestion avec Contournement Cloudflare (cloudscraper)
# ---------------------------------------------------------------------------
def fetch_html_with_cloudscraper() -> str:
    """
    Simule une session utilisateur humaine avec cloudscraper :
    1. Navigation sur la page d'accueil pour obtenir les cookies de session WAF.
    2. Délai d'attente aléatoire (2 à 4 secondes).
    3. Requête vers la page des marchés boursiers.
    """
    logger.info("Initialisation du scraper anti-Cloudflare...")
    
    # Création du scraper émulant un navigateur Chrome sous Windows
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        },
        delay=random.uniform(2.0, 4.0)
    )
    scraper.headers.update(BROWSER_HEADERS)

    try:
        # Étape 1 : Visite de la racine pour générer les cookies
        logger.info(f"Étape 1/2 : Visite de pré-chauffage ({BASE_URL})...")
        home_res = scraper.get(BASE_URL, timeout=30)
        logger.info(f"Statut page d'accueil : {home_res.status_code}")

        # Simulation d'un comportement humain
        sleep_time = random.uniform(2.5, 4.5)
        logger.info(f"Délai d'attente humain : {sleep_time:.2f}s...")
        time.sleep(sleep_time)

        # Étape 2 : Récupération de la page cible
        logger.info(f"Étape 2/2 : Téléchargement des cotations ({TARGET_URL})...")
        target_res = scraper.get(TARGET_URL, timeout=30)
        target_res.raise_for_status()

        logger.info("Page récupérée avec succès (HTTP 200).")
        return target_res.text

    except Exception as e:
        logger.error(f"Erreur lors du scraping cloudscraper : {e}")
        raise


# ---------------------------------------------------------------------------
# Extraction des Données du Tableau
# ---------------------------------------------------------------------------
def parse_stock_table(html_content: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Parse le tableau et extrait les valeurs boursières."""
    soup = BeautifulSoup(html_content, "html.parser")

    # Localisation dynamique du tableau des variations
    target_table = None
    for table in soup.find_all("table"):
        headers = " ".join([th.get_text(strip=True).lower() for th in table.find_all(["th", "td"])])
        if ("cours" in headers or "valeur" in headers) and ("volume" in headers or "haut" in headers or "bas" in headers):
            if len(table.find_all("tr")) >= 10:
                target_table = table
                break

    # Fallback si les en-têtes sont absents
    if not target_table:
        for table in soup.find_all("table"):
            rows_with_date = [
                r for r in table.find_all("tr")
                if len(r.find_all("td")) >= 7 and re.search(r'\d{2}/\d{2}/\d{4}', r.get_text())
            ]
            if len(rows_with_date) >= 10:
                target_table = table
                break

    if not target_table:
        raise ValueError("Impossible de localiser le tableau des variations dans le code HTML.")

    valeurs = []
    derniere_date_seance = None

    for row in target_table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 7:
            continue

        raw_name = cells[0].get_text(separator=" ")
        stock_name = clean_stock_name(raw_name)
        if not stock_name or stock_name.lower() in ["nom", "valeur", "titre", "secteur"]:
            continue

        raw_cours = clean_number(cells[1].get_text())
        raw_var_pct = clean_number(cells[2].get_text())
        raw_var_abs = clean_number(cells[3].get_text()) if len(cells) >= 8 else None
        plus_haut = clean_number(cells[4].get_text()) if len(cells) >= 8 else clean_number(cells[3].get_text())
        plus_bas = clean_number(cells[5].get_text()) if len(cells) >= 8 else clean_number(cells[4].get_text())
        volume = clean_number(cells[6].get_text()) if len(cells) >= 8 else clean_number(cells[5].get_text())
        date_str = cells[7].get_text().strip() if len(cells) >= 8 else cells[6].get_text().strip()

        if not derniere_date_seance and date_str and date_str != '-':
            derniere_date_seance = date_str

        # Format conforme aux attentes du front-end
        valeur_obj = {
            "nom": stock_name,
            "cours": round(raw_cours, 2) if raw_cours is not None else 0.0,
            "variation_pct": format_variation_pct(raw_var_pct, cells[2].get_text()),
            "variation_abs": format_variation_abs(raw_var_abs),
            "plus_haut": round(plus_haut, 2) if plus_haut is not None else round(raw_cours, 2) if raw_cours else 0.0,
            "plus_bas": round(plus_bas, 2) if plus_bas is not None else round(raw_cours, 2) if raw_cours else 0.0,
            "volume": int(volume) if volume is not None else 0
        }
        valeurs.append(valeur_obj)

    date_maj = derniere_date_seance or datetime.now().strftime("%d/%m/%Y à %H:%M")
    logger.info(f"{len(valeurs)} actions extraites avec succès pour la séance : {date_maj}")
    return date_maj, valeurs


# ---------------------------------------------------------------------------
# Sauvegarde des Résultats
# ---------------------------------------------------------------------------
def save_results(date_maj: str, valeurs: List[Dict[str, Any]]) -> None:
    """Enregistre cotations.json à la racine et archive dans data/."""
    os.makedirs(DATA_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1. Structure JSON stricte pour le Dashboard
    dashboard_payload = {
        "date_maj": date_maj,
        "valeurs": valeurs
    }

    # Sauvegarde à la racine pour index.html
    with open(OUTPUT_JSON_ROOT, "w", encoding="utf-8") as f:
        json.dump(dashboard_payload, f, ensure_ascii=False, indent=2)
    logger.info(f"Fichier principal généré : '{OUTPUT_JSON_ROOT}'")

    # 2. Sauvegarde horodatée dans data/
    json_archive = os.path.join(DATA_DIR, f"cotations_{today_str}.json")
    with open(json_archive, "w", encoding="utf-8") as f:
        json.dump(dashboard_payload, f, ensure_ascii=False, indent=2)

    # 3. Export CSV dans data/
    df = pd.DataFrame(valeurs)
    csv_archive = os.path.join(DATA_DIR, f"cotations_{today_str}.csv")
    csv_latest = os.path.join(DATA_DIR, "cotations_latest.csv")
    df.to_csv(csv_archive, index=False, encoding="utf-8-sig")
    df.to_csv(csv_latest, index=False, encoding="utf-8-sig")
    logger.info(f"Fichiers CSV générés dans '{DATA_DIR}/'")


# ---------------------------------------------------------------------------
# Point d'Entrée
# ---------------------------------------------------------------------------
def main():
    try:
        html = fetch_html_with_cloudscraper()
        date_maj, valeurs = parse_stock_table(html)
        if not valeurs:
            raise ValueError("Aucune donnée boursière trouvée dans le tableau.")
        save_results(date_maj, valeurs)
        logger.info("=== Pipeline terminé avec succès ===")
    except Exception as e:
        logger.critical(f"Échec de l'ingestion : {e}", exc_info=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
