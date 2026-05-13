<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Font Fallback Chains

The fallback chain is not a design preference — it is a promise that the artifact renders legibly on any machine. A chain that breaks (e.g. a machine lacking Plex and Inter falling through to Times New Roman) produces an off-system artifact. These chains are vetted to fall through to shapes that remain legible and governance-appropriate.

---

## Decks (`.pptx`)

| Role         | Chain                                   | Notes |
|--------------|-----------------------------------------|-------|
| Latin body   | `Calibri`                               | Universal on Windows ≥ Vista and Mac Office. Do not introduce alternates into deck scripts. |
| Latin mono   | `Consolas`                              | Universal on Windows; Mac falls back to Menlo cleanly. |
| Arabic body  | `Calibri`                               | Calibri's Arabic coverage is standard on any Office install. |

Why no Plex in decks? Embedding Plex into a `.pptx` increases file size and risks font substitution on a recipient without the embedded font enabled. Calibri is strictly typographic compromise in exchange for zero-risk portability. The precision comes from rhythm and weight, not the face itself.

---

## Static artifacts (PDF, PNG, HTML)

| Role           | Chain                                                                              |
|----------------|------------------------------------------------------------------------------------|
| Display (Latin)| `"IBM Plex Serif", Georgia, "Times New Roman", serif`                              |
| Body   (Latin) | `"IBM Plex Sans", Inter, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif`  |
| Body   (Arabic)| `"IBM Plex Sans Arabic", "Noto Sans Arabic", "Geeza Pro", "Al Nile", Calibri, sans-serif` |
| Body (Hebrew)  | `"IBM Plex Sans", "Noto Sans Hebrew", "David CLM", Arial, sans-serif`              |
| Body (Persian) | `"IBM Plex Sans Arabic", "Vazirmatn", "Noto Sans Arabic", Tahoma, sans-serif`      |
| Body (Urdu)    | `"Noto Nastaliq Urdu", "Jameel Noori Nastaleeq", "IBM Plex Sans Arabic", serif`    |
| Mono           | `"IBM Plex Mono", "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace`         |

Static artifacts render in a controlled environment (headless Chromium for HTML→PDF, or python-docx with explicit font names). The rendering machine installs the Plex family once; the fallbacks exist only to keep open HTML previews legible on dev machines that lack Plex.

---

## Licences

All fonts listed above are OFL-licensed (SIL Open Font License 1.1), freely redistributable, and ship with every SignalOS artifact that embeds them. No paid font licences are required for the v1.0 system.

- IBM Plex: https://github.com/IBM/plex (OFL 1.1)
- Inter: https://rsms.me/inter/ (OFL 1.1)
- Noto: https://fonts.google.com/noto (OFL 1.1)
- Vazirmatn: https://github.com/rastikerdar/vazirmatn (OFL 1.1)

---

## Installing on a build machine

```bash
# Ubuntu / Debian
sudo apt-get install -y fonts-ibm-plex fonts-noto-core fonts-noto-cjk fonts-inter \
     fonts-hosny-amiri fonts-vlgothic
fc-cache -fv

# Mac (via Homebrew)
brew tap homebrew/cask-fonts
brew install --cask font-ibm-plex font-inter font-noto-sans font-noto-sans-arabic

# Verify
fc-list | grep -i "IBM Plex"
fc-list :lang=ar | head
```

---

## Do not

- Do not replace Plex in the static stack with Helvetica Neue, Lato, Poppins, Nunito, Montserrat, or any other popular Google Font. These were considered and rejected.
- Do not substitute Calibri in decks with any other universal font (no Arial, no Segoe UI). Calibri is the deck's one legal face.
- Do not ship a decorative/display face (Playfair, Bebas Neue, etc.) in any SignalOS artifact. The governance tone requires restraint.
- Do not mix serif and sans-serif within a single running paragraph. Serif is reserved for display titles on static artifacts.
