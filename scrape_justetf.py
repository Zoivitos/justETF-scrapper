#!/usr/bin/env python3
import argparse
import datetime as dt
import html
import http.cookiejar
import json
import math
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE_URL = "https://www.justetf.com/fr/etf-profile.html?isin={isin}"
MONTH_INDEX = {
    "jan": 1,
    "janv": 1,
    "janvier": 1,
    "feb": 2,
    "fev": 2,
    "fevr": 2,
    "fevrier": 2,
    "fvrier": 2,
    "mar": 3,
    "mars": 3,
    "avr": 4,
    "avril": 4,
    "mai": 5,
    "jun": 6,
    "juin": 6,
    "jul": 7,
    "juil": 7,
    "juillet": 7,
    "aou": 8,
    "aout": 8,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "septembre": 9,
    "oct": 10,
    "octobre": 10,
    "nov": 11,
    "novembre": 11,
    "dec": 12,
    "decembre": 12,
    "dcembre": 12,
}

TARGET_TESTIDS = {
    "name": "etf-profile-header_etf-name",
    "description_block": "etf-quote-section_description-content-inner",
    "investment_focus": "tl_etf-basics_value_investment-focus",
    "fund_size_row": "etf-basics_row_fund-size",
    "ter": "tl_etf-basics_value_ter",
    "replication": "tl_etf-basics_value_replication",
    "replication_method": "tl_etf-basics_value_replication-method",
    "strategy_risk": "tl_etf-basics_value_strategy-risk",
    "fund_currency": "tl_etf-basics_value_fund-currency",
    "volatility_1y": "tl_etf-basics_value_volatility",
    "distribution": "tl_etf-basics_value_distribution-policy",
    "fund_domicile": "tl_etf-basics_value_domicile-country",
    "provider": "tl_etf-basics_value_fund-provider",
    "max_return": "etf-returns-section_max-return",
    "launch_date": "tl_etf-basics_value_launch-date",
}


def clean_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_percent_text(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = value.replace("\xa0", "").replace(" ", "")
    text = text.replace("%", "").replace("+", "").replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0))


def parse_french_date(value: Optional[str]) -> Optional[dt.date]:
    if not value:
        return None
    text = clean_text(value).lower()
    match = re.search(r"(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})", text)
    if not match:
        return None
    day = int(match.group(1))
    month_token = (
        match.group(2)
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("û", "u")
        .replace("ù", "u")
        .replace("ô", "o")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("à", "a")
        .replace("ç", "c")
    )
    year = int(match.group(3))
    month = MONTH_INDEX.get(month_token)
    if not month:
        return None
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


class JustETFClient:
    def __init__(self) -> None:
        jar = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(jar))

    def fetch_text(
        self,
        url: str,
        timeout: int,
        method: str = "GET",
        data: Optional[bytes] = None,
        referer: Optional[str] = None,
        accept: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
        }
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)

        req = Request(url, data=data, method=method, headers=headers)
        with self.opener.open(req, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")


class DataTestIdTextParser(HTMLParser):
    def __init__(self, target_ids: set[str]):
        super().__init__(convert_charrefs=True)
        self.target_ids = target_ids
        self.active: List[dict] = []
        self.results: Dict[str, List[str]] = {tid: [] for tid in target_ids}

    def handle_starttag(self, tag, attrs):
        for capture in self.active:
            capture["depth"] += 1

        attrs_dict = dict(attrs)
        testid = attrs_dict.get("data-testid")
        if testid in self.target_ids:
            self.active.append({"id": testid, "depth": 1, "buf": []})

    def handle_endtag(self, tag):
        to_remove = []
        for idx, capture in enumerate(self.active):
            capture["depth"] -= 1
            if capture["depth"] == 0:
                text = clean_text("".join(capture["buf"]))
                if text:
                    self.results[capture["id"]].append(text)
                to_remove.append(idx)
        for idx in reversed(to_remove):
            self.active.pop(idx)

    def handle_data(self, data):
        if not data:
            return
        for capture in self.active:
            capture["buf"].append(data)


def extract_first(results: Dict[str, List[str]], testid: str) -> Optional[str]:
    values = results.get(testid) or []
    return values[0] if values else None


def parse_profile_html(html_text: str, isin: str) -> Dict[str, object]:
    parser = DataTestIdTextParser(set(TARGET_TESTIDS.values()))
    parser.feed(html_text)
    results = parser.results

    fund_size_row = extract_first(results, TARGET_TESTIDS["fund_size_row"])
    fund_size = None
    if fund_size_row:
        fund_size = re.sub(r"^Taille du fonds\s*", "", fund_size_row, flags=re.IGNORECASE).strip()

    replication = extract_first(results, TARGET_TESTIDS["replication"])
    replication_method = extract_first(results, TARGET_TESTIDS["replication_method"])
    replication_full = None
    if replication and replication_method:
        replication_full = f"{replication} ({replication_method})"
    elif replication:
        replication_full = replication

    parsed = {
        "isin": isin,
        "nom": extract_first(results, TARGET_TESTIDS["name"]),
        "description": extract_first(results, TARGET_TESTIDS["description_block"]),
        "donnees": {
            "axe_investissement": extract_first(results, TARGET_TESTIDS["investment_focus"]),
            "taille_du_fonds": fund_size,
            "frais_totaux_sur_encours_ter": extract_first(results, TARGET_TESTIDS["ter"]),
            "methode_de_replication": replication_full,
            "risque_de_la_strategie": extract_first(results, TARGET_TESTIDS["strategy_risk"]),
            "monnaie_du_fonds": extract_first(results, TARGET_TESTIDS["fund_currency"]),
            "volatilite_sur_1_an": extract_first(results, TARGET_TESTIDS["volatility_1y"]),
            "distribution": extract_first(results, TARGET_TESTIDS["distribution"]),
            "domicile_du_fonds": extract_first(results, TARGET_TESTIDS["fund_domicile"]),
            "promoteur": extract_first(results, TARGET_TESTIDS["provider"]),
        },
        "cagr_depuis_creation_pct": None,
        "cagr_depuis_creation_source": None,
        "heatmap_mensuelle": None,
        "_meta_returns": {
            "max_return_text": extract_first(results, TARGET_TESTIDS["max_return"]),
            "launch_date_text": extract_first(results, TARGET_TESTIDS["launch_date"]),
        },
    }
    return parsed


def extract_wicket_ajax_url(page_html: str, keyword: str) -> Optional[str]:
    match = re.search(r'Wicket\.Ajax\.ajax\(\{"u":"([^"]*' + re.escape(keyword) + r'[^"]*)"', page_html)
    if not match:
        return None
    raw = html.unescape(match.group(1))
    raw = raw.replace(r"\/", "/")
    return "https://www.justetf.com" + raw


def extract_timer_ajax_url(page_html: str) -> Optional[str]:
    # Callback loaded shortly after page load, includes lazy panels (heatmap).
    matches = re.findall(
        r'Wicket\.Ajax\.ajax\(\{"u":"([^"]*\?\d+-1\.0-&isin=[^"]+&_wicket=1)"\}\)',
        page_html,
    )
    if not matches:
        return None
    raw = html.unescape(matches[0]).replace(r"\/", "/")
    return "https://www.justetf.com" + raw


def extract_cdata_blocks(xml_text: str) -> str:
    blocks = re.findall(r"<!\[CDATA\[(.*?)\]\]>", xml_text, flags=re.S)
    return "\n".join(blocks)


def split_quoted_values(value_list: str) -> List[str]:
    values: List[str] = []
    for m in re.finditer(r"'([^']*)'|\"([^\"]*)\"", value_list):
        values.append(m.group(1) if m.group(1) is not None else m.group(2))
    return values


def parse_heatmap_from_chart_script(script_text: str) -> Optional[Dict[str, object]]:
    lowered = script_text.lower()
    has_heatmap_hint = (
        "heatmap" in lowered
        or "coloraxis" in lowered
        or "rendements mensuels" in lowered
    )
    if not has_heatmap_hint:
        return None

    x_match = re.search(r"xAxis\s*:\s*\{.*?categories\s*:\s*\[([^\]]+)\]", script_text, flags=re.S)
    y_match = re.search(r"yAxis\s*:\s*\{.*?categories\s*:\s*\[([^\]]+)\]", script_text, flags=re.S)
    if not x_match:
        x_match = re.search(r"\.xAxis\[0\]\.setCategories\(\[([^\]]+)\]\)", script_text, flags=re.S)
    if not y_match:
        y_match = re.search(r"\.yAxis\[0\]\.setCategories\(\[([^\]]+)\]\)", script_text, flags=re.S)
    if not x_match or not y_match:
        return None

    month_labels = split_quoted_values(x_match.group(1))
    year_labels = split_quoted_values(y_match.group(1))
    if not month_labels or not year_labels:
        return None

    triples = re.findall(
        r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]",
        script_text,
    )
    object_points = re.findall(
        r"\{\s*x\s*:\s*(\d+)\s*,\s*y\s*:\s*(\d+)\s*,\s*value\s*:\s*(null|-?\d+(?:\.\d+)?)\s*\}",
        script_text,
        flags=re.I,
    )
    if not triples and not object_points:
        return None

    values: List[Dict[str, object]] = []
    for x_str, y_str, v_str in triples:
        x_idx = int(x_str)
        y_idx = int(y_str)
        if x_idx >= len(month_labels) or y_idx >= len(year_labels):
            continue
        values.append(
            {
                "year": year_labels[y_idx],
                "month": month_labels[x_idx],
                "month_index": x_idx + 1,
                "return_pct": float(v_str),
            }
        )
    for x_str, y_str, v_str in object_points:
        if v_str.lower() == "null":
            continue
        x_idx = int(x_str)
        y_idx = int(y_str)
        if x_idx >= len(month_labels) or y_idx >= len(year_labels):
            continue
        values.append(
            {
                "year": year_labels[y_idx],
                "month": month_labels[x_idx],
                "month_index": x_idx + 1,
                "return_pct": float(v_str),
            }
        )
    if not values:
        return None

    return {
        "months": month_labels,
        "years": year_labels,
        "values": values,
        "source": "returnsSection:viewMode",
    }


def fetch_heatmap_data(
    client: JustETFClient,
    profile_html: str,
    isin: str,
    timeout: int,
    debug_dir: Optional[Path] = None,
) -> Optional[Dict[str, object]]:
    timer_url = extract_timer_ajax_url(profile_html)
    page_url = BASE_URL.format(isin=isin)
    if timer_url:
        for attempt in range(1, 6):
            timer_xml = client.fetch_text(
                timer_url,
                timeout=timeout,
                method="GET",
                referer=page_url,
                accept="text/xml,application/xml,text/html,*/*;q=0.01",
                extra_headers={
                    "Wicket-Ajax": "true",
                    "Wicket-Ajax-BaseURL": f"fr/etf-profile.html?isin={isin}",
                },
            )
            if debug_dir:
                (debug_dir / f"{isin}_timer_attempt_{attempt}.xml").write_text(timer_xml, encoding="utf-8")

            timer_cdata = html.unescape(extract_cdata_blocks(timer_xml))
            if debug_dir and timer_cdata:
                (debug_dir / f"{isin}_timer_attempt_{attempt}_cdata.txt").write_text(timer_cdata, encoding="utf-8")

            heatmap_from_timer = parse_heatmap_from_chart_script(timer_cdata)
            if heatmap_from_timer is not None:
                if debug_dir:
                    (debug_dir / f"{isin}_heatmap_extracted.json").write_text(
                        json.dumps(heatmap_from_timer, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                return heatmap_from_timer

            # Some responses only reschedule timer; wait and retry.
            if "Wicket.Timer.set(" in timer_cdata and attempt < 5:
                time.sleep(0.35)
                continue

    viewmode_url = extract_wicket_ajax_url(profile_html, "returnsSection-viewMode")
    if not viewmode_url:
        return None

    payload = urlencode({"returnsSection:viewMode": "CHART"}).encode("utf-8")
    xml_text = client.fetch_text(
        viewmode_url,
        timeout=timeout,
        method="POST",
        data=payload,
        referer=page_url,
        accept="text/xml,application/xml,text/html,*/*;q=0.01",
        extra_headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Wicket-Ajax": "true",
            "Wicket-Ajax-BaseURL": f"fr/etf-profile.html?isin={isin}",
        },
    )

    if debug_dir:
        (debug_dir / f"{isin}_returns_viewmode.xml").write_text(xml_text, encoding="utf-8")

    cdata_text = html.unescape(extract_cdata_blocks(xml_text))
    if debug_dir and cdata_text:
        (debug_dir / f"{isin}_returns_viewmode_cdata.txt").write_text(cdata_text, encoding="utf-8")

    heatmap = parse_heatmap_from_chart_script(cdata_text)
    if debug_dir and heatmap is not None:
        (debug_dir / f"{isin}_heatmap_extracted.json").write_text(
            json.dumps(heatmap, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return heatmap


def compute_cagr_from_heatmap(heatmap: Optional[Dict[str, object]]) -> Optional[float]:
    if not heatmap:
        return None
    raw_values = heatmap.get("values")
    if not isinstance(raw_values, list) or not raw_values:
        return None

    points: List[tuple[int, int, float]] = []
    for row in raw_values:
        if not isinstance(row, dict):
            continue
        year_txt = str(row.get("year", "")).strip()
        month_idx = row.get("month_index")
        ret = row.get("return_pct")
        if not isinstance(month_idx, int):
            continue
        if not isinstance(ret, (int, float)):
            continue
        try:
            year_int = int(year_txt)
        except ValueError:
            continue
        points.append((year_int, month_idx, float(ret)))

    if not points:
        return None

    points.sort(key=lambda x: (x[0], x[1]))
    compounded = 1.0
    for _, _, monthly_pct in points:
        compounded *= 1.0 + (monthly_pct / 100.0)

    months_count = len(points)
    if months_count <= 0 or compounded <= 0:
        return None

    cagr = math.pow(compounded, 12.0 / months_count) - 1.0
    return cagr * 100.0


def compute_cagr_from_max_return(parsed: Dict[str, object]) -> Optional[float]:
    meta = parsed.get("_meta_returns")
    if not isinstance(meta, dict):
        return None
    max_return_pct = parse_percent_text(meta.get("max_return_text"))
    launch_date = parse_french_date(meta.get("launch_date_text"))
    if max_return_pct is None or launch_date is None:
        return None

    today = dt.date.today()
    days = (today - launch_date).days
    if days <= 0:
        return None

    years = days / 365.2425
    total_growth = 1.0 + (max_return_pct / 100.0)
    if total_growth <= 0:
        return None
    cagr = math.pow(total_growth, 1.0 / years) - 1.0
    return cagr * 100.0


def load_isins(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("Le fichier JSON doit contenir une liste d'ISIN (strings).")

    isins: List[str] = []
    for item in data:
        if not isinstance(item, str):
            raise ValueError("Chaque element de la liste JSON doit etre une string (ISIN).")
        isin = item.strip().upper()
        if isin:
            isins.append(isin)
    return isins


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape justETF et ecrit un JSON par ISIN.")
    parser.add_argument("isins_json", type=Path, help="Fichier JSON contenant la liste d'ISIN.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Dossier de sortie pour les fichiers <ISIN>.json (defaut: output)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="Pause (secondes) entre les requetes (defaut: 0.8)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout HTTP en secondes (defaut: 30)",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="Dossier de debug pour sauvegarder les reponses brutes HTTP",
    )
    args = parser.parse_args()

    try:
        isins = load_isins(args.isins_json)
    except Exception as exc:
        print(f"Erreur lecture ISIN: {exc}", file=sys.stderr)
        return 1

    if not isins:
        print("Aucun ISIN a traiter.")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.debug_dir:
        args.debug_dir.mkdir(parents=True, exist_ok=True)

    errors: Dict[str, str] = {}
    client = JustETFClient()

    for idx, isin in enumerate(isins, start=1):
        url = BASE_URL.format(isin=isin)
        print(f"[{idx}/{len(isins)}] {isin} -> {url}")
        try:
            profile_html = client.fetch_text(url=url, timeout=args.timeout, method="GET")
            if args.debug_dir:
                (args.debug_dir / f"{isin}_profile.html").write_text(profile_html, encoding="utf-8")

            parsed = parse_profile_html(profile_html, isin)
            if not parsed.get("nom"):
                raise ValueError("Nom introuvable: page probablement invalide ou structure changee.")

            heatmap = fetch_heatmap_data(
                client=client,
                profile_html=profile_html,
                isin=isin,
                timeout=args.timeout,
                debug_dir=args.debug_dir,
            )
            parsed["heatmap_mensuelle"] = heatmap

            cagr_from_heatmap = compute_cagr_from_heatmap(heatmap)
            if cagr_from_heatmap is not None:
                parsed["cagr_depuis_creation_pct"] = round(cagr_from_heatmap, 6)
                parsed["cagr_depuis_creation_source"] = "heatmap_mensuelle"
            else:
                cagr_fallback = compute_cagr_from_max_return(parsed)
                if cagr_fallback is not None:
                    parsed["cagr_depuis_creation_pct"] = round(cagr_fallback, 6)
                    parsed["cagr_depuis_creation_source"] = "max_return_plus_launch_date"

            if isinstance(parsed.get("_meta_returns"), dict):
                del parsed["_meta_returns"]

            output_path = args.output_dir / f"{isin}.json"
            output_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  OK -> {output_path}")

        except HTTPError as exc:
            msg = f"HTTP {exc.code}: {exc.reason}"
            errors[isin] = msg
            print(f"  ERREUR: {msg}", file=sys.stderr)
        except URLError as exc:
            msg = f"Erreur reseau: {exc.reason}"
            errors[isin] = msg
            print(f"  ERREUR: {msg}", file=sys.stderr)
        except Exception as exc:
            errors[isin] = str(exc)
            print(f"  ERREUR: {exc}", file=sys.stderr)

        if idx < len(isins) and args.delay > 0:
            time.sleep(args.delay)

    if errors:
        errors_path = args.output_dir / "errors.json"
        errors_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nTermine avec erreurs. Details: {errors_path}")
        return 2

    print("\nTermine sans erreur.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
