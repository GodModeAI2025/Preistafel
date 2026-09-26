# Preise und Regionen

## Automatische Quelle

`scripts/prices.py` lädt `model_prices_and_context_window.json` aus dem LiteLLM-Repository und pinnt den Commit-SHA. Regionale Einträge in dieser Datei:
- Anthropic über Bedrock: `eu.anthropic.<modell>`, `us.anthropic.<modell>`, `global.anthropic.<modell>`. Regional kostet 1,1× global.
- OpenAI: `regional_processing_uplift_multiplier_eu` bzw. `_us` im Modelleintrag (10 % für berechtigte Modelle ab März 2026)
- Azure OpenAI: `azure/eu/<modell>`, `azure/us/<modell>`

Welche Quelle je Region gilt, steht in `config/regions.toml`. Die Anthropic-API selbst kennt nur `inference_geo` = `global` oder `us` (1,1× ab Claude 4.6). Wer EU-Verarbeitung für Claude braucht, geht über Bedrock, Vertex oder Foundry. Die Region `eu` nutzt deshalb die Bedrock-EU-Preise.

Die Währung wird über den EZB-Referenzkurs (eurofxref-daily.xml) umgerechnet. Offline geht es mit `--fx <kurs>`.

## Offizielle Abgleichquellen (stichprobenartig mit web_fetch prüfen)

- Anthropic: https://platform.claude.com/docs/en/about-claude/pricing und https://platform.claude.com/docs/en/manage-claude/data-residency
- OpenAI: https://developers.openai.com/api/docs/pricing
- Azure OpenAI: https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/
- Bedrock: https://aws.amazon.com/bedrock/pricing/

Weicht ein Wert um mehr als 2 % ab, gilt der offizielle Wert. Trag ihn in `config/price_overrides.toml` ein, mit Quelle und Datum.

## Bekannte Grenzen

- Die LiteLLM-Datei ist community-gepflegt und kann bei neuen Modellen einige Tage hinterherhinken. Fehlt ein Modell, meldet `prices.py` das unter `missing`.
- Vertragsrabatte, Steuern und Batch-Rabatte werden nicht berücksichtigt. Gerechnet wird mit Listenpreisen.
- Die LiteLLM-PyPI-Versionen 1.82.7 und 1.82.8 waren im März 2026 kompromittiert. Dieser Skill lädt nur die JSON-Datei per HTTPS und installiert kein LiteLLM-Paket.
