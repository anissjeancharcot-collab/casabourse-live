#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper Bourse de Casablanca avec Playwright (Bypass Cloudflare Turnstile / 403)
Génère le fichier cotations.json pour le dashboard GitHub Pages.
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from playwright.sync_api import sync_playwright
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
logger = logging.getLogger("BVC_Playwright_Scraper")

TARGET_URL = "https://medias24.com/leboursier/marches-boursier"
OUTPUT_JSON_ROOT = "cotations.json"
DATA_DIR = "data"


# ---------------------------------------------------------------------------
# Nettoyage des données numériques
# ---------------------------------------------------------------------------
def clean_number(value_str: Optional[str]) -> Optional[float]:
    """Convertit une chaîne numérique en float."""
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
    """Nettoie le nom de l'action."""
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
    if val_float is None:
        return "0.00%"
    prefix = "+" if val_float > 0 else ""
    return f"{prefix}{val_float:.2f}%"


def format_variation_abs(val_float: Optional[float]) -> str:
    if val_float is None:
        return "0.00"
    prefix = "+" if val_float > 0 else ""
    return f"{prefix}{val_float:.2f}"


# ---------------------------------------------------------------------------
# Récupération de la page via Playwright (Chromium)
# ---------------------------------------------------------------------------
def fetch_page_with_playwright(url: str = TARGET_URL) -> str:
    """Lance Chromium headless pour contourner Cloudflare et récupérer le HTML."""
    logger.info("Démarrage du navigateur Chromium via Playwright...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        
        # Contexte avec profil de navigateur standard
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="fr-FR",
            timezone_id="Africa/Casablanca"
        )
        
        page = context.new_page()
        
        # Masquer les flags d'automatisation
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        logger.info(f"Navigation vers {url}...")
        # Attente jusqu'à ce que le réseau soit calme
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        # Attente explicite du rendu du tableau
        logger.info("Attente du chargement du tableau boursier...")
        page.wait_for_selector("table", timeout=30000)
        
        # Pause de 3 secondes pour laisser le temps aux scripts dynamiques de s'exécuter
        page.wait_for_timeout(3000)
        
        content = page.content()
        browser.close()
        logger.info("Page HTML extraite avec succès.")
        return content


# ---------------------------------------------------------------------------
# Extraction & Parsing
# ---------------------------------------------------------------------------
def parse_stock_table(html_content: str) -> Tuple[str, List[Dict[str, Any]]]:
    soup = BeautifulSoup(html_content, "html.parser")

    target_table = None
    for table in soup.find_all("table"):
        headers = " ".join([th.get_text(strip=True).lower() for th in table.find_all(["th", "td"])])
        if ("cours" in headers or "valeur" in headers) and ("volume" in headers or "haut" in headers or "bas" in headers):
            if len(table.find_all("tr")) >= 10:
                target_table = table
                break

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
        raise ValueError("Impossible de localiser le tableau des cotations dans le DOM.")

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
    logger.info(f"{len(valeurs)} actions extraites avec succès pour la date : {date_maj}")
    return date_maj, valeurs


# ---------------------------------------------------------------------------
# Sauvegarde
# ---------------------------------------------------------------------------
def save_results(date_maj: str, valeurs: List[Dict[str, Any]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    dashboard_payload = {
        "date_maj": date_maj,
        "valeurs": valeurs
    }

    # 1. Sauvegarde pour index.html
    with open(OUTPUT_JSON_ROOT, "w", encoding="utf-8") as f:
        json.dump(dashboard_payload, f, ensure_ascii=False, indent=2)
    logger.info(f"Fichier généré : '{OUTPUT_JSON_ROOT}'")

    # 2. Archives dans data/
    with open(os.path.join(DATA_DIR, f"cotations_{today_str}.json"), "w", encoding="utf-8") as f:
        json.dump(dashboard_payload, f, ensure_ascii=False, indent=2)

    df = pd.DataFrame(valeurs)
    df.to_csv(os.path.join(DATA_DIR, f"cotations_{today_str}.csv"), index=False, encoding="utf-8-sig")
    df.to_csv(os.path.join(DATA_DIR, "cotations_latest.csv"), index=False, encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def main():
    try:
        html = fetch_page_with_playwright()
        date_maj, valeurs = parse_stock_table(html)
        if not valeurs:
            raise ValueError("Aucune donnée boursière trouvée.")
        save_results(date_maj, valeurs)
        logger.info("=== Extraction terminée avec succès ===")
    except Exception as e:
        logger.critical(f"Erreur : {e}", exc_info=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
