# Vieša nuoroda (GitHub Pages)

## Rekomenduojama: GitHub Actions

Repozitorijoje yra darbo eiga `.github/workflows/pages-vidine-ramybe.yml`. Ji kelia **`docs/vidine-ramybe/`** turinį į Pages **šaknį** — svetainė atsivers čia:

**https://skautas.github.io/Futures-Signals-Bot/**

### Vieną kartą naršyklėje

1. Atidarykite: https://github.com/Skautas/Futures-Signals-Bot/settings/pages  
2. **Build and deployment** → **Source**: pasirinkite **GitHub Actions** (ne „Deploy from a branch“).  
3. Eikite į **Actions** ir palaukite, kol „Deploy Vidinė ramybė (Pages)“ bus žalias.  
4. Jei niekas neįsijungė — **Actions** → darbo eiga → **Run workflow**.

> Senesnis kelias **`/vidine-ramybe/`** galioja tik jei Pages šaltinis yra **`/docs` aplankas** (tada šaknis = visas `docs/`). Naudojant Actions, naudokite **šaknies** nuorodą aukščiau.

## Alternatyva: tik branch + `/docs`

Jei norite be Actions: **Source** → „Deploy from a branch“ → `main` → folder **`/docs`**.  
Tada puslapis bus: **https://skautas.github.io/Futures-Signals-Bot/vidine-ramybe/**  
(`docs/` šaknyje turi būti `vidine-ramybe/index.html` — jau yra.)

## Atnaujinimai

Keičiate `vidines_ramybes_mokykla/` → sinchronizuokite į `docs/vidine-ramybe/` ir `git push` (žr. `README.md` šaltinio aplanke).

## Kita vieta be GitHub

**Netlify Drop:** https://app.netlify.com/drop
