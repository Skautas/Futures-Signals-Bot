# Vieša nuoroda (GitHub Pages)

## Būtina tvarka (kitaip Actions krenta: „Get Pages site failed“ / Not Found)

**Pirmiausia** įjunkite Pages su Actions, **tada** leiskite darbo eigą.

1. Atidarykite: https://github.com/Skautas/Futures-Signals-Bot/settings/pages  
2. **Build and deployment** → **Source** → pasirinkite **GitHub Actions** (ne „Deploy from a branch“).  
3. Spauskite **Save**.  
4. Eikite į **Actions** → darbo eiga **Deploy Vidinė ramybė (Pages)** → **Re-run all jobs** (arba **Re-run failed jobs**).

Jei 2 žingsnyje paliksite „Deploy from a branch“ arba nieko neišsaugosite, `configure-pages` negaus Pages konfigūracijos ir matysite būtent jūsų ekrano klaidą.

## Po sėkmės

Svetainė bus čia (šaknis, be `/vidine-ramybe/`):

**https://skautas.github.io/Futures-Signals-Bot/**

## Rekomenduojama: GitHub Actions

Repozitorijoje yra darbo eiga `.github/workflows/pages-vidine-ramybe.yml`. Ji kelia **`docs/vidine-ramybe/`** turinį į Pages **šaknį**.

> Senesnis kelias **`/vidine-ramybe/`** galioja tik jei Pages šaltinis yra **`/docs` aplankas** (tada šaknis = visas `docs/`). Naudojant Actions, naudokite **šaknies** nuorodą aukščiau.

## Alternatyva: tik branch + `/docs`

Jei norite be Actions: **Source** → „Deploy from a branch“ → `main` → folder **`/docs`**.  
Tada puslapis bus: **https://skautas.github.io/Futures-Signals-Bot/vidine-ramybe/**  
(`docs/` šaknyje turi būti `vidine-ramybe/index.html` — jau yra.)

## Atnaujinimai

Keičiate `vidines_ramybes_mokykla/` → sinchronizuokite į `docs/vidine-ramybe/` ir `git push` (žr. `README.md` šaltinio aplanke).

## Kita vieta be GitHub

**Netlify Drop:** https://app.netlify.com/drop
