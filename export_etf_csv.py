#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


FRENCH_MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "fevr": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def deaccent(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_only


def parse_percent(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = clean_spaces(value).replace("%", "").replace("p.a.", "")
    text = text.replace("+", "")
    m = re.search(r"-?\d+(?:[\.,]\d+)?", text)
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def parse_aum_meur(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = clean_spaces(value)
    match = re.search(r"(\d[\d\s.,]*)\s*([A-Za-z]+)?", text)
    if not match:
        return None

    number_raw = match.group(1).replace(" ", "")
    number_raw = number_raw.replace(",", ".")
    try:
        number = float(number_raw)
    except ValueError:
        return None

    unit = (match.group(2) or "").lower()
    if unit in {"mrd", "bn", "b", "md"}:
        return number * 1000.0
    if unit in {"k", "mille"}:
        return number / 1000.0
    # M / Mio treated as million euros.
    return number


def extract_index_name(description: Optional[str]) -> Optional[str]:
    if not description:
        return None
    text = clean_spaces(description)
    m = re.search(r"reproduit l['’]index\s+([^\.]+)\.", text, flags=re.IGNORECASE)
    if m:
        return clean_spaces(m.group(1))
    return None


def extract_launch_date_from_description(description: Optional[str]) -> Optional[dt.date]:
    if not description:
        return None
    text = clean_spaces(description)
    m = re.search(r"lanc[ée]\s+le\s+(\d{1,2})\s+([A-Za-z\u00C0-\u017F]+)\s+(\d{4})", text, flags=re.IGNORECASE)
    if not m:
        return None

    day = int(m.group(1))
    month_token = deaccent(m.group(2).lower())
    month_token = month_token.replace(".", "")
    month_token = month_token.replace("fevrier", "fevrier")
    month = FRENCH_MONTHS.get(month_token)
    if not month:
        return None

    year = int(m.group(3))
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def infer_launch_date_from_heatmap(heatmap: Optional[Dict]) -> Optional[dt.date]:
    if not isinstance(heatmap, dict):
        return None
    values = heatmap.get("values")
    if not isinstance(values, list) or not values:
        return None

    points: List[Tuple[int, int]] = []
    for row in values:
        if not isinstance(row, dict):
            continue
        year_raw = row.get("year")
        month_idx = row.get("month_index")
        try:
            year = int(str(year_raw))
        except Exception:
            continue
        if not isinstance(month_idx, int):
            continue
        if month_idx < 1 or month_idx > 12:
            continue
        points.append((year, month_idx))

    if not points:
        return None

    year, month = sorted(points)[0]
    try:
        return dt.date(year, month, 1)
    except ValueError:
        return None


def compute_cagr_from_heatmap(heatmap: Optional[Dict]) -> Optional[float]:
    if not isinstance(heatmap, dict):
        return None
    values = heatmap.get("values")
    if not isinstance(values, list) or not values:
        return None

    returns: List[float] = []
    for row in values:
        if not isinstance(row, dict):
            continue
        ret = row.get("return_pct")
        if isinstance(ret, (int, float)):
            returns.append(float(ret))

    if not returns:
        return None

    compounded = 1.0
    for r in returns:
        compounded *= 1.0 + (r / 100.0)

    if compounded <= 0:
        return None

    months = len(returns)
    cagr = math.pow(compounded, 12.0 / months) - 1.0
    return cagr * 100.0


def compute_yearly_returns_from_heatmap(heatmap: Optional[Dict]) -> List[float]:
    if not isinstance(heatmap, dict):
        return []
    values = heatmap.get("values")
    if not isinstance(values, list):
        return []

    by_year: Dict[int, List[Tuple[int, float]]] = {}
    for row in values:
        if not isinstance(row, dict):
            continue
        try:
            year = int(str(row.get("year")))
        except Exception:
            continue
        month_idx = row.get("month_index")
        ret = row.get("return_pct")
        if not isinstance(month_idx, int) or not isinstance(ret, (int, float)):
            continue
        by_year.setdefault(year, []).append((month_idx, float(ret)))

    yearly_returns: List[float] = []
    for year in sorted(by_year):
        compounded = 1.0
        for _, ret in sorted(by_year[year], key=lambda x: x[0]):
            compounded *= 1.0 + (ret / 100.0)
        yearly_returns.append((compounded - 1.0) * 100.0)

    return yearly_returns


def classify_category(name: Optional[str], axis: Optional[str]) -> str:
    text = f"{name or ''} {axis or ''}".lower()
    if "core" in text:
        return "Core"
    if "hedg" in text:
        return "Hedge"
    return "Satellite"


def years_between(start_date: Optional[dt.date], end_date: dt.date) -> Optional[int]:
    if not start_date:
        return None
    days = (end_date - start_date).days
    if days < 0:
        return None
    return int(days / 365.2425)


def excel_col_name(index_one_based: int) -> str:
    result = ""
    n = index_one_based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def fmt_number(value: Optional[float], ndigits: int = 6) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return ""
    rounded = round(float(value), ndigits)
    text = f"{rounded:.{ndigits}f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def load_ticker_map(path: Optional[Path]) -> Dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        return {}

    mapping: Dict[str, str] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        isin = str(row.get("isin", "")).strip().upper()
        ticker = str(row.get("tickers", "")).strip().upper()
        if isin and ticker and isin not in mapping:
            mapping[isin] = ticker
    return mapping


def load_etf_json_files(input_dir: Path) -> List[Path]:
    files = sorted(p for p in input_dir.glob("*.json") if p.name.lower() != "errors.json")
    return files


def build_overview_rows(files: Iterable[Path], ticker_map: Dict[str, str]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    today = dt.date.today()

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            continue

        isin = str(data.get("isin", "")).strip().upper()
        if not isin:
            continue

        details = data.get("donnees") if isinstance(data.get("donnees"), dict) else {}
        description = data.get("description")
        name = data.get("nom")
        heatmap = data.get("heatmap_mensuelle")

        axis = details.get("axe_investissement") if isinstance(details, dict) else None
        ter = parse_percent(details.get("frais_totaux_sur_encours_ter") if isinstance(details, dict) else None)
        volatility = parse_percent(details.get("volatilite_sur_1_an") if isinstance(details, dict) else None)
        aum_meur = parse_aum_meur(details.get("taille_du_fonds") if isinstance(details, dict) else None)

        cagr = data.get("cagr_depuis_creation_pct")
        if not isinstance(cagr, (int, float)):
            cagr = compute_cagr_from_heatmap(heatmap)
        cagr = float(cagr) if isinstance(cagr, (int, float)) else None

        launch_date = extract_launch_date_from_description(description if isinstance(description, str) else None)
        if launch_date is None:
            launch_date = infer_launch_date_from_heatmap(heatmap)

        age_years = years_between(launch_date, today)

        yearly_returns = compute_yearly_returns_from_heatmap(heatmap)
        neg_years = sum(1 for r in yearly_returns if r < 0)
        worst_year = min(yearly_returns) if yearly_returns else None

        row = {
            "ISIN": isin,
            "Nom ETF": name or "",
            "Ticker": ticker_map.get(isin, ""),
            "Indice suivi": extract_index_name(description if isinstance(description, str) else None) or "",
            "Axe investissement": axis or "",
            "Categorie": classify_category(name if isinstance(name, str) else None, axis if isinstance(axis, str) else None),
            "Devise fonds": (details.get("monnaie_du_fonds") if isinstance(details, dict) else "") or "",
            "TER (%)": ter,
            "Replication": (details.get("methode_de_replication") if isinstance(details, dict) else "") or "",
            "Distribution": (details.get("distribution") if isinstance(details, dict) else "") or "",
            "Domicile": (details.get("domicile_du_fonds") if isinstance(details, dict) else "") or "",
            "Promoteur": (details.get("promoteur") if isinstance(details, dict) else "") or "",
            "AUM (MEUR)": aum_meur,
            "Date lancement": launch_date.strftime("%d/%m/%Y") if launch_date else "",
            "Age du fonds": age_years,
            "Volatilite 1 an (%)": volatility,
            "CAGR (%)": cagr,
            "Nb annees negatives": neg_years if yearly_returns else "",
            "Pire annee (%)": worst_year,
        }
        rows.append(row)

    return rows


def write_overview_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    headers = [
        "ISIN",
        "Nom ETF",
        "Ticker",
        "Indice suivi",
        "Axe investissement",
        "Categorie",
        "Devise fonds",
        "TER (%)",
        "Replication",
        "Distribution",
        "Domicile",
        "Promoteur",
        "AUM (MEUR)",
        "Date lancement",
        "Age du fonds",
        "Volatilite 1 an (%)",
        "CAGR (%)",
        "Nb annees negatives",
        "Pire annee (%)",
    ]

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(headers)
        for row in rows:
            writer.writerow(
                [
                    row.get("ISIN", ""),
                    row.get("Nom ETF", ""),
                    row.get("Ticker", ""),
                    row.get("Indice suivi", ""),
                    row.get("Axe investissement", ""),
                    row.get("Categorie", ""),
                    row.get("Devise fonds", ""),
                    fmt_number(row.get("TER (%)"), 4),
                    row.get("Replication", ""),
                    row.get("Distribution", ""),
                    row.get("Domicile", ""),
                    row.get("Promoteur", ""),
                    fmt_number(row.get("AUM (MEUR)"), 3),
                    row.get("Date lancement", ""),
                    row.get("Age du fonds", ""),
                    fmt_number(row.get("Volatilite 1 an (%)"), 4),
                    fmt_number(row.get("CAGR (%)"), 6),
                    row.get("Nb annees negatives", ""),
                    fmt_number(row.get("Pire annee (%)"), 4),
                ]
            )


def write_projection_csv(
    path: Path,
    rows: List[Dict[str, object]],
    years: int,
    capital_initial: float,
    inflation: float,
    dca_mensuel: float,
) -> None:
    etfs = rows
    if not etfs:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["Aucun ETF exploitable"]) 
        return

    matrix: List[List[str]] = []

    col_count = 1 + len(etfs)

    def blank_row() -> List[str]:
        return [""] * col_count

    def ensure_row(idx: int) -> None:
        while len(matrix) <= idx:
            matrix.append(blank_row())

    def set_cell(r: int, c: int, value: str) -> None:
        ensure_row(r)
        matrix[r][c] = value

    # Parameters block
    set_cell(0, 0, "Parametre")
    set_cell(0, 1, "Valeur")
    set_cell(1, 0, "Capital initial")
    set_cell(1, 1, fmt_number(capital_initial, 2))
    set_cell(2, 0, "Inflation annuelle")
    set_cell(2, 1, fmt_number(inflation, 6))
    set_cell(3, 0, "DCA mensuel")
    set_cell(3, 1, fmt_number(dca_mensuel, 2))
    set_cell(4, 0, "Horizon (annees)")
    set_cell(4, 1, str(years))

    set_cell(6, 0, "ETF")
    set_cell(7, 0, "ISIN")
    set_cell(8, 0, "CAGR brut annuel")
    set_cell(9, 0, "TER annuel")
    set_cell(10, 0, "Rendement net reel annuel")

    for idx, row in enumerate(etfs, start=2):
        col = excel_col_name(idx)
        etf_name = str(row.get("Nom ETF", ""))
        isin = str(row.get("ISIN", ""))
        cagr_pct = row.get("CAGR (%)") if isinstance(row.get("CAGR (%)"), (int, float)) else 0.0
        ter_pct = row.get("TER (%)") if isinstance(row.get("TER (%)"), (int, float)) else 0.0

        set_cell(6, idx - 1, etf_name)
        set_cell(7, idx - 1, isin)
        set_cell(8, idx - 1, f"={fmt_number(float(cagr_pct) / 100.0, 10)}")
        set_cell(9, idx - 1, f"={fmt_number(float(ter_pct) / 100.0, 10)}")
        set_cell(10, idx - 1, f"=(1+{col}9)*(1-{col}10)/(1+$B$3)-1")

    projection_header = 12
    set_cell(projection_header, 0, "Annee")
    for idx, row in enumerate(etfs, start=2):
        isin = str(row.get("ISIN", ""))
        set_cell(projection_header, idx - 1, isin)

    start_data_row = projection_header + 1
    for y in range(0, years + 1):
        r = start_data_row + y
        set_cell(r, 0, str(y))
        for idx in range(2, 2 + len(etfs)):
            col = excel_col_name(idx)
            if y == 0:
                set_cell(r, idx - 1, "=$B$2")
            else:
                prev_r = r - 1
                set_cell(r, idx - 1, f"={col}{prev_r + 1}*(1+{col}$11)+$B$4*12")

    net_final_row = start_data_row + years

    no_cost_header = net_final_row + 3
    set_cell(no_cost_header, 0, "Annee (sans frais)")
    for idx, row in enumerate(etfs, start=2):
        isin = str(row.get("ISIN", ""))
        set_cell(no_cost_header, idx - 1, isin)

    no_cost_start = no_cost_header + 1
    for y in range(0, years + 1):
        r = no_cost_start + y
        set_cell(r, 0, str(y))
        for idx in range(2, 2 + len(etfs)):
            col = excel_col_name(idx)
            if y == 0:
                set_cell(r, idx - 1, "=$B$2")
            else:
                prev_r = r - 1
                set_cell(r, idx - 1, f"={col}{prev_r + 1}*(1+{col}$9)+$B$4*12")

    no_cost_final_row = no_cost_start + years

    summary_header = no_cost_final_row + 3
    set_cell(summary_header, 0, "ISIN")
    set_cell(summary_header, 1, "Nom ETF")
    set_cell(summary_header, 2, "Capital final net reel")
    set_cell(summary_header, 3, "Capital final sans frais")
    set_cell(summary_header, 4, "Manque a gagner")
    set_cell(summary_header, 5, "Manque a gagner %")

    for i, row in enumerate(etfs, start=1):
        r = summary_header + i
        col = excel_col_name(i + 1)
        set_cell(r, 0, str(row.get("ISIN", "")))
        set_cell(r, 1, str(row.get("Nom ETF", "")))
        set_cell(r, 2, f"={col}{net_final_row + 1}")
        set_cell(r, 3, f"={col}{no_cost_final_row + 1}")
        set_cell(r, 4, f"=D{r + 1}-C{r + 1}")
        set_cell(r, 5, f"=E{r + 1}/D{r + 1}")

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        for row in matrix:
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genere 2 CSV Excel a partir des JSON ETF produits par scrape_justetf.py"
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="output",
        help="Dossier contenant les JSON ETF (defaut: output)",
    )
    parser.add_argument(
        "--overview-csv",
        type=Path,
        default=Path("etf_overview.csv"),
        help="Fichier CSV de synthese (defaut: etf_overview.csv)",
    )
    parser.add_argument(
        "--projection-csv",
        type=Path,
        default=Path("etf_projection.csv"),
        help="Fichier CSV de projection (defaut: etf_projection.csv)",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=30,
        help="Horizon de projection en annees (defaut: 30)",
    )
    parser.add_argument(
        "--capital-initial",
        type=float,
        default=10000.0,
        help="Capital initial utilise dans le CSV de projection (defaut: 10000)",
    )
    parser.add_argument(
        "--inflation",
        type=float,
        default=0.02,
        help="Inflation annuelle (defaut: 0.02, soit 2%%)",
    )
    parser.add_argument(
        "--dca-mensuel",
        type=float,
        default=0.0,
        help="Apport mensuel DCA (defaut: 0)",
    )
    parser.add_argument(
        "--ticker-map",
        type=Path,
        default=None,
        help="JSON optionnel de mapping ticker/isin (ex: ticker_isin_discovery.json)",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Dossier introuvable: {input_dir}")

    files = load_etf_json_files(input_dir)
    if not files:
        raise SystemExit(f"Aucun fichier JSON ETF trouve dans: {input_dir}")

    ticker_map = load_ticker_map(args.ticker_map)
    rows = build_overview_rows(files, ticker_map)

    write_overview_csv(args.overview_csv, rows)
    write_projection_csv(
        path=args.projection_csv,
        rows=rows,
        years=max(1, int(args.years)),
        capital_initial=float(args.capital_initial),
        inflation=float(args.inflation),
        dca_mensuel=float(args.dca_mensuel),
    )

    print(f"CSV synthese ecrit: {args.overview_csv}")
    print(f"CSV projection ecrit: {args.projection_csv}")
    print(f"ETF traites: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
