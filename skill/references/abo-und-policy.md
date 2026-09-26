# Abo-Nutzung: Regeln und Risiken

Das ist keine Rechtsberatung. Bei Nutzung im Unternehmen die Frage schriftlich mit dem Anbieter klären.

- **Anthropic:** Die OAuth-Anmeldung im Abo ist für die „ordinary use“ der eigenen Claude-Code-Installation gedacht. Die Consumer Terms (Pro/Max) verbieten automatisierten Zugriff außer per API-Key oder mit ausdrücklicher Erlaubnis. Team/Enterprise laufen unter den Commercial Terms.
- **OpenAI:** Für Automatisierung empfiehlt OpenAI API-Keys. ChatGPT-Login auf vertrauenswürdiger, privater Infrastruktur ist dokumentiert, aber als „advanced“ markiert. Business/Enterprise bieten Access Tokens.

Was dieser Skill deshalb bewusst so macht:
- Er nutzt die unveränderten, interaktiven CLIs in echten Terminals und extrahiert keine Tokens für fremde Clients.
- Es läuft immer nur eine Session gleichzeitig, und jeder Lauf ist vom Nutzer gestartet und beobachtbar.
- Kleine Messumfänge sind Standard. Keine Dauerläufe per cron mit Consumer-Abos.
- Ein Abo gehört einer Person. Credentials werden nie geteilt oder von Skripten gelesen.

Empfehlung: Gelegentliche Messungen mit dem eigenen Abo sind vertretbar. Für regelmäßige oder veröffentlichte Messungen einen Team/Enterprise-Zugang nutzen oder auf API-Keys mit Budget-Limit umstellen. Die Kostenrechnung dieses Skills funktioniert in beiden Fällen gleich.
