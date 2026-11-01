# Fishao Auto-Angler

Ein kleines Python-Skript, das das **Angel-Minispiel** im Browser-/Desktop-Spiel *Fishao* für dich
spielt. Wenn ein Fisch anbeißt, zeigt das Spiel einen farbigen Balken (Rot – Gelb – Grün – Gelb – Rot)
mit einem blauen Marker, den der Fisch immer wieder nach links oder rechts zieht. Der Spieler muss die
Pfeiltasten gedrückt halten, damit der Marker im grünen Bereich bleibt. Genau das erledigt dieses
Skript automatisch und schließt danach auch das "Fisch gefangen"-Fenster.

> **Hinweis:** Das Automatisieren eines Spiels kann gegen dessen Nutzungsbedingungen verstoßen.
> Nutzung auf eigenes Risiko und nur dort, wo es erlaubt ist. Das Skript liest nur den Bildschirm und
> drückt Tasten. Es verändert keine Spieldateien und greift nicht in den Netzwerkverkehr ein.

## Was es macht

1. **Findet den Balken selbstständig.** Es durchsucht laufend den Bildschirm (alle Monitore) nach dem
   Minispiel-Dialog. Eine Kalibrierung ist nicht nötig. Die Position des Dialogs, der Hintergrund
   (Sand, Gras, Wasser, Steinweg) und die Größe der grünen Zone spielen keine Rolle.
2. **Hält den Marker im grünen Bereich.** Solange der Balken sichtbar ist, misst es mehrmals pro
   Sekunde den blauen Marker und die Mitte der grünen Zone. Es sagt voraus, wohin der Marker läuft,
   und hält die Gegentaste gedrückt: Marker läuft nach links → **rechts** halten, Marker läuft nach
   rechts → **links** halten. Die Taste bleibt gedrückt (wie bei einem Menschen) und wird nur
   gewechselt, wenn sich die Richtung ändert.
3. **Hört auf, wenn der Balken weg ist.** Sobald der Balken verschwindet, werden alle Tasten
   losgelassen, und bis zum nächsten Biss wird nichts gesendet. Deine eigene Bewegung im Spiel wird
   also nicht beeinflusst.
4. **Schließt das "Gefangen"-Fenster.** Nach einem Fang zeigt das Spiel ein Ergebnisfenster
   ("… GEFANGEN!"). Das Skript erkennt es (unabhängig vom Fisch und seinem Namen) und drückt **Esc**,
   um es zu schließen.

## Voraussetzungen

- Windows 10 oder 11 (Tasten werden über die Windows-Schnittstelle `SendInput` gesendet)
- Python 3.9 oder neuer
- Python-Pakete: `mss`, `numpy`, `pyautogui`

```
pip install mss numpy pyautogui
```

## Benutzung

```
cd C:\fishao
python fishao_angler.py
```

1. Skript starten und das Konsolenfenster geöffnet lassen.
2. Ins Spielfenster klicken und die Angel selbst auswerfen, damit das Spielfenster beim Biss den
   Tastaturfokus hat.
3. Das Skript reagiert selbstständig auf jeden Biss. Beenden mit **Strg+C** oder indem du die Maus in
   die obere linke Bildschirmecke bewegst (pyautogui-Notbremse).

Falls `python` nicht gefunden wird, probiere `py fishao_angler.py`.

## Einstellungen

Alle Einstellungen sind Konstanten am Anfang der Datei `fishao_angler.py`.

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `MONITOR` | `0` | `0` = alle Monitore durchsuchen, `1` = nur Monitor 1, `2` = nur Monitor 2 … |
| `AUTO_CLOSE_CATCH` | `True` | "Fisch gefangen"-Fenster mit Esc schließen |
| `INVERT_KEYS` | `False` | Auf `True` setzen, wenn die Tasten in deinem Spiel in die falsche Richtung wirken |
| `KEY_MODE` | `"scancode"` | Wie Tasten gesendet werden: `"scancode"`, `"vk"` oder `"pyautogui"` (die anderen ausprobieren, falls Tasten nicht ankommen) |
| `LEAD_S` | `0.30` | Wie weit (in Sekunden) der Regler die Markerposition vorausberechnet. Höher = früheres Gegenhalten, niedriger = ruhiger |
| `KP` | `4.0` | Stärke der Reaktion auf den Abstand zur grünen Mitte |
| `STRICT` | `True` | Strenge Prüfung, ob ein gefundener Balken wirklich der Minispiel-Dialog ist |
| `LOG` | `True` | `fishao_log.csv` schreiben (Marker, Zonenmitte, Abweichung, gedrückte Taste) |
| `DIAG` | `True` | Diagnose beim Suchen ausgeben und `debug_beste_zeile.png` speichern |

## Dateien, die das Skript schreibt

- `debug_screen.png` – Screenshot beim Start, um zu prüfen, welcher Bereich erfasst wird
- `debug_beste_zeile.png` – bisher bestes Suchbild (nur mit `DIAG = True`)
- `fishao_log.csv` – Protokoll des letzten Minispiels pro Bild (mit `LOG = False` abschalten)

## Fehlersuche

- **Balken wird nicht gefunden:** Prüfe in `debug_screen.png`, ob das Spiel auf dem erfassten Monitor
  sichtbar ist.
- **Es wird nur eine Taste gedrückt / der Marker läuft weg:** Wahrscheinlich hat das Spielfenster
  nicht den Fokus. Klicke zuerst ins Spiel. Wenn es trotzdem passiert, setze `INVERT_KEYS = True`
  oder ändere `KEY_MODE`.
- **Marker schwingt zu stark:** `LEAD_S` erhöhen (z. B. `0.4`). **Zu nervös:** senken (z. B. `0.2`).
- **Fang-Fenster schließt sich nicht:** Das Skript versucht es höchstens 4-mal pro Fenster. Stelle
  sicher, dass das Spiel den Fokus hat. Sieht das Fenster anders aus als das Standardfenster (zum
  Beispiel ohne den grünen Button "ZEIGE FISCHBUCH"), wird es nicht erkannt.

## So funktioniert die Erkennung (kurz)

Alles beruht auf Farben und Formen, nicht auf dem Lesen von Text. Die Spielsprache sollte also keine
Rolle spielen (getestet wurde nur die deutsche Version):

- **Balken:** ein waagerechter Streifen mit roten Rändern und einem glatten grünen Maximum in der
  Mitte, innerhalb des beigen Dialogs, mit zwei blauen Marker-Dreiecken.
- **Marker:** die blauen Pixel im Balken. Ihre mittlere x-Position ist die Markerposition.
- **Grüne Zone:** der stärkste "Grün minus Rot"-Bereich des Balkens. Seine Mitte ist das Ziel.
- **Fang-Fenster:** der grüne Button "ZEIGE FISCHBUCH", das beige Fenster darum herum und das gelbe
  Längenlineal daneben.

## Einschränkungen

- Nur mit dem deutschen Client bei normaler Oberflächengröße getestet. Eine stark abweichende
  Skalierung oder ein stark veränderter Farbmodus kann die Erkennung stören.
- Das Skript wirft die Angel nicht aus, kauft keinen Köder und kann keinen Handel ausüben. Du
  startest jede Angelrunde selbst.
