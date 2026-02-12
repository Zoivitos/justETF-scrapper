#!/usr/bin/env python3
import argparse
import json
import re
import sys
import time
import http.cookiejar
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, build_opener, HTTPCookieProcessor

BASE_HOST = "https://www.justetf.com"
SEARCH_URL_TEMPLATE = BASE_HOST + "/fr/search.html?query={query}&search=ETFS"


def clean_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def load_tickers(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("Le fichier JSON doit contenir une liste de tickers (strings).")

    tickers: List[str] = []
    for item in data:
        if not isinstance(item, str):
            raise ValueError("Chaque element de la liste JSON doit etre une string (ticker).")
        ticker = item.strip().upper()
        if ticker:
            tickers.append(ticker)
    return tickers


class JustETFHttpClient:
    def __init__(self) -> None:
        jar = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(jar))

    def fetch_text(
        self,
        url: str,
        method: str = "GET",
        data: Optional[bytes] = None,
        timeout: int = 30,
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
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)

        req = Request(url, data=data, method=method, headers=headers)
        with self.opener.open(req, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")


def extract_fetch_callback_url(search_html: str) -> Optional[str]:
    # Variant seen in search=ALL pages.
    match = re.search(r'"fetchCallbackUrl":"([^"]+)"', search_html)
    if match:
        raw = match.group(1).replace(r"\/", "/")
        raw = unescape(raw)
        return urljoin(BASE_HOST, raw)

    # Variant seen in search=ETFS pages.
    match = re.search(r"var\s+fetchCallbackUrl\s*=\s*'([^']+)'", search_html)
    if match:
        raw = unescape(match.group(1))
        return urljoin(BASE_HOST, raw)

    return None


def extract_quick_search_callback_url(search_html: str) -> Optional[str]:
    urls = re.findall(
        r'(/fr/search\.html\?[^"\']*mainSearchPanel-searchForm-query[^"\']*_wicket=1)',
        search_html,
    )
    if not urls:
        return None

    # Prefer the primary callback (-1.0-) which returns the quick-search payload.
    for url in urls:
        if "-1.0-" in url:
            return urljoin(BASE_HOST, unescape(url))
    return urljoin(BASE_HOST, unescape(urls[0]))


def strip_tags(html_fragment: str) -> str:
    return clean_text(unescape(re.sub(r"<[^>]+>", " ", html_fragment)))


def extract_cdata_html(xml_text: str) -> str:
    # Wicket AJAX response wraps rendered HTML in CDATA sections.
    blocks = re.findall(r"<!\[CDATA\[(.*?)\]\]>", xml_text, flags=re.S)
    if not blocks:
        return ""
    return "\n".join(blocks)


def parse_results_from_quicksearch_html(html: str, input_ticker: str) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen: set[str] = set()

    row_pattern = re.compile(
        r'<tr[^>]*data-testid="quick-search-result-etf-([A-Z0-9]{12})"[^>]*>(.*?)</tr>',
        flags=re.I | re.S,
    )
    for match in row_pattern.finditer(html):
        isin = match.group(1).upper()
        row_html = match.group(2)
        if isin in seen:
            continue
        seen.add(isin)

        name_match = re.search(r'data-target-kind="result-link">([^<]+)</span>', row_html, flags=re.I)
        name = clean_text(unescape(name_match.group(1))) if name_match else ""
        if not name:
            td_blocks = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)
            if td_blocks:
                name = strip_tags(td_blocks[0])

        td_texts = [strip_tags(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)]
        row_ticker = input_ticker
        blocked_values = {
            "ETF",
            "ETFS",
            "ACTION",
            "ACTIONS",
            "OBLIGATION",
            "OBLIGATIONS",
            "MATIERES PREMIERES",
            "MATIERES PREMIERES",
        }
        for text in reversed(td_texts):
            if not text or text == isin:
                continue
            normalized = text.upper()
            if normalized in blocked_values:
                continue
            if re.fullmatch(r"[A-Z0-9.\-]{1,12}", normalized):
                row_ticker = text
                break

        results.append(
            {
                "tickers": row_ticker or input_ticker,
                "isin": isin,
                "nom_complet": name,
            }
        )
    return results


def build_datatables_payload(start: int, length: int, draw: int) -> Dict[str, str]:
    # Payload compatible DataTables server-side. Some backends need these keys.
    return {
        "draw": str(draw),
        "start": str(start),
        "length": str(length),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": "3",
        "order[0][dir]": "desc",
        "columns[0][data]": "",
        "columns[0][name]": "selectCheckbox",
        "columns[0][searchable]": "false",
        "columns[0][orderable]": "false",
        "columns[0][search][value]": "",
        "columns[0][search][regex]": "false",
        "columns[1][data]": "name",
        "columns[1][name]": "name",
        "columns[1][searchable]": "true",
        "columns[1][orderable]": "true",
        "columns[1][search][value]": "",
        "columns[1][search][regex]": "false",
        "columns[2][data]": "",
        "columns[2][name]": "sparkline",
        "columns[2][searchable]": "false",
        "columns[2][orderable]": "false",
        "columns[2][search][value]": "",
        "columns[2][search][regex]": "false",
        "columns[3][data]": "oneYearReturn",
        "columns[3][name]": "oneYearReturn",
        "columns[3][searchable]": "true",
        "columns[3][orderable]": "true",
        "columns[3][search][value]": "",
        "columns[3][search][regex]": "false",
        "columns[4][data]": "fundSize",
        "columns[4][name]": "fundSize",
        "columns[4][searchable]": "true",
        "columns[4][orderable]": "true",
        "columns[4][search][value]": "",
        "columns[4][search][regex]": "false",
        "columns[5][data]": "ter",
        "columns[5][name]": "ter",
        "columns[5][searchable]": "true",
        "columns[5][orderable]": "true",
        "columns[5][search][value]": "",
        "columns[5][search][regex]": "false",
        "columns[6][data]": "distributionPolicy",
        "columns[6][name]": "distributionPolicy",
        "columns[6][searchable]": "true",
        "columns[6][orderable]": "true",
        "columns[6][search][value]": "",
        "columns[6][search][regex]": "false",
        "columns[7][data]": "provider",
        "columns[7][name]": "provider",
        "columns[7][searchable]": "true",
        "columns[7][orderable]": "true",
        "columns[7][search][value]": "",
        "columns[7][search][regex]": "false",
        "columns[8][data]": "isin",
        "columns[8][name]": "isin",
        "columns[8][searchable]": "true",
        "columns[8][orderable]": "true",
        "columns[8][search][value]": "",
        "columns[8][search][regex]": "false",
        "columns[9][data]": "ticker",
        "columns[9][name]": "ticker",
        "columns[9][searchable]": "true",
        "columns[9][orderable]": "true",
        "columns[9][search][value]": "",
        "columns[9][search][regex]": "false",
    }


def parse_json_response(raw_text: str) -> Dict:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    # Some endpoints return extra wrappers; try to recover JSON object.
    first = raw_text.find("{")
    last = raw_text.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidate = raw_text[first : last + 1]
        return json.loads(candidate)

    raise ValueError(f"Reponse non JSON. Debut: {raw_text[:200]!r}")


def parse_results_from_html_fallback(html: str, ticker: str) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen: set[str] = set()

    pattern = re.compile(
        r'href="[^"]*etf-profile\.html\?isin=([A-Z0-9]{12})"[^>]*>([^<]+)</a>',
        re.IGNORECASE,
    )
    for m in pattern.finditer(html):
        isin = m.group(1).upper()
        name = clean_text(unescape(m.group(2)))
        if isin in seen:
            continue
        seen.add(isin)
        results.append({"tickers": ticker, "isin": isin, "nom_complet": name})
    return results


def fetch_result_page(
    client: JustETFHttpClient,
    fetch_callback_url: str,
    referer: str,
    start: int,
    length: int,
    draw: int,
    timeout: int,
) -> tuple[Dict, str]:
    payload = build_datatables_payload(start=start, length=length, draw=draw)
    body = urlencode(payload).encode("utf-8")
    text = client.fetch_text(
        fetch_callback_url,
        method="POST",
        data=body,
        timeout=timeout,
        referer=referer,
        accept="application/json, text/plain, */*",
    )
    parsed = parse_json_response(text)
    return parsed, text


def discover_for_ticker(
    ticker: str,
    timeout: int,
    page_size: int,
    max_pages: int,
    debug_dir: Optional[Path] = None,
) -> List[Dict[str, str]]:
    client = JustETFHttpClient()
    search_url = SEARCH_URL_TEMPLATE.format(query=ticker)
    search_html = client.fetch_text(search_url, method="GET", timeout=timeout, referer=BASE_HOST)
    if debug_dir:
        (debug_dir / f"{ticker}_search.html").write_text(search_html, encoding="utf-8")

    quick_callback_url = extract_quick_search_callback_url(search_html)
    if quick_callback_url:
        quick_payload = urlencode({"query": ticker}).encode("utf-8")
        quick_response = client.fetch_text(
            quick_callback_url,
            method="POST",
            data=quick_payload,
            timeout=timeout,
            referer=search_url,
            accept="text/xml, application/xml, text/html, */*;q=0.01",
            extra_headers={
                "Wicket-Ajax": "true",
                "Wicket-Ajax-BaseURL": f"fr/search.html?query={ticker}&search=ETFS",
            },
        )
        if debug_dir:
            (debug_dir / f"{ticker}_quicksearch.xml").write_text(quick_response, encoding="utf-8")

        quick_html = extract_cdata_html(quick_response)
        if debug_dir and quick_html:
            (debug_dir / f"{ticker}_quicksearch.html").write_text(quick_html, encoding="utf-8")

        quick_results = parse_results_from_quicksearch_html(quick_html, input_ticker=ticker)
        if quick_results:
            return quick_results

    fetch_callback_url = extract_fetch_callback_url(search_html)
    if not fetch_callback_url:
        fallback = parse_results_from_html_fallback(search_html, ticker=ticker)
        if fallback:
            return fallback
        raise ValueError("Impossible de trouver fetchCallbackUrl dans la page de recherche.")

    discovered: List[Dict[str, str]] = []
    seen_isins: set[str] = set()

    for page_idx in range(max_pages):
        start = page_idx * page_size
        draw = page_idx + 1

        try:
            response, raw_response = fetch_result_page(
                client=client,
                fetch_callback_url=fetch_callback_url,
                referer=search_url,
                start=start,
                length=page_size,
                draw=draw,
                timeout=timeout,
            )
            if debug_dir:
                (debug_dir / f"{ticker}_api_page_{page_idx + 1}.txt").write_text(raw_response, encoding="utf-8")
        except Exception:
            # Fallback if callback returns HTML/XML.
            fallback = parse_results_from_html_fallback(search_html, ticker=ticker)
            if fallback:
                return fallback
            raise

        rows = response.get("data")
        if not isinstance(rows, list) or not rows:
            break

        for row in rows:
            if not isinstance(row, dict):
                continue

            isin = clean_text(str(row.get("isin", ""))).upper()
            name = clean_text(str(row.get("name", "")))
            row_ticker = clean_text(str(row.get("ticker", ""))).upper()

            if not isin:
                continue
            if isin in seen_isins:
                continue
            seen_isins.add(isin)

            discovered.append(
                {
                    "tickers": row_ticker or ticker,
                    "isin": isin,
                    "nom_complet": name,
                }
            )

        if len(rows) < page_size:
            break

    return discovered


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decouvre les ISIN depuis une liste de tickers justETF."
    )
    parser.add_argument("tickers_json", type=Path, help="Fichier JSON contenant la liste de tickers.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ticker_isin_discovery.json"),
        help="Fichier JSON de sortie (defaut: ticker_isin_discovery.json)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Pause en secondes entre chaque ticker (defaut: 1.0)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout HTTP en secondes (defaut: 30)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="Nombre de lignes demandees par page API (defaut: 50)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Nombre max de pages API par ticker (defaut: 20)",
    )
    parser.add_argument(
        "--errors-output",
        type=Path,
        default=Path("ticker_isin_discovery_errors.json"),
        help="Fichier JSON de sortie des erreurs (defaut: ticker_isin_discovery_errors.json)",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="Dossier de debug pour sauvegarder les reponses brutes HTTP",
    )
    args = parser.parse_args()

    try:
        tickers = load_tickers(args.tickers_json)
    except Exception as exc:
        print(f"Erreur lecture tickers: {exc}", file=sys.stderr)
        return 1

    if not tickers:
        print("Aucun ticker a traiter.")
        return 0

    all_results: List[Dict[str, str]] = []
    errors: Dict[str, str] = {}
    if args.debug_dir:
        args.debug_dir.mkdir(parents=True, exist_ok=True)

    for idx, ticker in enumerate(tickers, start=1):
        print(f"[{idx}/{len(tickers)}] {ticker}")
        try:
            rows = discover_for_ticker(
                ticker=ticker,
                timeout=args.timeout,
                page_size=args.page_size,
                max_pages=args.max_pages,
                debug_dir=args.debug_dir,
            )
            all_results.extend(rows)
            print(f"  OK -> {len(rows)} resultat(s)")
        except HTTPError as exc:
            msg = f"HTTP {exc.code}: {exc.reason}"
            errors[ticker] = msg
            print(f"  ERREUR: {msg}", file=sys.stderr)
        except URLError as exc:
            msg = f"Erreur reseau: {exc.reason}"
            errors[ticker] = msg
            print(f"  ERREUR: {msg}", file=sys.stderr)
        except Exception as exc:
            msg = str(exc)
            errors[ticker] = msg
            print(f"  ERREUR: {msg}", file=sys.stderr)

        if idx < len(tickers) and args.delay > 0:
            time.sleep(args.delay)

    args.output.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResultats ecrits dans: {args.output}")

    if errors:
        args.errors_output.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Erreurs ecrites dans: {args.errors_output}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
