#!/usr/bin/env python3
"""Regionale Tokenpreise automatisch holen.

Quelle: LiteLLM model_prices_and_context_window.json (maschinenlesbar, versioniert
über Commit-SHA). Die Region aus config/regions.toml bestimmt, welcher Preiseintrag
gilt und welcher Aufschlag angewendet wird. Umrechnung in die Regionswährung über
den EZB-Referenzkurs.

Ausgabe: prices/<YYYY-MM-DD>_<region>.json mit Preisen je 1 Mio Tokens in USD
und in Regionswährung.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRICE_URL = "https://raw.githubusercontent.com/BerriAI/litellm/{ref}/model_prices_and_context_window.json"
COMMIT_API = "https://api.github.com/repos/BerriAI/litellm/commits?path=model_prices_and_context_window.json&per_page=1"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

FIELDS = {  # unser Name -> LiteLLM-Feld (USD pro Token)
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
    "cache_write_5m": "cache_creation_input_token_cost",
    "cache_write_1h": "cache_creation_input_token_cost_above_1hr",
}


def http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "herdr-bench/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def provider_of(model: str) -> str:
    return "anthropic" if model.startswith("claude") else "openai"


def resolve(entry_db: dict, spec: dict, model: str):
    """Liefert (schluessel, eintrag, multiplikator, hinweis) oder None."""
    for keys_name, mult_name in (("keys", "multiplier"), ("fallback_keys", "fallback_multiplier")):
        for pattern in spec.get(keys_name, []):
            key = pattern.format(model=model)
            entry = entry_db.get(key)
            if not entry:
                continue
            mult = spec.get(mult_name, 1.0)
            if isinstance(mult, str) and mult.startswith("field:"):
                field = mult.split(":", 1)[1]
                if field not in entry:
                    continue  # Modell nicht regional verfügbar -> nächster Kandidat
                mult = float(entry[field])
            note = spec.get("note", "") + (" (Fallback)" if keys_name == "fallback_keys" else "")
            return key, entry, float(mult), note
    return None


def fx_rate(currency: str, override: float | None) -> tuple[float, str]:
    """USD -> Zielwährung. Rückgabe (faktor, quelle)."""
    if currency == "USD":
        return 1.0, "identity"
    if override:
        return override, "manual --fx"
    xml = http_get(ECB_URL).decode()
    rates = dict(re.findall(r"currency='([A-Z]{3})' rate='([\d.]+)'", xml))
    date = re.search(r"time='([\d-]+)'", xml)
    usd_per_eur = float(rates["USD"])
    if currency == "EUR":
        return 1 / usd_per_eur, f"EZB {date.group(1) if date else ''}"
    return float(rates[currency]) / usd_per_eur, f"EZB {date.group(1) if date else ''}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="eu")
    ap.add_argument("--models", help="kommagetrennt; Standard: alle aus config/matrix.toml")
    ap.add_argument("--ref", default="main", help="LiteLLM-Git-Ref (Commit-SHA für Reproduzierbarkeit)")
    ap.add_argument("--fx", type=float, help="USD->Regionswährung manuell setzen")
    ap.add_argument("--source-file", help="lokale Preisdatei statt Download (Tests/Offline)")
    ap.add_argument("--out", help="Zieldatei")
    a = ap.parse_args()

    regions = tomllib.loads((ROOT / "config/regions.toml").read_text())
    if a.region not in regions:
        print(f"Region '{a.region}' fehlt in config/regions.toml. Verfügbar: {', '.join(regions)}", file=sys.stderr)
        return 2
    region = regions[a.region]
    matrix = tomllib.loads((ROOT / "config/matrix.toml").read_text())
    models = a.models.split(",") if a.models else sorted({c["model"] for c in matrix["config"]})
    overrides = tomllib.loads((ROOT / "config/price_overrides.toml").read_text())

    commit = None
    if a.source_file:
        db = json.loads(Path(a.source_file).read_text())
        source = f"file:{a.source_file}"
    else:
        ref = a.ref
        if ref == "main":
            try:
                commit = json.loads(http_get(COMMIT_API))[0]["sha"]
                ref = commit
            except Exception as e:  # GitHub-API optional
                print(f"Hinweis: Commit-SHA nicht ermittelbar ({e}); nutze 'main'.", file=sys.stderr)
        db = json.loads(http_get(PRICE_URL.format(ref=ref)))
        source = PRICE_URL.format(ref=ref)

    fx, fx_source = fx_rate(region["currency"], a.fx)
    out_models, missing = {}, []
    for m in models:
        spec = region[provider_of(m)]
        hit = resolve(db, spec, m)
        if not hit:
            missing.append(m)
            continue
        key, entry, mult, note = hit
        usd = {}
        for ours, theirs in FIELDS.items():
            v = entry.get(theirs)
            usd[ours] = None if v is None else round(float(v) * 1e6 * mult, 6)
        # Überschreibungen (offizielle Werte, vor Regionsaufschlag)
        ov = overrides.get(m)
        if ov:
            for f in FIELDS:
                if f in ov:
                    usd[f] = round(float(ov[f]) * mult, 6)
        # 1h-Cache fehlt? Anthropic-Regel: 2x Input
        if usd["cache_write_1h"] is None and provider_of(m) == "anthropic" and usd["input"]:
            usd["cache_write_1h"] = round(usd["input"] * 2, 6)
        local = {k: (None if v is None else round(v * fx, 6)) for k, v in usd.items()}
        out_models[m] = {
            "price_key": key, "multiplier": mult, "note": note,
            "override": bool(ov), "override_source": ov.get("source") if ov else None,
            "usd_per_mtok": usd, "local_per_mtok": local,
        }

    today = dt.date.today().isoformat()
    result = {
        "region": a.region, "currency": region["currency"], "fx_usd_to_local": fx, "fx_source": fx_source,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": source, "source_commit": commit, "models": out_models, "missing": missing,
    }
    out = Path(a.out) if a.out else ROOT / "prices" / f"{today}_{a.region}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"{out}  ({len(out_models)} Modelle, Währung {region['currency']}, Kurs {fx:.4f} [{fx_source}])")
    for m, v in out_models.items():
        p = v["local_per_mtok"]
        print(f"  {m:24s} in {p['input']}  out {p['output']}  cache_r {p['cache_read']}  ×{v['multiplier']}  [{v['price_key']}]")
    if missing:
        print(f"FEHLT (keine Kosten berechenbar): {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
