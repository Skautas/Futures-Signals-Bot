# Vidinės Ramybės Mokykla

Statinė vieno puslapio svetainė (HTML + CSS + JS), lietuvių kalba.

**Kosminis fonas:** paveikslas `images/cosmic-bg.png` — keiskite šį failą savo versija (rekomenduojama bent ~1920px pločio), kelias CSS faile: `url("images/cosmic-bg.png")`.

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

- Pridėti kontaktų skyrių, el. paštą, socialinius tinklus.
- 4 lygis: įkelti audio failus ir grotuvą (arba embed iš SoundCloud / kita).
- 5 lygis: prijungti forumą (Discourse, Circle, savas sprendimas).
- Talpinimas: Netlify, Vercel (tik statika), GitHub Pages arba jūsų serveris.

## Vieša nuoroda (GitHub Pages)

Po **push** ir įjungus **GitHub Actions** Pages (**Settings → Pages → Source: GitHub Actions**), svetainė bus šaknyje:

**https://skautas.github.io/Futures-Signals-Bot/**

(Jei naudojate tik „Deploy from branch“ su aplanku **`/docs`**, tada adresas su poaplankiu: `.../vidine-ramybe/` — žr. `docs/vidine-ramybe/VIESA_NUORODA.md`.)

Šaltinis talpinimui: aplankas `docs/vidine-ramybe/`. Sinchronizuokite po pakeitimų:

```powershell
robocopy "c:\Users\dneri\Documents\Replit futures-signals-bot\vidines_ramybes_mokykla" "c:\Users\dneri\Documents\Replit futures-signals-bot\docs\vidine-ramybe" /E /MIR
```

Dėmesio: `/MIR` ištrina failus paskirtyje, kurių nebėra šaltinyje. Jei norite tik papildyti, naudokite `/E` be `/MIR`.

Išsami instrukcija: `docs/vidine-ramybe/VIESA_NUORODA.md`.
