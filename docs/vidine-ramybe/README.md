# Vidinės Ramybės Mokykla

Statinė vieno puslapio svetainė (HTML + CSS + JS), lietuvių kalba.

**Kosminis fonas:** paveikslas `images/cosmic-bg.png` — keiskite šį failą savo versija (rekomenduojama bent ~1920px pločio), kelias CSS faile: `url("images/cosmic-bg.png")`.

**Meditacijos:** MP3 kataloge `audio/` — šeši anglų kalbos įrašai iš [UCLA Mindful](https://www.uclahealth.org/uclamindful/guided-meditations) (CC BY-NC-ND; žr. `audio/ATTRIBUTION.txt`). Ilgos praktikos **lietuviškas tekstas** — `meditacijos/ilgasis-atsipalaidavimas.html` (garsas ir tekstas nesutampa žodis į žodį). Instrukcijos — `audio/README.md`.

## Peržiūra kompiuteryje

Iš šio katalogo (PowerShell — naudokite `;`, ne `&&`):

```powershell
Set-Location "c:\Users\dneri\Documents\Replit futures-signals-bot\vidines_ramybes_mokykla"
python -m http.server 8080
```

Arba viena eilute:

```powershell
Set-Location "c:\Users\dneri\Documents\Replit futures-signals-bot\vidines_ramybes_mokykla"; python -m http.server 8080
```

Naršyklėje: `http://localhost:8080`

Arba tiesiog atidarykite `index.html` failą (navigacija veiks; kai kuriuose naršyklių režimuose gali būti apribojimų dėl `file://`).

## Tolimesni žingsniai

- QR puslapis: `qr-vidine-ramybe.html` (žr. `QR_PROGRAMA.md`).
- Pridėti kontaktų skyrių, el. paštą, socialinius tinklus.
- 4 lygis: papildomi įrašai — laikytis `audio/ATTRIBUTION.txt` licencijų.
- Talpinimas: Netlify, Vercel (tik statika), GitHub Pages arba jūsų serveris.

## Vieša nuoroda (GitHub Pages)

Po **push** darbo eiga įkelia svetainę į šaką **`gh-pages`**. Tada **vieną kartą** nustatykite Pages: **Settings → Pages → Deploy from a branch → `gh-pages` → / (root)** — žr. `docs/vidine-ramybe/VIESA_NUORODA.md`.

**https://skautas.github.io/Futures-Signals-Bot/**

Šaltinis talpinimui: aplankas `docs/vidine-ramybe/`. Sinchronizuokite po pakeitimų:

```powershell
robocopy "c:\Users\dneri\Documents\Replit futures-signals-bot\vidines_ramybes_mokykla" "c:\Users\dneri\Documents\Replit futures-signals-bot\docs\vidine-ramybe" /E /MIR
```

Dėmesio: `/MIR` ištrina failus paskirtyje, kurių nebėra šaltinyje. Jei norite tik papildyti, naudokite `/E` be `/MIR`.

Išsami instrukcija: `docs/vidine-ramybe/VIESA_NUORODA.md`.
