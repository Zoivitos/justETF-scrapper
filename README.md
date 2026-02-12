# justETFScrapper

Script Python pour scraper des pages ETF sur `justETF` a partir d'une liste d'ISIN, puis exporter un fichier JSON par ETF.

## Ce que fait le script

Le script `scrape_justetf.py` :

1. Lit un fichier JSON contenant une liste d'ISIN (strings).
2. Pour chaque ISIN, ouvre l'URL :
   - `https://www.justetf.com/fr/etf-profile.html?isin=<ISIN>`
3. Extrait les informations suivantes :
   - Nom
   - Description
   - Axe d'investissement
   - Taille du fonds
   - Frais totaux sur encours (TER)
   - Methode de replication
   - Risque de la strategie
   - Monnaie du fonds
   - Volatilite sur 1 an
   - Distribution
   - Domicile du fonds
   - Promoteur
4. Ecrit un fichier JSON par ISIN dans le dossier de sortie.
5. En cas d'erreurs, ecrit aussi un fichier `errors.json`.

## Prerequis

- Python 3.10+ (ou version equivalente recente)
- Acces reseau vers `www.justetf.com`

Le script utilise uniquement la librairie standard Python (pas de dependances externes).

## Format du fichier d'entree

Le fichier d'entree doit etre un JSON de type liste de strings :

```json
[
  "IE000OJ5TQP4",
  "IE000M7V94E1"
]
```

Exemple fourni : `isins.json`.

## Utilisation

Commande de base :

```powershell
.\.venv\Scripts\python.exe .\scrape_justetf.py .\isins.json
```

Exemple avec options :

```powershell
.\.venv\Scripts\python.exe .\scrape_justetf.py .\isins.json --output-dir .\resultats --delay 2.0 --timeout 45
```

## Second script : decouverte ISIN depuis tickers

Le script `discover_isins_from_tickers.py` permet de partir d'une liste de tickers (ex: `NATO`, `SGLD`) et de decouvrir tous les ETF trouves dans la recherche justETF.

Sortie : un seul fichier JSON avec une liste d'objets contenant :

- `tickers`
- `isin`
- `nom_complet`

Note technique : le script utilise le endpoint de "recherche rapide" charge par la page justETF. Selon justETF, ce flux peut parfois limiter le nombre de resultats renvoyes pour une requete tres large.

### Fichier d'entree (tickers)

```json
[
  "NATO",
  "SGLD"
]
```

### Commande de base

```powershell
.\.venv\Scripts\python.exe .\discover_isins_from_tickers.py .\tickers.json
```

### Exemple avec options

```powershell
.\.venv\Scripts\python.exe .\discover_isins_from_tickers.py .\tickers.json --output .\ticker_isin_discovery.json --delay 2.0 --page-size 100 --max-pages 30
```

## Options disponibles

### Argument positionnel

- `isins_json`
  - Chemin du fichier JSON contenant la liste d'ISIN.
  - Obligatoire.

### Options

- `--output-dir OUTPUT_DIR`
  - Dossier ou seront ecrits les fichiers `<ISIN>.json`.
  - Valeur par defaut : `output`.

- `--delay DELAY`
  - Temps d'attente (en secondes) entre deux requetes HTTP.
  - Valeur par defaut : `0.8`.
  - Utile pour reduire le risque de blocage/rate-limit.

- `--timeout TIMEOUT`
  - Timeout HTTP en secondes pour chaque requete.
  - Valeur par defaut : `30`.

- `-h` / `--help`
  - Affiche l'aide complete de la CLI.

### Options du script `discover_isins_from_tickers.py`

- `tickers_json`
  - Fichier JSON d'entree avec la liste des tickers.
  - Obligatoire.

- `--output OUTPUT`
  - Fichier JSON de sortie des resultats.
  - Defaut : `ticker_isin_discovery.json`.

- `--delay DELAY`
  - Pause en secondes entre chaque ticker.
  - Defaut : `1.0`.

- `--timeout TIMEOUT`
  - Timeout HTTP en secondes.
  - Defaut : `30`.

- `--page-size PAGE_SIZE`
  - Nombre de lignes demandees par page API.
  - Defaut : `50`.

- `--max-pages MAX_PAGES`
  - Nombre maximum de pages chargees par ticker.
  - Defaut : `20`.

- `--errors-output ERRORS_OUTPUT`
  - Fichier JSON contenant les erreurs par ticker.
  - Defaut : `ticker_isin_discovery_errors.json`.

## Structure des fichiers de sortie

Pour chaque ISIN reussi, un fichier `output/<ISIN>.json` est cree avec cette structure :

```json
{
  "isin": "IE000OJ5TQP4",
  "nom": "HANetf Future of Defence UCITS ETF",
  "description": "...",
  "donnees": {
    "axe_investissement": "...",
    "taille_du_fonds": "...",
    "frais_totaux_sur_encours_ter": "...",
    "methode_de_replication": "...",
    "risque_de_la_strategie": "...",
    "monnaie_du_fonds": "...",
    "volatilite_sur_1_an": "...",
    "distribution": "...",
    "domicile_du_fonds": "...",
    "promoteur": "..."
  }
}
```

Si certains ISIN echouent, un fichier `output/errors.json` est cree :

```json
{
  "ISIN_EN_ERREUR": "message d'erreur"
}
```

Pour le script ticker, la sortie ressemble a :

```json
[
  {
    "tickers": "NATO",
    "isin": "IE000OJ5TQP4",
    "nom_complet": "HANetf Future of Defence UCITS ETF"
  }
]
```

## Codes de retour du script

- `0` : execution terminee sans erreur
- `1` : erreur de lecture/format du fichier d'entree
- `2` : execution terminee avec au moins une erreur ISIN

## Conseils anti-blocage

- Augmenter `--delay` (ex: `1.5` a `3.0` secondes)
- Eviter de lancer plusieurs instances du script en parallele
- Garder un volume raisonnable de requetes
