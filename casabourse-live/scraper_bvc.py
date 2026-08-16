import requests
from bs4 import BeautifulSoup
import json

def fetch_bvc_data():
    url = "https://www.casablanca-bourse.com/"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    data = {"status": "Ouvert", "items": []}
    # Logique d'extraction des cotations BVC
    return data